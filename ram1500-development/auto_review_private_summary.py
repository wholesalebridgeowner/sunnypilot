#!/usr/bin/env python3
"""Read only private experimental route metrics; print strictly aggregate fields.

No device I/O, route identifiers, raw telemetry, coordinates or credentials
in stdout. Data stays in Railway's private bucket.
"""
from __future__ import annotations
import json
import math
import os

from auto_review_crosssegment_pilot import TRIAL_PREFIX

MAX_REPORTS = 48
MAX_BYTES = 1000000

def scalar(value):
    if value is None:
        return None
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return round(value, 4)
    return None

def public_metric_summary(body):
    if not isinstance(body, dict) or body.get("status") != "experimental_intersegment_metrics":
        return None
    if type(body.get("segments")) is not int or body["segments"] < 1:
        return None
    m = body.get("metrics")
    if not isinstance(m, dict):
        return None
    data = {
        "segments": scalar(body.get("segments")),
        "sourceVerified": body.get("sourceVerificationComplete") is True,
        "carStateSamples": scalar(m.get("coverage", {}).get("carStateSamples")),
        "carControlSamples": scalar(m.get("messageCounts", {}).get("carControl")),
        "controlsStateSamples": scalar(m.get("coverage", {}).get("controlsStateSamples")),
        "laneModelSamples": scalar(m.get("coverage", {}).get("laneModelSamples")),
        "radarStateSamples": scalar(m.get("coverage", {}).get("radarStateSamples")),
        "carControlLatActiveTrueSamples": scalar(m.get("cruiseOwnership", {}).get("latActiveSamples", {}).get("True")),
        "carControlLatActiveFalseSamples": scalar(m.get("cruiseOwnership", {}).get("latActiveSamples", {}).get("False")),
        "carControlLongActiveTrueSamples": scalar(m.get("cruiseOwnership", {}).get("longActiveSamples", {}).get("True")),
        "routeDurationSeconds": scalar(m.get("durationSeconds")),
        "laneChangeStartingWindows": scalar(m.get("laneChange", {}).get("completeStartingWindows")),
        "lateralActiveSamples": scalar(m.get("steering", {}).get("lateralActiveSamples")),
        "steeringSaturationEpisodes": scalar(m.get("steering", {}).get("steerSaturatedEventEpisodes")),
        "p90AbsSteeringRateDegPerSec": scalar(m.get("steering", {}).get("p90AbsSteeringRateDegPerSecond")),
        "p90AbsObservedAccelerationJerk": scalar(m.get("comfort", {}).get("p90AbsJerkMps3")),
        "qualifiedHeadwaySamples": scalar(m.get("leadHeadway", {}).get("eligibleCruiseSamples")),
        "medianObservedHeadwaySec": scalar(m.get("leadHeadway", {}).get("medianSeconds")),
    }
    if not data["sourceVerified"] or data["carStateSamples"] is None:
        return None
    return data

def run(s3, bucket):
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=TRIAL_PREFIX)
    entries = [o for page in pages for o in page.get("Contents", ())
               if o.get("Key", "").endswith(".json")]
    if len(entries) > MAX_REPORTS:
        return {"status": "too_many_reports_for_bounded_summary", "reportCount": len(entries)}
    outputs = []
    skipped = 0
    for entry in entries:
        if not (0 < entry.get("Size", 0) <= MAX_BYTES):
            skipped += 1
            continue
        try:
            payload = json.loads(s3.get_object(Bucket=bucket, Key=entry["Key"])["Body"].read())
            summary = public_metric_summary(payload)
        except Exception:
            # Never expose raw exception messages, keys, payloads or identifiers.
            summary = None
        if summary:
            outputs.append(summary)
        else:
            skipped += 1
    outputs.sort(key=lambda x: x["segments"], reverse=True)
    return {"status": "private_aggregate_metrics", "reports": outputs,
            "acceptedExperimental": len(outputs), "skipped": skipped,
            "validForDeployment": False,
            "caveat": "Different routes are not matched conditions and cannot prove improved driving comfort"}

def main():
    import boto3
    from botocore.config import Config
    env = os.environ
    required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    if any(not env.get(k) for k in required):
        raise RuntimeError("missing private bucket configuration")
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"],
                      aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                      aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
                      region_name=env.get("S3_REGION", "auto"),
                      config=Config(s3={"addressing_style": "virtual"}))
    result = run(s3, env["S3_BUCKET"])
    print("RAM_PRIVATE_AGGREGATES " + json.dumps(result, separators=(",", ":")), flush=True)

if __name__ == "__main__":
    main()
