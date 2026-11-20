#!/usr/bin/env python3
"""Read-only, time-correlated Q-log investigation of 8:19 PM steering warning.

Uses comma.ai route metadata + a pinned cereal source + initData wall/mono
anchors. Only fixed alert labels and numeric aggregate ranges leave the
private container. Raw route/device IDs, coordinates, CAN, and auth never print.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, urlencode
import re

from incident_route_inventory import KEY, TARGET
from incident_route_metadata import clean, parse_time, safe_api
from auto_review import SCHEMA_COMMIT
from private_route_probe import reader_from_cereal

ALERTS = {
    "steerSaturated", "steerTimeLimit", "steerUnavailable", "steerTempUnavailable",
    "steerTempUnavailableSilent", "steerOverride", "steerDisengage",
    "controlsMismatch", "excessiveActuation", "canError", "stockLkas",
    "steerTimeLimit", "steerTempUnavailable"
}
WINDOW_SECONDS=120
MAX_COMPRESSED=25000000
MAX_SEGMENTS=9

def finite(value):
    return type(value) in (float,int) and math.isfinite(float(value))

def num(o, key):
    try:
        v=getattr(o,key) if not isinstance(o,dict) else o.get(key)
        return float(v) if finite(v) else None
    except (AttributeError,KeyError,ValueError,TypeError,OverflowError):
        return None

def private_candidate(objects, rows, target=TARGET):
    indexed={}
    for obj in objects:
        m=KEY.fullmatch(obj.get("Key",""))
        if m:
            indexed.setdefault(m["route"],{}).setdefault(m["kind"],{})[int(m["segment"])]=obj
    candidates={}
    for row in rows:
        name=row.get("fullname") or row.get("canonical_route_name")
        if not isinstance(name,str):continue
        stored=clean(name)
        if stored not in indexed:continue
        begin=parse_time(row.get("start_time") or row.get("start_time_utc_millis") or row.get("create_time"))
        finish=parse_time(row.get("end_time") or row.get("end_time_utc_millis"))
        if begin is None or not (target-timedelta(hours=6)<=begin<=target+timedelta(minutes=2)):continue
        if finish is not None and finish < target-timedelta(minutes=5):continue
        elapsed=(target-begin).total_seconds()
        segment=max(0,int(elapsed//60))
        available=indexed[stored].get("qlog",{})
        subset=[(i,obj) for i,obj in sorted(available.items()) if segment-3<=i<=segment+3]
        if subset:
            candidates[stored]=(subset,elapsed)
    return list(candidates.values())

def observe(paths, reader, target=TARGET):
    summary={
        "status":"qlog_warning_window_analysed",
        "target":"2026-11-20T02:19:00Z",
        "windowSecondsEachSide":WINDOW_SECONDS,
        "segmentsVerified":0, "segmentsWithWallClockAnchor":0,
        "samplesInWindow":0,
        "carStateSamples":0,"carControlSamples":0,
        "latActiveTrueSamples":0, "latActiveFalseSamples":0,
        "speedMpsMax":None,
        "epsSteeringTorqueMaxAbs":None,
        "steeringRateMaxAbsDegPerSecond":None,
        "events":[],
        "eventStatus":"not_observed",
        "noVehicleWrite":True,
    }
    names=Counter()
    observedEvents=[]
    for path in paths:
        messages=list(reader(path))
        headers=[m for m in messages if str(m.which())=="initData"]
        if not headers:
            return {"status":"no_source_header","segmentsVerified":summary["segmentsVerified"],
                    "noVehicleWrite":True}
        if any(str(getattr(m.initData,"gitCommit",None))!=SCHEMA_COMMIT for m in headers):
            return {"status":"unmatched_source_header","segmentsVerified":summary["segmentsVerified"],
                    "noVehicleWrite":True}
        summary["segmentsVerified"]+=1
        # Pair the recorded wall clock at initData creation with its monotonic
        # timestamp. Ignore anchors yielding a wall clock inconsistent with the
        # event window based on the first data message in this segment.
        anchors=[]
        for m in headers:
            wall_ns=num(m.initData,"wallTimeNanos")
            mono_ns=num(m,"logMonoTime")
            if wall_ns is None or mono_ns is None or wall_ns<1e17:
                continue
            anchors.append((mono_ns/1e9,wall_ns/1e9))
        if not anchors:
            continue
        first_data=next((m for m in messages if str(m.which())!="initData"),None)
        if first_data is None:continue
        origin=min(anchors,key=lambda a:abs((a[1]+(int(first_data.logMonoTime)/1e9-a[0]))-target.timestamp()))
        first_ts=origin[1]+(int(first_data.logMonoTime)/1e9-origin[0])
        if abs(first_ts-target.timestamp())>WINDOW_SECONDS+600:
            continue
        summary["segmentsWithWallClockAnchor"]+=1
        for m in messages:
            kind=str(m.which())
            if kind=="initData":continue
            offset=origin[1]+int(m.logMonoTime)/1e9-origin[0]-target.timestamp()
            if abs(offset)>WINDOW_SECONDS:continue
            summary["samplesInWindow"]+=1
            if kind=="carState":
                summary["carStateSamples"]+=1
                d=m.carState
                v=num(d,"vEgo")
                if v is not None:summary["speedMpsMax"]=round(max(summary["speedMpsMax"] or 0,abs(v)),3)
                t=num(d,"steeringTorqueEps")
                if t is not None:summary["epsSteeringTorqueMaxAbs"]=round(max(summary["epsSteeringTorqueMaxAbs"] or 0,abs(t)),3)
                rate=num(d,"steeringRateDeg")
                if rate is not None:summary["steeringRateMaxAbsDegPerSecond"]=round(max(summary["steeringRateMaxAbsDegPerSecond"] or 0,abs(rate)),3)
            elif kind=="carControl":
                summary["carControlSamples"]+=1
                if m.carControl.latActive:summary["latActiveTrueSamples"]+=1
                else:summary["latActiveFalseSamples"]+=1
            elif kind=="onroadEvents":
                for e in m.onroadEvents:
                    name=str(e.name)
                    if name in ALERTS:
                        names[name]+=1
                        observedEvents.append({
                            "secondsFromWarning":int(round(offset)),
                            "name":name,
                            "warning":bool(e.warning),
                            "softDisable":bool(e.softDisable),
                            "immediateDisable":bool(e.immediateDisable),
                        })
    if summary["segmentsWithWallClockAnchor"]==0:
        summary["status"]="no_valid_utc_wall_clock_alignment"
    summary["events"]=observedEvents[:30]
    summary["eventStatus"]="candidate_alert_present" if observedEvents else "not_observed"
    summary["eventSampleCounts"]=dict(sorted(names.items()))
    summary["clockAlignmentRequired"]=True
    return summary

def main():
    import boto3
    from botocore.config import Config
    env=os.environ
    required=("S3_BUCKET","S3_ENDPOINT","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY","COMMA_JWT")
    if any(not env.get(k) for k in required):raise RuntimeError("missing cloud credentials")
    s3=boto3.client("s3",endpoint_url=env["S3_ENDPOINT"],
        aws_access_key_id=env["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
        region_name=env.get("S3_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    objects=[o for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=env["S3_BUCKET"],Prefix="logs/") for o in page.get("Contents",())]
    devices=safe_api("https://api.comma.ai/v1/me/devices/",env["COMMA_JWT"])
    if not isinstance(devices,list):raise RuntimeError("unexpected device inventory")
    wanted=env.get("COMMA_DONGLE_ID")
    matched=[d for d in devices if isinstance(d,dict) and d.get("dongle_id")==wanted
             and d.get("is_paired") is not False]
    if len(matched)!=1:
        print("RAM_2019_QLOG "+json.dumps({"status":"device_selection_ambiguous","noVehicleWrite":True}),flush=True)
        return
    qs=urlencode({"start":int((TARGET-timedelta(hours=6)).timestamp()*1000),
                  "end":int((TARGET+timedelta(hours=1)).timestamp()*1000)})
    rows=safe_api("https://api.comma.ai/v1/devices/"+quote(wanted,safe="")+"/routes_segments?"+qs,env["COMMA_JWT"])
    if not isinstance(rows,list):raise RuntimeError("unexpected route metadata")
    matches=private_candidate(objects,rows)
    if len(matches)!=1:
        print("RAM_2019_QLOG "+json.dumps({"status":"route_absent_or_ambiguous",
                  "candidateCount":len(matches),"noVehicleWrite":True}),flush=True)
        return
    selected,elapsed=matches[0]
    if len(selected)>MAX_SEGMENTS:
        print("RAM_2019_QLOG "+json.dumps({"status":"too_many_candidate_segments","noVehicleWrite":True}),flush=True)
        return
    with tempfile.TemporaryDirectory(prefix="private-ram-incident-") as folder:
        paths=[]
        for seg,obj in selected:
            if not 0<int(obj.get("Size",0))<=MAX_COMPRESSED:
                print("RAM_2019_QLOG "+json.dumps({"status":"compressed_file_invalid","noVehicleWrite":True}),flush=True)
                return
            dest=Path(folder)/str(seg)/"qlog.zst"
            dest.parent.mkdir(parents=True,exist_ok=True)
            s3.download_file(env["S3_BUCKET"],obj["Key"],str(dest))
            if not 0<dest.stat().st_size<=MAX_COMPRESSED:
                raise ValueError("bad downloaded file size")
            paths.append(dest)
        try: outcome=observe(paths,reader_from_cereal(Path(
            env.get("PINNED_CEREAL_LOG_SCHEMA","/opt/sunnypilot/cereal/log.capnp"))))
        except Exception:
            # No potentially sensitive Cap'n Proto or S3 exception text in logs.
            outcome={"status":"reader_or_mapping_error","noVehicleWrite":True}
    outcome["segmentsSelected"]=len(selected)
    outcome["secondsAfterRouteStartEstimate"]=round(elapsed)
    print("RAM_2019_QLOG "+json.dumps(outcome,separators=(",",":")),flush=True)

if __name__=="__main__":
    main()
