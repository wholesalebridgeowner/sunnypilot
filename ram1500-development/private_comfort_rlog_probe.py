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

PINNED_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
KEY_PATTERN = re.compile(r"^logs/([^/]+)/([0-9]+)/rlog\.zst$")
ROUTE_PATTERN = re.compile(r"^[0-9a-f]{8}--[0-9a-f]{10}$")
MAX_COMPRESSED_BYTES = 30_000_000
MAX_SEGMENTS = 4


def parse_segments(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+){0,3}", value):
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
    commits = [str(r.get("gitCommit") or "").lower().strip()
               for r in records if r.get("service") == "initData"]
    commits = [v for v in commits if v]
    matches = all(len(v) >= 8 and (PINNED_COMMIT.startswith(v) or v == PINNED_COMMIT)
                  for v in commits)
    return {
        "initDataCommitObservations": len(commits),
        "recordedCommitMatchedPinned": bool(commits) and matches,
        "sourceWorkingTreeStillUnverified": True,
    }


def public_lane_summary(lanes: dict) -> dict:
    """Bound all exported keys, including enum-like values from model records."""
    phases = {}
    count_fields = (
        "completedTransitions", "invalidDuration", "insufficientCarState",
        "invalidCarState", "driverOverrideOrUnknown", "incompleteCarCoverage",
        "carStateGap", "missingKinematics", "lowSpeedExcluded",
        "qualifiedWindows", "censoredByGapOrInvalidModel", "censoredAtEnd",
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
    episodes = analyze_episodes(records)
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
        "following": {k: following.get(k) for k in allowed_following},
        "laneModelPhases": public_lane_summary(lanes),
        "interpretation": (
            "OEM ACC target distance, throttle and brake commands not decoded; "
            "lead/source and acceleration are observational only. "
            "Model lane phases do not establish physical lane crossing. "
            "Segments and successive samples are correlated, not independent trials."
        ),
    }


def main() -> None:
    import boto3
    from botocore.config import Config

    env = os.environ
    required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY", "RAM_ROUTE_PREFIX")
    if any(not env.get(k) for k in required):
        raise ValueError("missing private environment configuration")
    segments = parse_segments(env.get("RAM_SEGMENTS", "29,30,31"))
    schema_dir = Path(env.get("RAM_SCHEMA_DIR", "/opt/sunnypilot/cereal"))
    if any(v != "ok" for v in verify_schema_dir(schema_dir).values()):
        raise RuntimeError("pinned source schema integrity gate failed")
    schema = load_log_schema(schema_dir)
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"],
                      aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                      aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
                      region_name=env.get("S3_REGION", "auto"),
                      config=Config(s3={"addressing_style": "virtual"}))
    pager = s3.get_paginator("list_objects_v2")
    objects = (item for page in pager.paginate(Bucket=env["S3_BUCKET"], Prefix="logs/")
               for item in page.get("Contents", ()))
    keys = select_keys(objects, route_prefix=env["RAM_ROUTE_PREFIX"], segments=segments)

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
        records, read_results = load_records(paths, schema)
        if len(read_results) != len(segments) or any(
                f.error or f.truncated or f.messages == 0 for f in read_results):
            raise RuntimeError("private R-log decode incomplete")
        payload = summarize(records, read_results)
        print("RAM_PRIVATE_COMFORT " + json.dumps(payload, sort_keys=True, allow_nan=False),
              flush=True)


if __name__ == "__main__":
    # No exception traceback: SDK errors can contain private endpoint/key/object data.
    try:
        main()
    except Exception:
        print('RAM_PRIVATE_COMFORT {"status":"failed_closed_private_data_or_quality_gate"}',
              flush=True)
        raise SystemExit(1)
