"""Read-only steering-wheel reversal proxy under Comma lateral control.

This is NOT a lane-center oscillation detector or an optimizer. It measures
sign-reversing steering-wheel rate while verified Comma lateral control is
active, curvature request is small, driver steering input is absent, and
vehicle CAN and actuator readings are fresh. True lane error/clearance,
road bank and correct commanded curvature are not independently observed.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from math import isfinite

SOURCE_AGE_NS=120_000_000
MAX_DT_NS=100_000_000
MIN_SPEED_MPS=12.0
MAX_SPEED_MPS=43.0
MAX_STRAIGHT_CURVATURE=0.00065
MAX_NATIVE_OUTPUT=300.0
RATE_SIGN_THRESHOLD_DEG_S=1.5
MIN_SIGN_DWELL_NS=250_000_000


def _num(x):
    return float(x) if isinstance(x,(int,float)) and not isinstance(x,bool) and isfinite(x) else None


def analyze_segment(state,controls,curvature,outputs):
    """state rows: t,valid,canValid,gear,vEgo,steeringRate,steeringPressed,steerFault
       controls rows: t,latActive; curvature: t,desiredCurvature;
       outputs: t,torqueOutputCan. No raw CAN bytes or absolute clocks emitted.
    """
    sources={}
    for name,rows in (("controls",controls),("curvature",curvature),("outputs",outputs)):
        sorted_rows=sorted(rows,key=lambda r:r[0])
        sources[name]=(sorted_rows,[r[0] for r in sorted_rows])
    def asof(name,t):
        rows,times=sources[name]
        idx=bisect_right(times,t)-1
        if idx<0 or not 0<=t-times[idx]<=SOURCE_AGE_NS:return None
        return rows[idx][1]
    counts=Counter()
    times=[]
    rates=[]
    last=None
    streak_sign=0
    streak_start=None
    strong_last_sign=0
    reversals=0
    elapsed_ns=0
    for row in sorted(state,key=lambda r:r[0]):
        if len(row)!=8:
            raise ValueError("invalid scalar vehicle observation")
        t,valid,canvalid,gear,speed,rate,pressed,fault=row
        if not isinstance(t,int) or t<0:raise ValueError("invalid timestamp")
        speed,rate=_num(speed),_num(rate)
        cc=asof("controls",t)
        desired=_num(asof("curvature",t))
        native=_num(asof("outputs",t))
        if valid is not True or canvalid is not True:
            reason="vehicle_or_can_invalid"
        elif gear!="drive" or speed is None or not MIN_SPEED_MPS<=speed<=MAX_SPEED_MPS:
            reason="speed_or_gear_excluded"
        elif pressed is not False or fault is not False:
            reason="driver_or_fault"
        elif cc is not True:
            reason="lateral_not_verified"
        elif desired is None or abs(desired)>MAX_STRAIGHT_CURVATURE:
            reason="curvature_not_straight"
        elif native is None or abs(native)>=MAX_NATIVE_OUTPUT:
            reason="torque_limit_or_unavailable"
        elif rate is None:
            reason="steering_rate_invalid"
        else:
            reason=None
        if reason:
            counts[reason]+=1
            last=None
            streak_sign=0
            streak_start=None
            strong_last_sign=0
            continue
        counts["eligibleSamples"]+=1
        rates.append(abs(rate))
        if last is not None:
            dt=t-last
            if 0<dt<=MAX_DT_NS:
                elapsed_ns+=dt
            else:
                streak_sign=0
                streak_start=None
                strong_last_sign=0
        else:
            streak_sign=0
            streak_start=None
            strong_last_sign=0
        last=t
        sign=1 if rate>=RATE_SIGN_THRESHOLD_DEG_S else -1 if rate<=-RATE_SIGN_THRESHOLD_DEG_S else 0
        if sign==0:
            # A short neutral interval does not itself make a new oscillation.
            continue
        if sign!=streak_sign:
            streak_sign=sign
            streak_start=t
        if streak_start is not None and t-streak_start>=MIN_SIGN_DWELL_NS and sign!=strong_last_sign:
            if strong_last_sign and sign==-strong_last_sign:
                reversals+=1
            strong_last_sign=sign
    ordered=sorted(rates)
    idx=int((len(ordered)-1)*0.95) if ordered else 0
    return {
      "eligibleSamples":counts["eligibleSamples"],
      "eligibleContinuousSeconds":round(elapsed_ns/1e9,3),
      "stableSignReversals":reversals,
      "reversalsPerMinuteProxy":round(reversals/(elapsed_ns/60e9),2) if elapsed_ns>0 else None,
      "p95AbsSteeringWheelRateDegPerSec":round(ordered[idx],2) if ordered else None,
      "rejections":{k:v for k,v in counts.items() if k!="eligibleSamples"},
      "limitations":[
        "Steering-wheel direction changes are NOT proven lane-center ping-pong or road safety performance.",
        "Curvature is the controller desired estimate; actual lane boundary tracking is unobserved.",
        "Driver torque and EPS limiting filters may exclude valid behavior; report coverage.",
        "No tuning value, physical actuator latency estimate or on-device control change is implied.",
      ]
    }


def combine(summaries):
    result=Counter()
    rejects=Counter()
    for r in summaries:
        result["eligibleSamples"]+=r["eligibleSamples"]
        result["eligibleContinuousMillis"]+=round(r["eligibleContinuousSeconds"]*1000)
        result["stableSignReversals"]+=r["stableSignReversals"]
        rejects.update(r["rejections"])
    seconds=result["eligibleContinuousMillis"]/1000
    return {"status":"steering_reversal_proxy_observational_only",
            "segments":len(summaries),
            "eligibleSamples":result["eligibleSamples"],
            "eligibleContinuousSeconds":round(seconds,3),
            "stableSignReversals":result["stableSignReversals"],
            "reversalsPerMinuteProxy":round(result["stableSignReversals"]/(seconds/60),2) if seconds else None,
            "rejections":dict(rejects),
            "noVehicleWrite":True,
            "limitations":[
              "Metric cannot determine steering calibration optimum, physical lane-center error or true ping-pong.",
              "Summed valid time excludes invalid/driver-override and torque-limited data, not complete driving exposure.",
              "No parameter release without independent held-out routes, stability/override safety checks and owner approval.",
            ]}
