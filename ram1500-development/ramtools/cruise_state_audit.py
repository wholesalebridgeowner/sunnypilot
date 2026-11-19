"""Offline source-reconciled Ram ACC/LKAS evidence, read-only.

The *installed* Chrysler RAM DT CarState reads DAS_3.ACC_ACTIVE from
camera bus 2 (CAN 0x99). This module compares raw ACC status and the
CarState-derived cruiseState on the same recorded, monotonic timeline.

Source: installed Sunnypilot 1f7233cb/.../opendbc/car/chrysler/carstate.py
DAS_3 source DBC: ACC_ACTIVE 21|1@0+; ACC_AVAILABLE 20|1@0+.
This is an independent *decoder check*, not a second independent sensor
and not evidence of a throttle/brake request.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from math import isfinite

MAX_AGE_NS=150_000_000
ACC_MSG_ID=0x99
CAMERA_BUS=2
EXPECTED_SOURCE="1f7233cb983f3c13fdbba47a86f8fbc796ad9211"

def decode_das3(data):
    """Return ACC_ACTIVE, ACC_AVAILABLE from pinned DBC single-bit fields."""
    if not isinstance(data,(bytes,bytearray)) or len(data)!=8:
        return None
    return {"active":bool((data[2]>>5)&1),"available":bool((data[2]>>4)&1)}

def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value)

def _bool(value):
    return value if type(value) is bool else None

def summarize_segment(carstates,controls,selfdrives,das3):
    """Input scalar tuples, all with monotonic ns timestamps and no identifiers.

    carstates: (t,valid,canValid,cruiseEnabled,cruiseAvailable,vEgo)
    controls: (t,latActive)
    selfdrives: (t,enabled)
    das3: (t,raw-frame-eight-bytes)
    """
    rows=sorted(carstates,key=lambda x:x[0])
    controls=sorted(controls,key=lambda x:x[0])
    selfdrives=sorted(selfdrives,key=lambda x:x[0])
    das3=sorted(das3,key=lambda x:x[0])
    indexes={name:(rows,[r[0] for r in rows]) for name,rows in (
      ("control",controls),("selfdrive",selfdrives),("das3",das3))}
    def asof(name,t):
        # Each call is local to one source segment; never reuse a previous one.
        seq,times=indexes[name]
        i=bisect_right(times,t)-1
        if i<0 or not 0<=t-times[i]<=MAX_AGE_NS:
            return None
        return seq[i]
    n=Counter()
    n["carStateRows"]=len(rows)
    n["rawDAS3Frames"]=len(das3)
    n["selfdriveRows"]=len(selfdrives)
    n["carControlRows"]=len(controls)
    n["rawACCActiveFrames"]=0
    n["rawACCAvailableFrames"]=0
    n["rawMalformedDAS3Frames"]=0
    for _,raw in das3:
        decoded=decode_das3(raw)
        if decoded is None:
            n["rawMalformedDAS3Frames"]+=1
        else:
            n["rawACCActiveFrames"]+=decoded["active"]
            n["rawACCAvailableFrames"]+=decoded["available"]
    for row in rows:
        if len(row)!=6:raise ValueError("invalid carState tuple")
        t,valid,canvalid,enabled,available,speed=row
        if not isinstance(t,int) or t<0:raise ValueError("invalid monotonic timestamp")
        if _bool(enabled) is True:n["cruiseEnabledTrue"]+=1
        elif _bool(enabled) is False:n["cruiseEnabledFalse"]+=1
        else:n["cruiseEnabledUnknown"]+=1
        if _bool(available) is True:n["cruiseAvailableTrue"]+=1
        if _bool(canvalid) is True:n["canValidTrue"]+=1
        if _bool(valid) is True:n["carStateEnvelopeValidTrue"]+=1
        if _finite(speed) and speed>=14.5:n["speedAtLeast32Mph"]+=1
        cc=asof("control",t)
        if cc is not None:
            n["carControlFresh"]+=1
            if _bool(cc[1]) is True:n["latActiveTrue"]+=1
            if _bool(cc[1]) is True and _bool(enabled) is False:
                n["latActiveButCruiseReportedOff"]+=1
        sd=asof("selfdrive",t)
        if sd is not None:
            n["selfdriveFresh"]+=1
            if _bool(sd[1]) is True:n["selfdriveEnabledTrue"]+=1
        raw=asof("das3",t)
        if raw is None:
            n["rawACCStatusUnavailable"]+=1
        else:
            decoded=decode_das3(raw[1])
            if decoded is None:
                n["rawACCStatusMalformed"]+=1
            else:
                n["rawACCStatusMatched"]+=1
                if _bool(enabled) is not None:
                    n["rawVsCarStateCompared"]+=1
                    if decoded["active"] != enabled:
                        n["rawVsCarStateDisagree"]+=1
                if _bool(available) is not None:
                    n["rawAvailabilityCompared"]+=1
                    if decoded["available"] != available:
                        n["rawAvailabilityDisagree"]+=1
    return dict(n)

def aggregate_segments(segments):
    """Input sequence of complete independent summaries, no raw vehicle data."""
    total=Counter()
    groups=[]
    for i in range(0,len(segments),15):
        window=Counter()
        for s in segments[i:i+15]:
            window.update(s)
        groups.append({"windowOrdinal":i//15+1,"segmentCount":len(segments[i:i+15]),
                       "cruiseEnabledTrue":window["cruiseEnabledTrue"],
                       "cruiseEnabledFalse":window["cruiseEnabledFalse"],
                       "cruiseEnabledUnknown":window["cruiseEnabledUnknown"],
                       "latActiveTrue":window["latActiveTrue"],
                       "selfdriveEnabledTrue":window["selfdriveEnabledTrue"],
                       "rawACCActiveFrames":window["rawACCActiveFrames"],
                       "rawDAS3Frames":window["rawDAS3Frames"],
                       "rawVsCarStateCompared":window["rawVsCarStateCompared"],
                       "rawVsCarStateDisagree":window["rawVsCarStateDisagree"]})
        total.update(window)
    result={
        "status":"observational_full_route_state_reconciliation",
        "completeSegments":len(segments),
        "totals":dict(total),
        "windows":groups,
        "interpretation":{
            "rawDAS3":"CAN 0x99 bus 2 ACC_ACTIVE bit is the very input used by installed CarState; decoder cross-check, not independent factory ACC actuation evidence",
            "latActive":"Comma lateral activation is separate from the OEM adaptive cruise status",
            "cruise":"If 0 true in a complete recorded route, only that route is known to have no observed active ACC state; not the entire day",
            "routeCompleteness":"Complete R-logs present does not prove all driving that day was recorded",
            "noActuation":"No read/write to vehicle, no radar/throttle/brake/steering control or safety modifications",
        }
    }
    return result
