#!/usr/bin/env python3
"""One-shot private Railway pilot of bounded cross-segment Q-log metrics.

Only processes an existing quarantined route. Never changes its original
quarantine, the 'latest' accepted report, the Comma, or driving controls.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from auto_review import SCHEMA_COMMIT, summarize_log_messages
from auto_review_crosssegment_order import source_pinned_route_messages, RouteOrderingError
from auto_review_job import (group_routes, route_key, bounded_trial_key,
                             regression_origin_key, read_json_or_none,
                             put_private_json, MAX_COMPRESSED, PREFIX)

TRIAL_PREFIX = PREFIX + "intersegment-trials/"

def choose_candidate(s3, bucket, now):
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="logs/")
    candidates = group_routes((o for page in pages for o in page.get("Contents", ())), now)
    for candidate in candidates:
        if read_json_or_none(s3, bucket, TRIAL_PREFIX + candidate["hash"] + "/"
                             + candidate["fingerprint"] + ".json") is not None:
            continue
        baseline = read_json_or_none(s3, bucket, route_key(candidate))
        if not isinstance(baseline, dict) or baseline.get("status") != "quarantined":
            continue
        reasons = []
        for key in (bounded_trial_key(candidate), regression_origin_key(candidate)):
            old = read_json_or_none(s3, bucket, key)
            if isinstance(old, dict):
                reasons.append(old.get("reason"))
        if "segment_boundary_regression" in reasons:
            return candidate
    return None

def safe_failure(exc):
    if type(exc) is RouteOrderingError and str(exc) in {
        "non-empty-counters", "recorded-source-not-verified",
        "invalid-event-clock", "lateness-exceeds-window", "bounded-memory-ceiling",
        "emission-regression", "missing-source-header",
    }:
        return str(exc)
    return "reader_or_metrics_invalid"

def run(s3, bucket, now, reader):
    chosen = choose_candidate(s3, bucket, now)
    if chosen is None:
        return {"status": "no_eligible_quarantined_boundary_routes"}
    storage_key = (TRIAL_PREFIX + chosen["hash"] + "/" + chosen["fingerprint"] + ".json")
    with tempfile.TemporaryDirectory(prefix="ram-intersegment-trial-") as root:
        paths = []
        for segment, obj in chosen["items"]:
            local = Path(root) / str(segment) / "qlog.zst"
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(local))
            if not (0 < local.stat().st_size <= MAX_COMPRESSED):
                raise ValueError("invalid downloaded size")
            paths.append(local)
        counters = {}
        try:
            metrics = summarize_log_messages(
                source_pinned_route_messages(paths, reader, counters))
            if counters["verifiedSegments"] != len(paths):
                raise RouteOrderingError("missing-source-header")
            if metrics["coverage"]["carStateSamples"] < 1:
                raise ValueError("no recorded car state")
            body = {
                "status": "experimental_intersegment_metrics",
                "schemaCommit": SCHEMA_COMMIT,
                "segments": len(paths),
                "sourceVerificationComplete": True,
                "clockCounters": counters,
                "metrics": metrics,
                "releaseAuthorized": False,
                "note": "Private trial: event-time ordering of complete original observations; not an accepted route, control release, or actuator validation",
            }
            summary = {
                "status": "experimental_intersegment_metrics_saved",
                "segments": len(paths),
                "carStateSamples": metrics["coverage"]["carStateSamples"],
                "laneChanges": metrics["laneChange"]["completeStartingWindows"],
                "steerSaturatedEpisodes": metrics["steering"]["steerSaturatedEventEpisodes"],
                "intraSegmentInversions": counters["intraSegmentInversions"],
                "crossSegmentInversions": counters["crossSegmentInversions"],
                "releaseAuthorized": False,
            }
        except Exception as exc:
            reason = safe_failure(exc)
            body = {"status": "experimental_intersegment_rejected",
                    "schemaCommit": SCHEMA_COMMIT,
                    "segments": len(paths), "reason": reason,
                    "releaseAuthorized": False}
            summary = {"status": "experimental_intersegment_rejected",
                       "segments": len(paths), "reason": reason,
                       "releaseAuthorized": False}
    # Never rewrite the original quarantine, other diagnostics or latest.json.
    put_private_json(s3, bucket, storage_key, body)
    return summary

def main():
    import boto3
    from botocore.config import Config
    from private_route_probe import reader_from_cereal
    env = os.environ
    keys = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    if any(not env.get(k) for k in keys):
        raise RuntimeError("private bucket configuration missing")
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"],
          aws_access_key_id=env["S3_ACCESS_KEY_ID"],
          aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
          region_name=env.get("S3_REGION", "auto"),
          config=Config(s3={"addressing_style": "virtual"}))
    schema = Path(env.get("PINNED_CEREAL_LOG_SCHEMA", "/opt/sunnypilot/cereal/log.capnp"))
    outcome = run(s3, env["S3_BUCKET"], datetime.now(timezone.utc),
                  reader_from_cereal(schema))
    print("RAM_INTERSEGMENT_TRIAL " + json.dumps(outcome, separators=(",", ":")),
          flush=True)

if __name__ == "__main__":
    main()
