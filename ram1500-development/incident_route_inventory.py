#!/usr/bin/env python3
"""Find the PRIVATE Comma route containing a driver-reported warning.

No raw route names, device IDs, positions, VINs, or credentials in stdout.
This is an inventory-only first pass: keys and counts do not prove that the
event itself is in the available logs.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import json
import os
import re

KEY = re.compile(r"^logs/(?P<route>[^/]+)/(?P<segment>[0-9]+)/(?P<kind>qlog|rlog)\.zst$")
ROUTE_START = re.compile(r"(?:^|\|)(?P<day>20[0-9]{2}-[01][0-9]-[0-3][0-9])--(?P<hh>[0-2][0-9])-(?P<mm>[0-5][0-9])-(?P<ss>[0-5][0-9])$")
TARGET = datetime(2026, 11, 20, 2, 19, 0, tzinfo=timezone.utc)
MAX_PRE_EVENT_HOURS = 6
SEGMENT_SECONDS = 60

def route_start(route):
    match = ROUTE_START.search(route)
    if not match:
        return None
    try:
        return datetime.fromisoformat(
            f"{match['day']}T{match['hh']}:{match['mm']}:{match['ss']}+00:00"
        )
    except ValueError:
        return None

def report_inventory(objects, target=TARGET):
    routes = defaultdict(lambda: {"qlog":set(), "rlog":set()})
    known_routes = set()
    unparsable = set()
    for obj in objects:
        key = obj.get("Key", "")
        match = KEY.fullmatch(key)
        if not match:
            continue
        route = match["route"]
        known_routes.add(route)
        if route_start(route) is None:
            unparsable.add(route)
        routes[route][match["kind"]].add(int(match["segment"]))
    candidates = []
    for route, data in routes.items():
        start = route_start(route)
        if not start or not (target-timedelta(hours=MAX_PRE_EVENT_HOURS) <= start <= target+timedelta(seconds=30)):
            continue
        elapsed = (target-start).total_seconds()
        expected = max(0, int(elapsed // SEGMENT_SECONDS))
        extent = max(data["qlog"] | data["rlog"], default=-1)
        if extent < expected - 2:
            continue
        candidates.append({
            "secondsAfterRouteStart":round(elapsed),
            "expectedSegmentBySixtySecondEstimate":expected,
            "qlogWithinTwoSegments":sum(1 for k in range(max(0,expected-2),expected+3) if k in data["qlog"]),
            "rlogWithinTwoSegments":sum(1 for k in range(max(0,expected-2),expected+3) if k in data["rlog"]),
            "qlogCount":len(data["qlog"]),
            "rlogCount":len(data["rlog"]),
            "highestSegment":extent,
        })
    candidates.sort(key=lambda x:x["secondsAfterRouteStart"])
    return {
        "status":"incident_route_inventory_only",
        "target":"2026-11-20T02:19:00Z",
        "distinctRoutes":len(known_routes),
        "unparsableRouteCount":len(unparsable),
        "candidateCount":len(candidates),
        "candidates":candidates[:8],
        "routeClockAssumption":"Route timestamps assumed UTC, segments roughly 60s; must verify via initData.wallTimeNanos and logMonoTime",
        "eventConfirmed":False
    }

def main():
    import boto3
    from botocore.config import Config
    env=os.environ
    required=("S3_BUCKET","S3_ENDPOINT","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY")
    if any(not env.get(k) for k in required):
        raise RuntimeError("private S3 storage credentials not available")
    s3=boto3.client("s3",endpoint_url=env["S3_ENDPOINT"],
        aws_access_key_id=env["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
        region_name=env.get("S3_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    pag=s3.get_paginator("list_objects_v2").paginate(Bucket=env["S3_BUCKET"],Prefix="logs/")
    outcome=report_inventory(obj for page in pag for obj in page.get("Contents",()))
    print("RAM_2019_INCIDENT "+json.dumps(outcome,separators=(",",":")),flush=True)

if __name__=="__main__":
    main()
