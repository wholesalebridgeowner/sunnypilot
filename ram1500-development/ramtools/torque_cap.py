"""Find torque-cap episodes in pinned 1f7233cb drive-build logs, including NO-alert runs.

Designed for rlogs around Nov 17 segment 30. Never requests vehicle control.
Record inputs from verified pinned schema via ramtools.logio; outputs keep no
absolute timestamps, VIN, GPS coordinates or raw CAN frames.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite
from statistics import median

from .timeline import Index, joined_row

NS=1_000_000_000

def _n(value):
  try:
    if value is None or isinstance(value,bool):
      return None
    x=float(value)
    return x if isfinite(x) else None
  except (ValueError,TypeError):
    return None

def find_runs(index: Index, threshold=0.95, gap_seconds=0.05, minimum_seconds=0.10):
  """Find contiguous near-cap controlsState samples, no event alert required."""
  if not 0 < threshold <= 1 or gap_seconds <= 0 or minimum_seconds < 0:
    raise ValueError("invalid run parameters")
  items=index.by_service.get("controlsState",[])
  runs=[]
  cur=[]
  for item in items:
    out=_n(item.get("torqueState.output"))
    active=item.get("torqueState.active") is True
    eligible=active and out is not None and abs(out)>=threshold
    if eligible:
      if cur and (item["t"]-cur[-1]["t"])/NS>gap_seconds:
        runs.append(cur)
        cur=[]
      cur.append(item)
    elif cur:
      runs.append(cur)
      cur=[]
  if cur:
    runs.append(cur)
  # duration includes one typical time step, estimated from neighboring timestamps.
  return [run for run in runs if (run[-1]["t"]-run[0]["t"])/NS>=minimum_seconds]

def window_stats(index: Index, run: list[dict], before_seconds=0.5, after_seconds=0.5):
  """Compute sample-aligned controller, vehicle and motor outcomes."""
  start,end=run[0]["t"],run[-1]["t"]
  controls=index.between("controlsState",int(start-before_seconds*NS),int(end+after_seconds*NS))
  rows=[joined_row(index,r["t"],services=("controlsState","carControl","carOutput","carState","selfdriveState","liveParameters")) for r in controls]
  engaged=[r for r in rows if r.get("controlsState.torqueState.active") is True]
  cap=[r for r in engaged if _n(r.get("controlsState.torqueState.output")) is not None and abs(_n(r.get("controlsState.torqueState.output")))>=0.95]
  pairs=[]
  for r in cap:
    rq=_n(r.get("carControl.actuators.torque"))
    applied=_n(r.get("carOutput.actuatorsOutput.torqueOutputCan"))
    measured=_n(r.get("carState.steeringTorqueEps"))
    driver=_n(r.get("carState.steeringTorque"))
    active=r.get("carControl.latActive") is True
    if rq is None or applied is None:
      continue
    pairs.append({"requested":rq*350.,"applied":applied,"motor":measured,"driver":driver,"latActive":active,
                  "asofCarControlMs":r.get("carControl.dt_ms"),"asofCarOutputMs":r.get("carOutput.dt_ms"),
                  "asofCarStateMs":r.get("carState.dt_ms")})
  abs_gap=[abs(p["requested"]-p["applied"]) for p in pairs]
  abs_motor=[abs(p["motor"]) for p in pairs if p["motor"] is not None]
  abs_applied=[abs(p["applied"]) for p in pairs]
  speeds=[_n(r.get("carState.vEgo")) for r in cap]
  speeds=[v for v in speeds if v is not None]
  laterr=[abs(_n(r["controlsState.torqueState.desiredLateralAccel"])-_n(r["controlsState.torqueState.actualLateralAccel"]))
          for r in cap if _n(r.get("controlsState.torqueState.desiredLateralAccel")) is not None
          and _n(r.get("controlsState.torqueState.actualLateralAccel")) is not None]
  near_limit=sum(1 for p in pairs if abs(p["applied"])>=332.5)
  reduced=sum(1 for p in pairs if abs(p["requested"]-p["applied"])>3.5)
  return {
    "runSampleCount":len(run),"durationSecondsMeasured":round((end-start)/NS,3),
    "windowControllerSamples":len(rows),"capWindowSamples":len(cap),
    "pairedCapSamples":len(pairs),"motorFeedbackSamples":len(abs_motor),
    "medianAbsRequestedMinusAppliedCAN":round(median(abs_gap),2) if abs_gap else None,
    "maxAbsAppliedCAN":max(abs_applied) if abs_applied else None,
    "medianAbsMotorFeedbackNative":round(median(abs_motor),2) if abs_motor else None,
    "at95PercentOfCommandCeilingSamples":near_limit,
    "limitedByCarControllerProxySamples":reduced,
    "latActivePairedSamples":sum(p["latActive"] for p in pairs),
    "speedMinMaxMps":[round(min(speeds),2),round(max(speeds),2)] if speeds else None,
    "medianAbsDesiredMinusModelActualLatAccel":round(median(laterr),3) if laterr else None,
    "meanDataAgeMs":{field:round(sum(p[field] for p in pairs if p[field] is not None)/sum(p[field] is not None for p in pairs),1)
                     if any(p[field] is not None for p in pairs) else None
                     for field in ("asofCarControlMs","asofCarOutputMs","asofCarStateMs")},
    "interpretation":["Native steering command units are not Newton-meters.",
      "EPS motor feedback is NOT subtracted from LKAS command: they are distinct signals.",
      "A run at normalized controller cap is not necessarily a steerSaturated alert.",
      "As-of joining may include delay and source decimation; inspect mean age and actual per-row skew.",
      "No command or safety parameters modified."]
  }

def analyze(records,threshold=0.95,min_seconds=0.10,gap_seconds=0.05,maximum_runs=30):
  if maximum_runs<1:raise ValueError("maximum_runs must be positive")
  safe=sorted((r for r in records if r.get("valid") is not False and r.get("service") in
               ("controlsState","carControl","carOutput","carState","selfdriveState","liveParameters")),key=lambda r:r["t"])
  idx=Index(safe)
  runs=find_runs(idx,threshold,gap_seconds,min_seconds)
  runs=sorted(runs,key=lambda run:(-len(run),run[0]["t"]))[:maximum_runs]
  results=[]
  for run in runs:
    stats=window_stats(idx,run)
    stats["segment"]=run[0].get("segment")
    results.append(stats)
  return {"runCountAll":len(find_runs(idx,threshold,gap_seconds,min_seconds)),
          "returnedRuns":len(results),"runs":results,
          "thresholdNormalized":threshold,"minimumSecondsMeasured":min_seconds,
          "notes":["No raw private data or absolute route timestamps emitted.",
                   "For segment 30 only, run durations and windows at segment boundaries are censored.",
                   "Use the pinned 1f7233cb cereal schema. Qlog will undersample torque dynamics; use rlog for forensic conclusions."]}
