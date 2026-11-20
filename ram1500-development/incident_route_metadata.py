#!/usr/bin/env python3
"""Private Comma API + S3 route inventory around an 8:19 PM local warning.

Only anonymous counts and relative times are printed. Exact device IDs,
route names, locations, raw telemetry, and auth credentials remain private.
No vehicle commands or on-device changes.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from incident_route_inventory import KEY, TARGET

def clean(name):
    return re.sub(r"[^A-Za-z0-9._|\-]", "_", name)

def parse_time(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int,float)) and not isinstance(value, bool):
            epoch = float(value)
            if epoch > 1e12: epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        if isinstance(value, str):
            st = value.strip()
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", st):
                return parse_time(float(st))
            out = datetime.fromisoformat(st.replace("Z","+00:00"))
            return out.astimezone(timezone.utc) if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError, TypeError):
        pass
    return None

def safe_api(url, jwt):
    req = Request(url, headers={"Authorization":"JWT "+jwt, "Accept":"application/json"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)

def route_meta(s3_objects, records, target=TARGET):
    by_route=defaultdict(lambda:{"qlog":set(),"rlog":set()})
    for obj in s3_objects:
        match=KEY.fullmatch(obj.get("Key",""))
        if match:
            by_route[match["route"]][match["kind"]].add(int(match["segment"]))
    coverage=defaultdict(lambda:{"starts":[],"ends":[],"metadataSegments":set()})
    for rec in records:
        name=rec.get("fullname") or rec.get("canonical_route_name")
        if not isinstance(name,str) or not name:
            continue
        stored=clean(name)
        if stored not in by_route:
            continue
        start=parse_time(rec.get("start_time") or rec.get("start_time_utc_millis") or rec.get("create_time"))
        end=parse_time(rec.get("end_time") or rec.get("end_time_utc_millis"))
        if start: coverage[stored]["starts"].append(start)
        if end: coverage[stored]["ends"].append(end)
        for segment in rec.get("segment_number",[]) if isinstance(rec.get("segment_number"),list) else []:
            if type(segment) is int: coverage[stored]["metadataSegments"].add(segment)
        value=rec.get("number")
        if type(value) is int: coverage[stored]["metadataSegments"].add(value)
    selected=[]
    for route,info in coverage.items():
        start=min(info["starts"],default=None)
        end=max(info["ends"],default=None)
        if start is None:
            continue
        # Timestamps in route metadata, not names. Use broad tolerance for
        # partial uploads and recordings still in progress.
        if not (target-timedelta(hours=6) <= start <= target+timedelta(minutes=2)):
            continue
        if end is not None and end < target-timedelta(minutes=5):
            continue
        s3=by_route[route]
        elapsed=(target-start).total_seconds()
        exp=max(0,int(elapsed // 60))
        selected.append({
            "secondsAfterRecordedStart":round(elapsed),
            "secondsBeforeRecordedEnd":round((end-target).total_seconds()) if end else None,
            "qlogSegmentsStored":len(s3["qlog"]),
            "rlogSegmentsStored":len(s3["rlog"]),
            "estimatedWarningSegment":exp,
            "qlogWithinTwoEstimatedSegments":sum(1 for i in range(max(0,exp-2),exp+3) if i in s3["qlog"]),
            "rlogWithinTwoEstimatedSegments":sum(1 for i in range(max(0,exp-2),exp+3) if i in s3["rlog"]),
            "metadataSegments":len(info["metadataSegments"]),
        })
    selected.sort(key=lambda x:x["secondsAfterRecordedStart"])
    return {
        "status":"private_incident_route_metadata_inventory",
        "utcWarning":"2026-11-20T02:19:00Z",
        "storedRoutes":len(by_route),
        "metadataMatchedStoredRoutes":len(coverage),
        "candidateCount":len(selected),
        "candidates":selected[:6],
        "eventInLogConfirmed":False,
        "note":"Time/segment estimate only. Use initData wallTimeNanos + logMonoTime or a source-verified wall clock before concluding event occurred in files."
    }

def main():
    import boto3
    from botocore.config import Config
    env=os.environ
    required=("S3_BUCKET","S3_ENDPOINT","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY","COMMA_JWT")
    if any(not env.get(k) for k in required):
        raise RuntimeError("Required private cloud configuration not present")
    s3=boto3.client("s3",endpoint_url=env["S3_ENDPOINT"],
        aws_access_key_id=env["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
        region_name=env.get("S3_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    s3page=s3.get_paginator("list_objects_v2").paginate(Bucket=env["S3_BUCKET"],Prefix="logs/")
    objects=[o for p in s3page for o in p.get("Contents",())]
    devices=safe_api("https://api.comma.ai/v1/me/devices/",env["COMMA_JWT"])
    if not isinstance(devices,list):
        raise RuntimeError("Unexpected paired-device inventory type")
    configured=env.get("COMMA_DONGLE_ID","")
    matched=[d for d in devices if isinstance(d,dict) and d.get("dongle_id")
             and d.get("is_paired") is not False and (not configured or d["dongle_id"]==configured)]
    if len(matched)!=1:
        print("RAM_2019_INCIDENT "+json.dumps({"status":"device_selection_ambiguous",
                  "pairedCandidateCount":len(matched)}),flush=True)
        return
    id=matched[0]["dongle_id"]
    qs=urlencode({"start":int((TARGET-timedelta(hours=6)).timestamp()*1000),
                  "end":int((TARGET+timedelta(hours=1)).timestamp()*1000)})
    records=safe_api("https://api.comma.ai/v1/devices/"+quote(id,safe="")+"/routes_segments?"+qs,
                     env["COMMA_JWT"])
    if not isinstance(records,list):
        raise RuntimeError("Unexpected route metadata shape")
    output=route_meta(objects,records)
    output["metadataRows"]=len(records)
    print("RAM_2019_INCIDENT "+json.dumps(output,separators=(",",":")),flush=True)

if __name__=="__main__":
    main()
