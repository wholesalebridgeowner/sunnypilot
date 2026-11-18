"""Read-only matched-speed/gear CAN *activity* audit for RAM factory park assist.

Candidate IDs are not decoded as steering commands. Contrast bytes 0..N-3
between EPS_AUTO_PARK_HAS_CONTROL_2 states, conditioned on the same reported
gear and low-speed band, with 150 ms past-only temporal joins. Excludes two
trailing bytes often used for counters/checksum; CAN payload never leaves RAM
or aggregate output. Insufficient comparisons explicitly stay unknown.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from math import isfinite

MAX_SOURCE_AGE_NS=150_000_000
MAX_CANDIDATE_GAP_NS=300_000_000


def _asof(t,stamps,rows):
    i=bisect_right(stamps,t)-1
    if i<0 or t<stamps[i] or t-stamps[i]>MAX_SOURCE_AGE_NS:
        return None
    return rows[i]


def inspect_activity(frames,car_states,eps_flags,*,min_frames=30):
    """All inputs contain only a monotonic timestamp and privacy-minimized data.

    frames: (t_ns,stream,bus,address,data), vehicle: (t_ns,speed_mps,gear),
    flags: (t_ns,bool).
    """
    if min_frames<2:
        raise ValueError("min_frames must be >=2")
    cars=sorted(car_states,key=lambda x:x[0])
    flags=sorted(eps_flags,key=lambda x:x[0])
    ts_car=[x[0] for x in cars]
    ts_flag=[x[0] for x in flags]
    groups=defaultdict(list)
    counts=Counter()
    for t,svc,bus,ident,raw in sorted(frames,key=lambda x:x[0]):
        if svc!="can":
            continue
        car=_asof(t,ts_car,cars)
        flag=_asof(t,ts_flag,flags)
        if car is None or flag is None:
            counts["missingFreshStateOrEPS"]+=1
            continue
        speed,gear=car[1],car[2]
        if not isinstance(speed,(int,float)) or isinstance(speed,bool) or not isfinite(speed) or not -.1<=speed<=.8:
            counts["notLowSpeed"]+=1
            continue
        if gear not in ("drive","reverse"):
            counts["unsupportedGear"]+=1
            continue
        if bus>=128:
            counts["harnessForwardedExcluded"]+=1
            continue
        if int(bus)==0 and int(ident)==0x31:
            # This message contains the EPS status flag defining our comparison.
            # Ranking it as an external PAM candidate would be circular.
            counts["epsStatusExcluded"]+=1
            continue
        payload=bytes(raw)
        if len(payload)<3 or len(payload)>64:
            counts["invalidPayload"]+=1
            continue
        # Last two bytes removed to prevent check/counter changes dominating.
        # We do not publish any byte, bit position, or candidate steering command.
        key=(gear,str(bool(flag[1])),int(bus),int(ident))
        groups[key].append((int(t),payload[:-2]))
    ranked=[]
    for gear in ("reverse","drive"):
        ids={(b,i) for (g,_,b,i) in groups if g==gear}
        for bus,ident in sorted(ids):
            a=groups.get((gear,"False",bus,ident),[])
            b=groups.get((gear,"True",bus,ident),[])
            if min(len(a),len(b))<min_frames:
                counts["insufficientMatchedFrames"]+=1
                continue
            def changes(group):
                diff=0;eligible=0;prev=None
                for t,raw in group:
                    if prev is not None and 0<t-prev[0]<=MAX_CANDIDATE_GAP_NS and len(prev[1])==len(raw):
                        eligible+=1
                        diff+=(prev[1]!=raw)
                    prev=(t,raw)
                return diff,eligible
            d0,n0=changes(a);d1,n1=changes(b)
            if min(n0,n1)<min_frames-1:
                counts["insufficientAdjacentPairs"]+=1
                continue
            p0=d0/n0;p1=d1/n1
            ranked.append({"gear":gear,"bus":bus,"idHex":f"0x{ident:X}",
                           "inactiveMatchedFrames":len(a),"activeMatchedFrames":len(b),
                           "inactivePayloadChangeFraction":round(p0,3),
                           "activePayloadChangeFraction":round(p1,3),
                           "changeFractionDelta":round(p1-p0,3)})
    ranked.sort(key=lambda x:(-abs(x["changeFractionDelta"]),
                              -min(x["activeMatchedFrames"],x["inactiveMatchedFrames"]),x["gear"],x["bus"],x["idHex"]))
    return {
      "status":"matched_observational_CAN_activity_only",
      "candidateCount":len(ranked),
      "candidates":ranked[:30],
      "filteredCounts":dict(counts),
      "minFramesPerState":min_frames,
      "limitations":[
        "The control bit is reported by EPS; candidate CAN IDs are NOT decoded as actuator commands.",
        "Vehicle gear and speed are matched, but steering load, sensors, and ECU state can still confound.",
        "Source inputs must be at-or-before and no older than 150ms. Thin baseline yields no comparison.",
        "Forwarded harness frames and the EPS status frame itself are excluded to avoid double counting and circular inference.",
        "Payload bytes, bit offsets, VIN, location and per-frame data are never emitted.",
        "Two tail bytes excluded to reduce checksum/counter confounding; this can hide actual signal changes.",
        "No CAN injection, ECU programming or authorization bypass.",
      ]
    }
