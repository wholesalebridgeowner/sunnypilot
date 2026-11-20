#!/usr/bin/env python3
"""One-shot, private, read-only pinned R-log ACC/lane comfort audit for Railway.

No public HTTP endpoint, device commands, raw CAN output, route identifiers,
GPS, VIN, absolute timestamps, secrets, or per-sample records in stdout.
The vehicle's factory ACC actuation is NOT decoded by this tool.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ramtools.acc_motion_integrity import analyze as analyze_motion
from ramtools.comfort_episodes import evaluate as analyze_episodes
from ramtools.lane_phase_quality import analyze as analyze_lanes
from ramtools.logio import load_records
from ramtools.schema import load_log_schema, verify_schema_dir
from ramtools.recorded_source_integrity import require_exact_recorded_commits

PINNED_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
KEY_PATTERN = re.compile(r"^logs/([^/]+)/([0-9]+)/rlog\.zst$")
ROUTE_PATTERN = re.compile(r"^[0-9a-f]{8}--[0-9a-f]{10}$")
MAX_COMPRESSED_BYTES = 30_000_000
# A bounded retained morning window (27..46) can be audited as 20 partial-route
# R-logs; this does not imply the missing early segments have been recovered.
MAX_SEGMENTS = 20
# Only these fixed, non-identifying stage labels can appear in a failure log.
CURRENT_STAGE = "startup"


def stage(name: str) -> None:
    global CURRENT_STAGE
    if name not in ("configuration", "schema", "inventory", "download", "decode", "summary"):
        raise ValueError("invalid private audit stage")
    CURRENT_STAGE = name


def parse_segments(value: str) -> tuple[int, ...]:
    # At most twenty 0..1000 selectors, no unbounded input or silent truncation.
    if type(value) is not str or len(value) > 99 or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", value):
        raise ValueError("invalid segment selection")
    numbers = tuple(int(s) for s in value.split(","))
    if not numbers or len(numbers) > MAX_SEGMENTS or len(set(numbers)) != len(numbers):
        raise ValueError("invalid segment selection")
    if any(x > 1000 for x in numbers):
        raise ValueError("segment out of bounds")
    return numbers


def select_keys(objects, *, route_prefix: str, segments: tuple[int, ...]) -> list[str]:
    """Select one unambiguous completed route; never return private key in errors."""
    if not re.fullmatch(r"[0-9a-f]{8}", route_prefix):
        raise ValueError("invalid route selector")
    if not segments or len(set(segments)) != len(segments) or len(segments) > MAX_SEGMENTS:
        raise ValueError("invalid segment selector")
    found: dict[str, dict[int, str]] = {}
    for obj in objects:
        m = KEY_PATTERN.fullmatch(obj.get("Key", ""))
        if m is None:
            continue
        route = m.group(1).split("|")[-1]
        if not ROUTE_PATTERN.fullmatch(route) or route[:8] != route_prefix:
            continue
        seg = int(m.group(2))
        if seg not in segments:
            continue
        length = obj.get("Size")
        if type(length) is not int or not 0 < length <= MAX_COMPRESSED_BYTES:
            raise ValueError("invalid compressed object size")
        route_keys = found.setdefault(route, {})
        if seg in route_keys:
            raise ValueError("duplicate segment")
        route_keys[seg] = obj["Key"]
    if len(found) != 1:
        raise ValueError("missing or ambiguous route")
    chosen = next(iter(found.values()))
    if set(chosen) != set(segments):
        raise ValueError("incomplete segment coverage")
    return [chosen[n] for n in segments]


def pinned_recorded_build(records: list[dict]) -> dict:
    """A matching decoder schema does not establish an identical dirty runtime."""
    commits = [r.get("gitCommit") for r in records
               if r.get("service") == "initData"]
    matches = bool(commits) and all(type(v) is str and v == PINNED_COMMIT
                                    for v in commits)
    return {
        "initDataCommitObservations": len(commits),
        "recordedCommitMatchedPinned": matches,
        "sourceWorkingTreeStillUnverified": True,
    }


def require_comfort_segment_source_pins(records: list[dict], read_results) -> dict:
    """Fail closed unless every selected R-log has its own full source header.

    A valid header in one segment cannot silently cover another segment with
    absent or shorter recorded-build metadata. Only safe counts are returned.
    """
    if not read_results:
        raise ValueError("missing selected R-log provenance")
    segments = []
    for file in read_results:
        segment = getattr(file, "segment", None)
        if type(segment) is not int or segment < 0 or segment in segments:
            raise ValueError("invalid or duplicate selected segment provenance")
        segments.append(segment)
    observed = {seg: [] for seg in segments}
    for rec in records:
        if rec.get("service") != "initData":
            continue
        seg = rec.get("segment")
        if type(seg) is not int or seg not in observed:
            raise ValueError("unscoped recorded source metadata")
        observed[seg].append(rec.get("gitCommit"))
    verified = sum(require_exact_recorded_commits(values, PINNED_COMMIT)
                   for values in observed.values())
    return {"sourceVerifiedSegments": len(segments),
            "sourceVerifiedHeaderCount": verified}


def public_lane_summary(lanes: dict) -> dict:
    """Bound all exported keys, including enum-like values from model records."""
    phases = {}
    count_fields = (
        "completedTransitions", "invalidDuration", "insufficientCarState",
        "invalidCarState", "driverOverrideOrUnknown", "incompleteCarCoverage",
        "carStateGap", "missingKinematics", "lowSpeedExcluded",
        "qualifiedWindows", "censoredByGapOrInvalidModel", "censoredAtEnd",
        "censoredBySegmentBoundaryOrUnverified", "carStateSegmentMismatch",
        "censoredByDirectionChangeOrUnknown",
    )
    score_fields = (
        "medianDurationSeconds", "p90PeakAbsSteeringRateDegPerSecond",
        "p90SteeringRateDerivativeDegPerSecondSquared", "medianQualifiedSpeedMps",
    )
    for phase in ("laneChangeStarting", "laneChangeFinishing"):
        data = lanes.get("phaseWindows", {}).get(phase, {})
        counts = data.get("counts", {})
        direction_counts = data.get("qualifiedByDirection", {})
        directions = {k: int(direction_counts.get(k, 0))
                      for k in ("left", "right", "none", "unknown")}
        directions["other"] = sum(int(v) for k, v in direction_counts.items()
                                  if k not in directions)
        phases[phase] = {
            "counts": {key: int(counts.get(key, 0)) for key in count_fields},
            "qualifiedByDirection": directions,
            **{key: data.get(key) for key in score_fields},
        }
    return {
        "modelSamples": int(lanes.get("modelSamples", 0)),
        "phaseWindows": phases,
        "physicalLaneBoundaryCrossing": "not_observable_from_logged_model_state",
    }


def summarize(records: list[dict], read_results) -> dict:
    """Only a fixed set of aggregate fields leaves the private analysis process."""
    provenance = pinned_recorded_build(records)
    motion = analyze_motion(records)
    # Historical episode helper assumes every input has a valid monotonic clock.
    # Corrupt/unknown services cannot supply an invented time anchor.
    timed = [r for r in records if type(r.get("t")) is int and r["t"] >= 0]
    episodes = analyze_episodes(timed)
    lanes = analyze_lanes(records)
    following = episodes.get("following", {})
    allowed_following = (
        "validCruiseLeadObservations", "minimumEpisodeSeconds",
        "qualifyingEpisodeCount", "qualifyingEpisodeTotalSeconds",
        "medianEpisodeGapSeconds", "medianObservedGapSeconds",
        "p10ObservedGapSeconds", "fractionObservedBelow1p5Seconds",
        "closingTTCMedianSeconds", "closingTTCObservations",
        "modelSourceEpisodeFractionMedian",
    )
    return {
        "status": "partial_observational_private_rlog_validation",
        "decoderPinnedCommit": PINNED_COMMIT,
        "provenance": provenance,
        "quality": {
            "selectedFiles": len(read_results),
            "decodedMessages": sum(f.messages for f in read_results),
            "truncatedFiles": sum(bool(f.truncated or f.error) for f in read_results),
        },
        "accMotionIntegrity": motion,
        "following": {
            **{k: following.get(k) for k in allowed_following},
            # Fixed known reason codes only; never forward raw diagnostic keys,
            # event text, segment identifiers or per-observation data.
            "validityRejections": {
                k: int(following.get("leadFilteringRejections", {}).get(k, 0))
                for k in ("invalidCarStateOrCan", "invalidRadarMessage",
                          "ambiguousRadarTimestamp")
            },
        },
        "laneModelPhases": public_lane_summary(lanes),
        "interpretation": (
            "OEM ACC target distance, throttle and brake commands not decoded; "
            "lead/source and acceleration are observational only. "
            "Model lane phases do not establish physical lane crossing. "
            "Segments and successive samples are correlated, not independent trials."
        ),
    }


def private_decode_failure_kind(path: Path, schema, *, segment: int) -> dict:
    """Classify first failure without serializing any private event or exception."""
    from ramtools.logio import event_to_record, read_log_bytes
    data, decompress_error = read_log_bytes(path)
    if decompress_error:
        return {"context": "decompression", "failureType": "CompressedStreamError"}
    count = 0
    try:
        for evt in schema.Event.read_multiple_bytes(data):
            count += 1
            if count > 5000:
                return {"context": "not_reproduced_in_first_5000", "failureType": "unknown"}
            try:
                name = evt.which()
                event_to_record(evt, segment)
            except Exception as e:
                safe = name if "name" in locals() and re.fullmatch(r"[A-Za-z0-9_]{1,48}", name) else "unknown"
                return {"context": "event_field_decoding",
                        "service": safe, "failureType": type(e).__name__}
    except Exception as e:
        return {"context": "capnp_event_framing", "failureType": type(e).__name__}
    return {"context": "no_reproduced_failure", "failureType": "unknown"}


def main() -> None:
    import boto3
    from botocore.config import Config

    stage("configuration")
    env = os.environ
    required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY", "RAM_ROUTE_PREFIX")
    if any(not env.get(k) for k in required):
        raise ValueError("missing private environment configuration")
    segments = parse_segments(env.get("RAM_SEGMENTS", "29,30,31"))
    schema_dir = Path(env.get("RAM_SCHEMA_DIR", "/opt/sunnypilot/cereal"))
    stage("schema")
    if any(v != "ok" for v in verify_schema_dir(schema_dir).values()):
        raise RuntimeError("pinned source schema integrity gate failed")
    schema = load_log_schema(schema_dir)
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"],
                      aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                      aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
                      region_name=env.get("S3_REGION", "auto"),
                      config=Config(s3={"addressing_style": "virtual"}))
    stage("inventory")
    pager = s3.get_paginator("list_objects_v2")
    objects = (item for page in pager.paginate(Bucket=env["S3_BUCKET"], Prefix="logs/")
               for item in page.get("Contents", ()))
    keys = select_keys(objects, route_prefix=env["RAM_ROUTE_PREFIX"], segments=segments)

    stage("download")
    with tempfile.TemporaryDirectory(prefix="ram-private-comfort-") as tmp:
        paths = []
        for segment, key in zip(segments, keys):
            # Structural filename is synthetic. Actual route/owner identifiers are
            # neither inserted into decoded records nor printed to stdout.
            p = Path(tmp) / f"00000000--0000000000--{segment}" / "rlog.zst"
            p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            s3.download_file(env["S3_BUCKET"], key, str(p))
            if not 0 < p.stat().st_size <= MAX_COMPRESSED_BYTES:
                raise RuntimeError("invalid private download size")
            p.chmod(0o600)
            paths.append(p)
        stage("decode")
        records, read_results = load_records(paths, schema)
        if len(read_results) != len(segments) or any(
                f.error or f.truncated or f.messages == 0 for f in read_results):
            # Never log path, private object key, per-frame content or the
            # untrusted exception text (it may contain identifiers).
            health = [{
                "fileOrdinal": i,
                "decodedMessages": int(f.messages),
                "truncated": bool(f.truncated),
                "errorCategory": (
                    "capnp" if f.error and "capnp:" in f.error else
                    "zstd" if f.error and "zstd:" in f.error else
                    "other" if f.error else "none"),
            } for i, f in enumerate(read_results)]
            diagnosis = private_decode_failure_kind(paths[0], schema, segment=segments[0])
            print("RAM_PRIVATE_COMFORT " + json.dumps({
                "status": "incomplete_private_decode",
                "files": health,
                "firstFileFailure": diagnosis,
            }, sort_keys=True), flush=True)
            raise RuntimeError("private R-log decode incomplete")
        stage("summary")
        source_evidence = require_comfort_segment_source_pins(records, read_results)
        payload = summarize(records, read_results)
        payload["provenance"].update(source_evidence)
        print("RAM_PRIVATE_COMFORT " + json.dumps(payload, sort_keys=True, allow_nan=False),
              flush=True)


if __name__ == "__main__":
    # No exception traceback: SDK errors can contain private endpoint/key/object data.
    try:
        main()
    except Exception as exc:
        print("RAM_PRIVATE_COMFORT " + json.dumps({
            "status": "failed_closed_private_data_or_quality_gate",
            "stage": CURRENT_STAGE,
            "failureType": type(exc).__name__,
        }, sort_keys=True), flush=True)
        raise SystemExit(1)
