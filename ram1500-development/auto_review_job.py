#!/usr/bin/env python3
"""Scheduled private S3 route analyzer. Never contacts or commands a Comma.

Reviews one eligible route per run to bound cost and memory. Stores aggregate
analysis in the existing private bucket, never raw telemetry in public logs.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from auto_review import SCHEMA_COMMIT, compare, summarize_log_messages

PATH = re.compile(r"^logs/(.+)/([0-9]+)/qlog\.zst$")
MAX_SEGMENTS = 96
MAX_COMPRESSED = 25_000_000
QUIET_HOURS = 0.5  # 30-minute upload quiet period; later uploads produce a new fingerprint
LOOKBACK_DAYS = 14
PREFIX = "automated-ram-reviews/v1/"

def group_routes(objects, now, *, quiet_hours=QUIET_HOURS, lookback_days=LOOKBACK_DAYS):
    """Route grouping, strict contiguous segment gate; newest route first."""
    groups = defaultdict(list)
    for o in objects:
        match = PATH.fullmatch(o.get("Key", ""))
        if not match:
            continue
        updated = o.get("LastModified")
        size = o.get("Size")
        route, segment = match.groups()
        # Retain all objects so recent or invalid segments invalidate the route.
        groups[route].append((int(segment), o))
    candidates = []
    for route, segments in groups.items():
        if not 1 <= len(segments) <= MAX_SEGMENTS:
            continue
        if any(not isinstance(o.get("LastModified"), datetime)
               or o["LastModified"].tzinfo is None
               or not (now - timedelta(days=lookback_days) <= o["LastModified"]
                       <= now - timedelta(hours=quiet_hours))
               or not isinstance(o.get("Size"), int)
               or not (0 < o["Size"] <= MAX_COMPRESSED)
               for _, o in segments):
            continue
        nums = sorted(s for s, _ in segments)
        if nums != list(range(len(nums))):
            continue
        # Only accept a route if *all* of its known objects have aged out.
        entries = sorted(segments)
        latest = max(o["LastModified"] for _, o in entries)
        if latest > now - timedelta(hours=quiet_hours):
            continue
        h = hashlib.sha256()
        h.update(SCHEMA_COMMIT.encode())
        for segment, o in entries:
            h.update(f"{segment}:{o['Size']}:{o.get('ETag','')}:".encode())
            h.update(o["LastModified"].isoformat().encode())
        route_hash = hashlib.sha256(route.encode()).hexdigest()[:20]
        candidates.append({"hash": route_hash, "route": route, "items": entries,
                           "fingerprint": h.hexdigest()[:24], "updated": latest})
    return sorted(candidates, key=lambda x: x["updated"], reverse=True)

def route_key(candidate):
    return PREFIX + "reports/" + candidate["hash"] + "/" + candidate["fingerprint"] + ".json"

def read_json_or_none(s3, bucket, key):
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(raw)
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            return None
        raise

def put_private_json(s3, bucket, key, payload):
    s3.put_object(Bucket=bucket, Key=key, Body=(json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n").encode(),
                  ContentType="application/json")

class SourcePinError(ValueError):
    """A selected segment does not prove the full installed-build source."""


def pinned_qlog_messages(paths, reader):
    """Discard replayed old-clock initData AFTER exact per-segment verification.

    Q-log initData headers can repeat at segment boundaries with timestamps
    from an older process. They are metadata, not route motion observations.
    Other non-monotonic traffic is never reordered or silently discarded.
    """
    for path in paths:
        headers = 0
        for message in reader(path):
            kind = str(message.which())
            if kind == "initData":
                recorded = getattr(message.initData, "gitCommit", None)
                if type(recorded) is not str or recorded != SCHEMA_COMMIT:
                    raise SourcePinError("recorded source is missing or does not match")
                headers += 1
                continue
            yield message
        if not headers:
            raise SourcePinError("segment has no exact source header")


def run(s3, bucket, now, reader):
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="logs/")
    objects = (obj for page in pages for obj in page.get("Contents", ()))
    candidates = group_routes(objects, now)
    selected = None
    for candidate in candidates:
        if read_json_or_none(s3, bucket, route_key(candidate)) is None:
            selected = candidate
            break
    if selected is None:
        return {"status": "no_new_eligible_routes", "eligible": len(candidates)}
    with tempfile.TemporaryDirectory(prefix="ram-auto-review-") as folder:
        paths = []
        for segment, o in selected["items"]:
            output = Path(folder) / str(segment) / "qlog.zst"
            output.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, o["Key"], str(output))
            if not (0 < output.stat().st_size <= MAX_COMPRESSED):
                raise ValueError("Invalid compressed input size")
            paths.append(output)
        try:
            metrics = summarize_log_messages(pinned_qlog_messages(paths, reader))
        except Exception as exc:
            # Persist a private, source-scoped tombstone so one invalid historical
            # route cannot fail every five-minute cron and starve older routes.
            # Neither the exception message nor private file identifiers escape.
            reason = ("source_unverified" if isinstance(exc, SourcePinError)
                      else "decode_or_order_invalid")
            put_private_json(s3, bucket, route_key(selected), {
                "reportVersion": 1, "status": "quarantined",
                "reason": reason, "schemaCommit": SCHEMA_COMMIT,
                "sourcePinRequiredPerSegment": True,
                "routeHash": selected["hash"],
                "inputFingerprint": selected["fingerprint"],
                "segments": len(paths),
            })
            return {"status": "route_quarantined", "reason": reason,
                    "segments": len(paths)}
    latest_key = PREFIX + "latest.json"
    latest = read_json_or_none(s3, bucket, latest_key)
    # A later upload can change a route's fingerprint; never use that
    # same route's older partial report as an independent comparator.
    prior = (latest["metrics"] if latest and latest.get("metrics")
             and latest.get("routeHash") != selected["hash"] else None)
    is_newest = not latest or selected["updated"].isoformat() >= latest.get("routeUpdatedAt", "")
    delta = compare(metrics, prior if is_newest else None)
    report = {
        "reportVersion": 1, "routeHash": selected["hash"],
        "inputFingerprint": selected["fingerprint"],
        "schemaCommit": SCHEMA_COMMIT,
        "analyzedAt": now.isoformat(),
        "routeUpdatedAt": selected["updated"].isoformat(),
        "segments": len(selected["items"]),
        "sourceVerifiedSegments": len(paths),
        "completeness": "contiguous-from-zero-after-30-minutes-quiet; final-route-count-unverified",
        "metrics": metrics, "comparison": delta,
        "recommendation": "Observational review only. No automatic steering or braking code deployment.",
    }
    put_private_json(s3, bucket, route_key(selected), report)
    if is_newest:
        put_private_json(s3, bucket, latest_key, report)
    # No location, route names, identifiers or telemetry in console output.
    return {"status": "review_saved", "segments": len(selected["items"]),
            "laneChanges": metrics["laneChange"]["completeStartingWindows"],
            "steeringEventEpisodes": metrics["steering"]["steerSaturatedEventEpisodes"],
            "comparisonBaseline": delta["baselineAvailable"], "becameLatest": is_newest}

def main():
    import boto3
    from botocore.config import Config
    from private_route_probe import reader_from_cereal
    env = os.environ
    required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise RuntimeError("Missing storage configuration: " + ",".join(missing))
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"],
                      aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                      aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
                      region_name=env.get("S3_REGION", "auto"),
                      config=Config(s3={"addressing_style": "virtual"}))
    schema = Path(env.get("PINNED_CEREAL_LOG_SCHEMA", "/opt/sunnypilot/cereal/log.capnp"))
    reader = reader_from_cereal(schema)
    result = run(s3, env["S3_BUCKET"], datetime.now(timezone.utc), reader)
    print("RAM_AUTO_REVIEW " + json.dumps(result, separators=(",", ":")), flush=True)

if __name__ == "__main__":
    main()
