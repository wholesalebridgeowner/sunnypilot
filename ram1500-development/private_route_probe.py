#!/usr/bin/env python3
"""Private, read-only S3 R-log probe for the pinned Comma 3 driving build.

Railway one-shot job only. No vehicle connection, CAN transmission, VIN access,
public HTTP listener, or raw telemetry in stdout. The device's cereal revision
is pinned in Dockerfile; never decode with arbitrary master schema.
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
from pathlib import Path

LOG_KEY = re.compile(r"^logs/(.+)/(\d+)/(rlog|qlog)\.zst$")
SELECTED_SEGMENTS = (0, 29, 30, 31)
MAX_COMPRESSED_BYTES = 25_000_000


def select_keys(objects, *, route_prefix, kind="rlog", segments=SELECTED_SEGMENTS):
    """Fail closed on ambiguous routes, missing segments, and oversized objects.

    No raw keys, route identifiers, or credentials appear in error messages.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{8}", route_prefix):
        raise ValueError("route prefix must be exactly eight hexadecimal characters")
    if kind not in ("rlog", "qlog"):
        raise ValueError("unsupported log kind")
    if len(set(segments)) != len(segments) or any(
        isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in segments
    ):
        raise ValueError("invalid requested segments")
    matches = {}
    for obj in objects:
        match = LOG_KEY.fullmatch(obj.get("Key", ""))
        if not match:
            continue
        route, segment_text, found_kind = match.groups()
        if found_kind != kind or not route.split("|")[-1].startswith(route_prefix + "--"):
            continue
        size = obj.get("Size", 0)
        if not isinstance(size, int) or size <= 0 or size > MAX_COMPRESSED_BYTES:
            raise ValueError("missing, empty, or oversized compressed log")
        segment = int(segment_text)
        if segment in segments:
            matches.setdefault(route, {})[segment] = obj["Key"]
    if len(matches) != 1:
        raise ValueError("route selection is ambiguous or absent")
    chosen = next(iter(matches.values()))
    if set(chosen) != set(segments):
        raise ValueError("incomplete selected segment coverage")
    return [chosen[s] for s in segments]


def reader_from_cereal(schema_path):
    """Use compressed Cap'n Proto log framing from the exact device revision."""
    import capnp
    import zstandard as zstd

    schema = capnp.load(str(schema_path), imports=[str(schema_path.parent)])
    def read(path):
        compressed = Path(path).read_bytes()
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as stream:
            decompressed = stream.read()
        # Keep decompressed bytes in scope while pycapnp iterates the messages.
        yield from schema.Event.read_multiple_bytes(decompressed)
    return read


def summarize(result, steering, coverage, eps, torque_window, cap_audit=None, command_replay=None):
    # Deliberately do not include absolute timestamps, route IDs, source paths,
    # VIN, raw samples, geoposition, or command traces in process output.
    groups = steering["groups"]
    metrics = groups["allControllerStateSamples"]["metrics"]
    fields = ("vEgo_mps", "steeringAngleDeg", "steeringTorqueEps",
              "requestedTorque", "outputTorqueCan", "lateralError")
    numbers = {name: {key: metrics[name][key]
                      for key in ("count", "median", "p95Abs", "maxAbs")}
               for name in fields}
    speed_bands = {
        name: {
            "carStateSamples": report["carStateSamples"],
            "eligibleActiveLowDriverSamples": report["eligibleActiveLowDriverSamples"],
            "delaysMs": report["delaysMs"],
        } for name, report in eps["speedBands"].items()
        if report["carStateSamples"]
    }
    return {
        "windowNumericMetrics": numbers,
        "focusedTorqueWindow": torque_window,
        "normalizedVsNativeTorqueCapAudit": cap_audit,
        "causalCommandReplay": command_replay,
        "epsDirectionCorrelationsBySpeed": speed_bands,
        "status": "partial_observational_analysis_only",
        "segments": list(SELECTED_SEGMENTS),
        "logKind": "rlog",
        "focusSeconds": result["summary"]["focusSeconds"],
        "focusWindowSeconds": result["summary"]["windowSeconds"],
        "messagesScannedInSelectedSegments": result["summary"]["messageCount"],
        "windowRows": len(result["rows"]),
        "steeringEventSamplesInSelectedSegments": steering["eventSampleCount"],
        "steeringEpisodesInSelectedSegments": steering["eventEpisodeCount"],
        "controllerSamplesInWindow": steering["controllerSamples"],
        "saturatedControllerSamplesInWindow":
            groups["controllerSaturated"]["samples"],
        "nearCommandLimitControllerSamplesInWindow":
            groups["allControllerStateSamples"]["nearReportedCommandLimitSamples"],
        "sourceMatchedWithinAgeSamples": steering["sourceMatchedWithinAgeSamples"],
        "quality": {
            "inputRows": coverage.get("inputRows"),
            "eventEpisodes": coverage.get("steerSaturatedEpisodeCount"),
        },
        "warning": "Selected segments only. Steering command, EPS feedback and event correlation cannot prove physical actuation or authorize a software release.",
    }


def main():
    import boto3
    from botocore.config import Config
    from analyze_route import analyze
    import steering_saturation_report
    import telemetry_coverage
    import eps_feedback_lag
    import torque_limit_coverage
    import torque_command_replay
    import torque_replay_phase_audit

    env = os.environ
    required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY", "RAM_ROUTE_PREFIX")
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise RuntimeError("required private environment references unavailable: " +
                           ",".join(missing))

    s3 = boto3.client(
        "s3", endpoint_url=env["S3_ENDPOINT"],
        aws_access_key_id=env["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
        region_name=env.get("S3_REGION", "auto"),
        config=Config(s3={"addressing_style": "virtual"}),
    )
    paginator = s3.get_paginator("list_objects_v2")
    objects = (obj for page in paginator.paginate(Bucket=env["S3_BUCKET"],
                                                   Prefix="logs/")
               for obj in page.get("Contents", ()))
    keys = select_keys(objects, route_prefix=env["RAM_ROUTE_PREFIX"])
    with tempfile.TemporaryDirectory(prefix="ram-private-") as dirname:
        root = Path(dirname) / "private"
        paths = []
        for segment, key in zip(SELECTED_SEGMENTS, keys):
            p = root / str(segment) / "rlog.zst"
            p.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(env["S3_BUCKET"], key, str(p))
            if p.stat().st_size == 0 or p.stat().st_size > MAX_COMPRESSED_BYTES:
                raise RuntimeError("invalid compressed download size")
            paths.append(p)
        schema = Path(env.get("PINNED_CEREAL_LOG_SCHEMA",
                              "/opt/sunnypilot/cereal/log.capnp"))
        focus = float(env.get("RAM_FOCUS_SECONDS", "1803"))
        window = float(env.get("RAM_WINDOW_SECONDS", "40"))
        if not (0 <= focus <= 86400 and 0 <= window <= 180):
            raise ValueError("focus must be 0..86400 sec and window 0..180 sec")
        result = analyze(paths, reader_from_cereal(schema), focus, window)
        steering = steering_saturation_report.analyze(result["rows"])
        coverage = telemetry_coverage.analyze(result["rows"])
        eps = eps_feedback_lag.analyze(result["rows"])
        focus_rows = [row for row in result["rows"]
                      if 1800 <= row["seconds"] <= 1807]
        focused = steering_saturation_report.analyze(focus_rows)
        focused_group = focused["groups"]["allControllerStateSamples"]
        torque_window = {
            "routeRelativeSeconds": [1800, 1807],
            "controllerSamples": focused["controllerSamples"],
            "nearCommandLimitControllerSamples":
                focused_group["nearReportedCommandLimitSamples"],
            "requestNormalized": focused_group["metrics"]["requestedTorque"],
            "commandNative": focused_group["metrics"]["outputTorqueCan"],
            "epsFeedbackNative": focused_group["metrics"]["steeringTorqueEps"],
        }
        cap_audit = torque_limit_coverage.audit(result["rows"],
                begin_seconds=1800.0, end_seconds=1807.0)
        command_replay = torque_command_replay.analyze(result["rows"],
                start=1800.0, end=1807.0)
        phase_audit = torque_replay_phase_audit.audit(result["rows"],
                start=1800.0, end=1807.0)
        summary = summarize(result, steering, coverage, eps, torque_window, cap_audit, command_replay)
        summary["commandReplayPhaseSensitivity"] = phase_audit
        print("RAM_PRIVATE_PROBE " + json.dumps(
            summary, separators=(",", ":"), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
