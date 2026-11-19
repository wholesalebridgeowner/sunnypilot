#!/usr/bin/env python3
"""One-shot private full-completed-route Ram ACC and steering-state reconciliation.

No device connectivity, vehicle actuation, raw CAN publication, GPS/VIN, exact
route identity, absolute timestamps or control commands. Source-pinned decoder.
"""
from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import re
import tempfile

from ramtools.cruise_state_audit import (
    ACC_MSG_ID, CAMERA_BUS, EXPECTED_SOURCE, aggregate_segments, summarize_segment,
)
from ramtools.schema import load_log_schema, verify_schema_dir
from ramtools.logio import read_log_bytes

KEY_RE=re.compile(r"^logs/[^/]+\\|([0-9a-f]{8}--[0-9a-f]{10})/([0-9]+)/rlog\\.zst$")
MAX_SEGMENTS=70
MAX_BYTES=30_000_000
STAGE="configuration"

def select_complete_route(objects,prefix):
    if re.fullmatch(r"[0-9a-f]{8}",prefix) is None:
        raise ValueError("invalid route selector")
    routes=defaultdict(dict)
    for obj in objects:
        key=obj.get("Key","")
        m=KEY_RE.fullmatch(key)
        if m is None or not m.group(1).startswith(prefix):
            continue
        seg=int(m.group(2))
        if seg>=MAX_SEGMENTS:
            raise ValueError("too many route segments")
        size=obj.get("Size",None)
        if type(size) is not int or not 0<size<=MAX_BYTES:
            raise ValueError("invalid private log size")
        if seg in routes[m.group(1)]:
            raise ValueError("duplicate file for segment")
        routes[m.group(1)][seg]=key
    if len(routes)!=1:
        raise ValueError("missing or ambiguous target route")
    chosen=next(iter(routes.values()))
    if not chosen or set(chosen)!=set(range(max(chosen)+1)):
        raise ValueError("missing contiguous route Rlogs")
    return [chosen[i] for i in sorted(chosen)]


def safe_decode_segment(path,schema):
    """Read pinned Capnp segment; only scalar fields and raw camera ACC bits."""
    data,error=read_log_bytes(path)
    if error or not data:
        raise RuntimeError("compressed segment could not be decoded completely")
    states,controls,selfdrives,das3=[],[],[],[]
    commits=[]
    seen=0
    try:
        for event in schema.Event.read_multiple_bytes(data):
            seen+=1
            which=event.which()
            t=int(event.logMonoTime)
            if t<0:
                raise ValueError("bad monotonic timestamp")
            if which=="initData":
                version=str(event.initData.gitCommit).lower().strip()
                if not version:
                    raise ValueError("missing recorded git source")
                commits.append(version)
            elif which=="carState":
                cs=event.carState
                states.append((t, bool(event.valid),bool(cs.canValid),
                               bool(cs.cruiseState.enabled),bool(cs.cruiseState.available),
                               float(cs.vEgo)))
            elif which=="carControl":
                controls.append((t,bool(event.carControl.latActive)))
            elif which=="selfdriveState":
                selfdrives.append((t,bool(event.selfdriveState.enabled)))
            elif which=="can":
                for frame in event.can:
                    if frame.src==CAMERA_BUS and frame.address==ACC_MSG_ID:
                        das3.append((t,bytes(frame.dat)))
    except Exception as exc:
        raise RuntimeError("private CAN R-log decode failed") from None
    if not commits or any(not EXPECTED_SOURCE.startswith(c) for c in commits):
        raise ValueError("recorded build does not match exact pinned source")
    if not states or not seen:
        raise ValueError("missing carState recording")
    result=summarize_segment(states,controls,selfdrives,das3)
    return result,seen,len(commits)


def main():
    global STAGE
    import boto3
    from botocore.config import Config
    e=os.environ
    require=("S3_BUCKET","S3_ENDPOINT","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY","RAM_ROUTE_PREFIX")
    if any(not e.get(x) for x in require):
        raise ValueError("missing private bucket source")
    prefix=e["RAM_ROUTE_PREFIX"]
    STAGE="schema"
    root=Path(e.get("RAM_SCHEMA_DIR","/opt/sunnypilot/cereal"))
    if any(x!="ok" for x in verify_schema_dir(root).values()):
        raise ValueError("source-pinned schema failed")
    schema=load_log_schema(root)
    s3=boto3.client("s3",endpoint_url=e["S3_ENDPOINT"],
                    aws_access_key_id=e["S3_ACCESS_KEY_ID"],
                    aws_secret_access_key=e["S3_SECRET_ACCESS_KEY"],
                    region_name=e.get("S3_REGION","auto"),
                    config=Config(s3={"addressing_style":"virtual"}))
    STAGE="inventory"
    pages=s3.get_paginator("list_objects_v2").paginate(Bucket=e["S3_BUCKET"],Prefix="logs/")
    keys=select_complete_route((x for page in pages for x in page.get("Contents",())),prefix)
    STAGE="decode"
    segments=[]
    total_messages=0
    init_pins=0
    with tempfile.TemporaryDirectory(prefix="ram-acc-private-") as tmp:
        tmpdir=Path(tmp)
        for idx,key in enumerate(keys):
            path=tmpdir/"segment.zst"
            s3.download_file(e["S3_BUCKET"],key,str(path))
            if not 0<path.stat().st_size<=MAX_BYTES:
                raise ValueError("bad downloaded R-log size")
            group,messages,pins=safe_decode_segment(path,schema)
            segments.append(group)
            total_messages+=messages
            init_pins+=pins
            path.unlink()
    STAGE="summary"
    result=aggregate_segments(segments)
    result["fullRouteProvenance"]={
        "completeRlogSegments":len(keys),
        "decodedMessages":total_messages,
        "recordedBuildPinsMatched":init_pins,
        "truncatedOrFailedFiles":0,
        "sourceWorkingTreeStillUnverified":True,
        "pinnedCommit":EXPECTED_SOURCE,
    }
    print("RAM_FULL_ROUTE_CRUISE_AUDIT "+json.dumps(result,sort_keys=True,allow_nan=False),flush=True)


if __name__=="__main__":
    try:main()
    except Exception as exc:
        # Private SDK exceptions may include endpoint/bucket/device identifiers.
        print("RAM_FULL_ROUTE_CRUISE_AUDIT "+json.dumps({"status":"failed_closed",
            "stage":STAGE,"failureCategory":type(exc).__name__}),flush=True)
        raise SystemExit(1)
