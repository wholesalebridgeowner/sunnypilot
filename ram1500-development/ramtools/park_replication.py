"""Independent-replication gates for observational OEM parking CAN candidates.

An apparently dramatic frame-by-frame change during one parking maneuver is
not repeat evidence. Segments MUST be separate consecutive same-gear,
same-state runs separated by real status transitions or temporal gaps. The
module neither decodes steering commands nor outputs CAN payloads.
"""
from __future__ import annotations

from collections import defaultdict
from math import isfinite

MAX_GAP_S = .30
MIN_RUN_SECONDS = 1.0
MIN_RUN_FRAMES = 10
MIN_REPLICATES = 2


def _valid(t, gear, park_flag, payload):
    return (isinstance(t,(int,float)) and not isinstance(t,bool) and isfinite(t) and t >= 0
            and gear in ("drive","reverse") and type(park_flag) is bool
            and isinstance(payload,bytes) and len(payload)>=1)


def _runs(records, *, max_gap_s=MAX_GAP_S):
    """(time_seconds, gear, park_flag, CAN core bytes); time is monotonic."""
    if not (isfinite(max_gap_s) and 0 < max_gap_s <= 5):
        raise ValueError("max_gap_s out of bounds")
    runs = []
    current = None
    previous_t = None
    for t, gear, active, payload in records:
        if not _valid(t,gear,active,payload):
            raise ValueError("invalid or sensitive observation shape")
        if previous_t is not None and t < previous_t:
            raise ValueError("events must be chronological")
        previous_t=t
        fresh = (current is None or current["gear"] != gear or current["active"] != active
                 or t-current["last"] > max_gap_s)
        if fresh:
            current={"gear":gear,"active":active,"first":t,"last":t,"samples":0,
                     "changes":0,"eligiblePairs":0,"previousPayload":None}
            runs.append(current)
        if current["previousPayload"] is not None and t > current["last"] and t-current["last"] <= max_gap_s:
            current["eligiblePairs"]+=1
            current["changes"]+=payload != current["previousPayload"]
        current["last"]=t
        current["samples"]+=1
        current["previousPayload"]=payload
    return runs


def replication_audit(records, *, min_run_seconds=MIN_RUN_SECONDS,
                      min_run_frames=MIN_RUN_FRAMES, min_replicates=MIN_REPLICATES):
    """Return rate estimates and *replication sufficiency*, never raw payloads.

    The sequence contains only frames of ONE candidate bus/ID already joined to
    authoritative EPS park state and fresh matched vehicle speed and gear.
    State transitions can be missed when no candidate frames are transmitted:
    the caller must pass a complete state transition event stream to interpret
    run count; otherwise results are exploratory only.
    """
    if not(isfinite(min_run_seconds) and 0 < min_run_seconds <= 120):
        raise ValueError("invalid duration threshold")
    if not (isinstance(min_run_frames,int) and not isinstance(min_run_frames,bool)
            and 2 <= min_run_frames <= 100000):
        raise ValueError("invalid frame threshold")
    if not (isinstance(min_replicates,int) and not isinstance(min_replicates,bool)
            and 2 <= min_replicates <= 100):
        raise ValueError("invalid replicate threshold")
    summary={}
    for run in _runs(records):
        duration=run["last"]-run["first"]
        if run["samples"]<min_run_frames or duration<min_run_seconds or run["eligiblePairs"]<min_run_frames-1:
            continue
        key=(run["gear"],run["active"])
        summary.setdefault(key,[]).append(run["changes"]/run["eligiblePairs"])
    out=[]
    for gear in ("reverse","drive"):
        inactive=summary.get((gear,False),[])
        active=summary.get((gear,True),[])
        sufficient=len(inactive)>=min_replicates and len(active)>=min_replicates
        out.append({"gear":gear,"inactiveIndependentRuns":len(inactive),
                    "activeIndependentRuns":len(active),
                    "replicated":sufficient,
                    "reason":"replicated_observational_activity_only" if sufficient else "insufficient_independent_state_runs",
                    "inactiveMeanRunChangeFraction":round(sum(inactive)/len(inactive),3) if inactive else None,
                    "activeMeanRunChangeFraction":round(sum(active)/len(active),3) if active else None})
    return {
        "status":"observational_not_an_actuator_decoder",
        "minimumIndependentRunsPerState":min_replicates,
        "minRunSeconds":min_run_seconds,
        "gearResults":out,
        "limitations":[
            "A single factory auto-parking activation cannot support a replicated controller attribution.",
            "Consecutive frames within one control episode are autocorrelated, not independent trials.",
            "Do not count a temporal gap alone as a distinct factory parking activation.",
            "Caller must supply all state transitions or the run-count gate is exploratory.",
            "A replicated candidate still does not prove ECU origin, command meaning or authorization.",
            "No raw CAN bytes, VIN, GPS or precise maneuver locations in output.",
        ],
    }
