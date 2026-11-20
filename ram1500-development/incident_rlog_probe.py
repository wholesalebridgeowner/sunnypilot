#!/usr/bin/env python3
"""Private, source-pinned R-log analysis of 2026-11-19 8:19:09 CST warning.

No device writes. Never prints route names, VIN, GPS, raw CAN, credentials,
or untrusted exception text. Reads only R-log segments near the warning.
"""
from __future__ import annotations
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import quote, urlencode

from auto_review import SCHEMA_COMMIT
from incident_route_inventory import KEY, TARGET
from incident_route_metadata import safe_api
from incident_qlog_probe import private_candidate, num, finite
from private_route_probe import reader_from_cereal

WARNING_SECONDS_AFTER_MINUTE = 9.18
FOCUS_SECONDS = 5.0
OUTER_SECONDS = 30.0
MAX_SEGMENTS = 5
MAX_COMPRESSED = 25_000_000

class InvalidRlog(ValueError):
    pass

def percentiles(values):
    v=sorted(abs(x) for x in values if finite(x))
    if not v:return {"count":0,"p90Abs":None,"maxAbs":None}
    i=min(len(v)-1,int((len(v)-1)*0.9))
    return {"count":len(v),"p90Abs":round(v[i],3),"maxAbs":round(v[-1],3)}

def read_rlog(paths, reader, target=TARGET):
    event=target.timestamp()+WARNING_SECONDS_AFTER_MINUTE
    points=[]
    heads=0
    clocked=0
    for path in paths:
        anchor=None
        saw_source=False
        first_data_checked=False
        for m in reader(path):
            kind=str(m.which())
            if kind=="initData":
                if str(getattr(m.initData,"gitCommit",None))!=SCHEMA_COMMIT:
                    raise InvalidRlog("source-mismatch")
                saw_source=True
                wall=num(m.initData,"wallTimeNanos")
                mono=num(m,"logMonoTime")
                if wall is not None and mono is not None and wall>=1e17:
                    anchor=(mono/1e9,wall/1e9)
                continue
            if anchor is None:
                continue
            ns=num(m,"logMonoTime")
            if ns is None:
                continue
            offset=anchor[1]+(ns/1e9-anchor[0])-event
            if not first_data_checked:
                first_data_checked=True
                if abs(offset)>OUTER_SECONDS+240:
                    anchor=None
                    continue
            if abs(offset)>OUTER_SECONDS:
                continue
            if kind=="carState":
                d=m.carState
                points.append((offset,kind,{
                    "speed":num(d,"vEgo"),
                    "eps":num(d,"steeringTorqueEps"),
                    "driver":num(d,"steeringTorque"),
                    "rate":num(d,"steeringRateDeg"),
                    "angle":num(d,"steeringAngleDeg"),
                    "steeringPressed":bool(d.steeringPressed),
                }))
            elif kind=="carControl":
                d=m.carControl
                points.append((offset,kind,{
                    "latActive":bool(d.latActive),
                    "request":num(getattr(d,"actuators",None),"torque"),
                }))
            elif kind=="carOutput":
                a=getattr(m.carOutput,"actuatorsOutput",None)
                points.append((offset,kind,{
                    "torqueOutputCan":num(a,"torqueOutputCan"),
                    "torqueOutputNormalized":num(a,"torque"),
                }))
            elif kind=="controlsState":
                state=getattr(m.controlsState,"lateralControlState",None)
                try:
                    part=getattr(state,state.which())
                    saturated=bool(getattr(part,"saturated")) if hasattr(part,"saturated") else None
                    output=num(part,"output")
                except (AttributeError,TypeError,ValueError):
                    saturated=None
                    output=None
                points.append((offset,kind,{"saturated":saturated,"controllerOutput":output}))
            elif kind=="onroadEvents":
                for ev in m.onroadEvents:
                    if str(ev.name) in ("steerSaturated","steerOverride","steerTimeLimit"):
                        points.append((offset,"alert",{
                            "name":str(ev.name),"warning":bool(ev.warning)
                        }))
        if not saw_source:raise InvalidRlog("missing-source-header")
        heads+=1
        if first_data_checked:clocked+=1

    near=[(t,k,d) for t,k,d in points if abs(t)<=FOCUS_SECONDS]
    r={}
    for kind in ("carState","carControl","carOutput","controlsState"):
        r[kind]=[d for _,k,d in near if k==kind]
    speed=percentiles(d.get("speed") for d in r["carState"])
    return {
        "status":"event_centered_private_rlog_observation" if near else "no_event_window_rlog_rows",
        "warningClock":"2026-11-20T02:19:09.18Z",
        "sourceCommit":SCHEMA_COMMIT,
        "rlogSegmentsVerified":heads,
        "rlogSegmentsWallAligned":clocked,
        "focusSecondsEachSide":FOCUS_SECONDS,
        "outerSecondsEachSide":OUTER_SECONDS,
        "recordsInOuterWindow":len(points),
        "recordsInFocusWindow":len(near),
        "samples":{
            "carState":len(r["carState"]),
            "carControl":len(r["carControl"]),
            "carOutput":len(r["carOutput"]),
            "controlsState":len(r["controlsState"]),
        },
        "latActiveTrue":sum(1 for d in r["carControl"] if d.get("latActive") is True),
        "latActiveFalse":sum(1 for d in r["carControl"] if d.get("latActive") is False),
        "controllerSaturatedTrue":sum(1 for d in r["controlsState"] if d.get("saturated") is True),
        "controllerSaturatedFalse":sum(1 for d in r["controlsState"] if d.get("saturated") is False),
        "steeringPressedTrue":sum(1 for d in r["carState"] if d.get("steeringPressed") is True),
        "normalizedRequestTorque":percentiles(d.get("request") for d in r["carControl"]),
        "nativeCommandTorqueField":percentiles(d.get("torqueOutputCan") for d in r["carOutput"]),
        "normalizedOutputTorque":percentiles(d.get("torqueOutputNormalized") for d in r["carOutput"]),
        "epsSteeringTorqueSensor":percentiles(d.get("eps") for d in r["carState"]),
        "driverSteeringTorqueSensor":percentiles(d.get("driver") for d in r["carState"]),
        "steeringRateDegPerSecond":percentiles(d.get("rate") for d in r["carState"]),
        "speedMps":speed,
        "eventsInOuterWindow":[{"offsetSeconds":round(t,2),"name":d["name"],
                 "warning":d["warning"]} for t,k,d in points if k=="alert" and d["name"]=="steerSaturated"][:10],
        "warning":"CarOutput.torqueOutputCan is a source-reported CAN-command field, not proof of physical EPS response. No vehicle controls changed.",
        "noVehicleWrite":True,
    }

def main():
    import boto3
    from botocore.config import Config
    env=os.environ
    required=("S3_BUCKET","S3_ENDPOINT","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY",
              "COMMA_JWT","COMMA_DONGLE_ID")
    if any(not env.get(k) for k in required):
        raise RuntimeError("private storage or paired-device reference missing")
    s3=boto3.client("s3",endpoint_url=env["S3_ENDPOINT"],
        aws_access_key_id=env["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
        region_name=env.get("S3_REGION","auto"),
        config=Config(s3={"addressing_style":"virtual"}))
    objects=[o for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=env["S3_BUCKET"],Prefix="logs/") for o in page.get("Contents",())]
    devices=safe_api("https://api.comma.ai/v1/me/devices/",env["COMMA_JWT"])
    matched=[d for d in devices if isinstance(d,dict) and
             d.get("dongle_id")==env["COMMA_DONGLE_ID"] and d.get("is_paired") is not False]
    if len(matched)!=1:
        print("RAM_2019_RLOG "+json.dumps({"status":"device_ambiguous","noVehicleWrite":True}),flush=True)
        return
    qs=urlencode({"start":int((TARGET-timedelta(hours=6)).timestamp()*1000),
                  "end":int((TARGET+timedelta(hours=1)).timestamp()*1000)})
    rows=safe_api("https://api.comma.ai/v1/devices/"+quote(env["COMMA_DONGLE_ID"],safe="")+
        "/routes_segments?"+qs,env["COMMA_JWT"])
    selected=private_candidate(objects,rows)
    if len(selected)!=1:
        print("RAM_2019_RLOG "+json.dumps({"status":"route_unavailable_or_ambiguous",
             "candidateCount":len(selected),"noVehicleWrite":True}),flush=True)
        return
    qlogs,elapsed=selected[0]
    route=KEY.fullmatch(qlogs[0][1]["Key"])["route"]
    wanted=max(0,int(elapsed//60))
    near=[]
    for o in objects:
        match=KEY.fullmatch(o.get("Key",""))
        if not match or match["route"]!=route or match["kind"]!="rlog":continue
        segment=int(match["segment"])
        if wanted-2<=segment<=wanted+2:
            near.append((segment,o))
    near.sort(key=lambda item:item[0])
    if not 0<len(near)<=MAX_SEGMENTS:
        print("RAM_2019_RLOG "+json.dumps({"status":"no_rlog_at_incident",
             "nearbyRlogSegments":len(near),"noVehicleWrite":True}),flush=True)
        return
    with tempfile.TemporaryDirectory(prefix="ram-private-rlog-warning-") as root:
        paths=[]
        for seg,o in near:
            if not 0<int(o.get("Size",0))<=MAX_COMPRESSED:
                raise RuntimeError("invalid compressed R-log size")
            local=Path(root)/str(seg)/"rlog.zst"
            local.parent.mkdir(parents=True,exist_ok=True)
            s3.download_file(env["S3_BUCKET"],o["Key"],str(local))
            if not 0<local.stat().st_size<=MAX_COMPRESSED:
                raise RuntimeError("invalid downloaded R-log")
            paths.append(local)
        try:
            report=read_rlog(paths,reader_from_cereal(Path(env.get(
                "PINNED_CEREAL_LOG_SCHEMA","/opt/sunnypilot/cereal/log.capnp"))))
        except Exception:
            report={"status":"source_reader_or_time_alignment_failed","noVehicleWrite":True}
    report["incidentEstimatedSegment"]=wanted
    report["rlogSegmentsSelected"]=len(near)
    report["rlogSegmentIndices"]=[n for n,_ in near]
    print("RAM_2019_RLOG "+json.dumps(report,separators=(",",":"),allow_nan=False),flush=True)

if __name__=="__main__":
    main()
