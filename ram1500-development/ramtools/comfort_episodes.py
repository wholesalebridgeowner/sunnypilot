"""Offline following-gap and lane-change episode quality measurements.

This consumes the already privacy-minimized, pinned-schema records.
All outputs are aggregate statistics. Do not interpret predicted/model
lead distances as validated OEM radar measurements or as safe TTC targets.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite

from .timeline import Index

NS=1_000_000_000

def _num(v):
  return float(v) if isinstance(v,(float,int)) and not isinstance(v,bool) and isfinite(v) else None

def _pct(values,quantile):
  vals=sorted(v for v in values if _num(v) is not None)
  if not vals:return None
  at=(len(vals)-1)*quantile
  low=int(at)
  high=min(len(vals)-1,low+1)
  return round(vals[low]+(vals[high]-vals[low])*(at-low),3)

def _duration(rows):
  return round((rows[-1]["t"]-rows[0]["t"])/NS,3) if len(rows)>=2 else 0.0

def _segments(rows,predicate,max_gap_s=.35):
  groups=[]
  cur=[]
  for r in rows:
    accepted=predicate(r)
    if accepted:
      if cur and (r["t"]-cur[-1]["t"])/NS>max_gap_s:
        groups.append(cur)
        cur=[]
      cur.append(r)
    elif cur:
      groups.append(cur)
      cur=[]
  if cur:groups.append(cur)
  return groups

def evaluate(records, *, minimum_follow_s=2.0, min_lane_samples=3):
  """Measure following episodes and lane-change entry/exit behavior.

  Hard preconditions: cruise enabled, neither pedal pressed, ego speed > 2m/s;
  a fresh carState (<=150ms), positive model/radar distance, and closing speed.
  """
  if minimum_follow_s<0 or min_lane_samples<1:
    raise ValueError("invalid quality parameters")
  ix=Index(sorted((r for r in records if r.get("valid") is not False),key=lambda r:r["t"]))
  base=ix.first_t()
  if base is None:return {"status":"no_data","following":{},"laneChange":{}}
  lead=[]
  drop=Counter()
  # Rejected or invalid radar messages must break following episodes, even
  # when the remaining valid samples happen to be <0.55s apart.
  continuity=0
  radars=sorted((r for r in records if r.get("service")=="radarState"
                 and isinstance(r.get("t"),int)), key=lambda r:r["t"])
  for rec in radars:
    # Radar and state must have the same verified segment provenance.
    # Numbers repeat across routes; the CLI additionally enforces one route.
    if type(rec.get("segment")) is not int or rec["segment"] < 0:
      drop["radarSegmentUnknown"]+=1
      continuity+=1
      continue
    if rec.get("valid") is False:
      drop["invalidRadarMessage"]+=1
      continuity+=1
      continue
    if rec.get("leadOne.status") is not True:
      drop["leadNotValid"]+=1;continuity+=1;continue
    vehicle,age=ix.asof("carState",rec["t"],.15)
    if vehicle is None:
      drop["staleCarState"]+=1;continuity+=1;continue
    if (type(vehicle.get("segment")) is not int or
        vehicle["segment"] != rec["segment"]):
      drop["carStateSegmentMismatch"]+=1;continuity+=1;continue
    speed=_num(vehicle.get("vEgo"))
    dist=_num(rec.get("leadOne.dRel"))
    rel=_num(rec.get("leadOne.vRel"))
    if speed is None or speed<=2 or dist is None or not 0<dist<300:
      drop["invalidSpeedOrDistance"]+=1;continuity+=1;continue
    if vehicle.get("cruiseState.enabled") is not True:
      drop["cruiseInactive"]+=1;continuity+=1;continue
    if vehicle.get("gasPressed") is not False or vehicle.get("brakePressed") is not False:
      drop["driverPedalOrUnknown"]+=1;continuity+=1;continue
    lead.append({"t":rec["t"],"gap":dist/speed,"dRel":dist,"vRel":rel,
                 "modelSource":rec.get("leadOne.radar") is not True,"speed":speed,
                 "ageMs":age*1000,"continuity":continuity,
                 "segment":rec["segment"]})
  # Do not join across an excluded observation (pedal use, invalid lead,
  # stale state, missing radar data). Temporal adjacency alone is not proof
  # that factory ACC continuously followed one valid lead.
  groups=[]
  for observation in lead:
    if (not groups or observation["continuity"]!=groups[-1][-1]["continuity"] or
        observation["segment"] != groups[-1][-1]["segment"] or
        (observation["t"]-groups[-1][-1]["t"])/NS>.55):
      groups.append([])
    groups[-1].append(observation)
  long_follow=[group for group in groups if _duration(group)>=minimum_follow_s and len(group)>=3]
  # Falling below 1.5 s is merely a diagnostic bin, NOT a recommended gap target.
  follow_duration=sum(_duration(g) for g in long_follow)
  closing_ttc=[r["dRel"]/-r["vRel"] for group in long_follow for r in group
               if r["vRel"] is not None and r["vRel"] < -.5 and 0<r["dRel"]/-r["vRel"]<120]
  all_gaps=[r["gap"] for group in long_follow for r in group if 0<r["gap"]<20]
  episode_stats=[{"durationSeconds":_duration(group),
                  "medianGapSeconds":_pct([r["gap"] for r in group],.5),
                  "minimumGapSeconds":round(min(r["gap"] for r in group),3),
                  "modelLeadFraction":round(sum(x["modelSource"] for x in group)/len(group),3)}
                  for group in long_follow]
  models=ix.by_service.get("drivingModelData",[])
  starts=[]
  for i in range(1,len(models)):
    prior=str(models[i-1].get("meta.laneChangeState"))
    current=str(models[i].get("meta.laneChangeState"))
    if prior!="laneChangeStarting" and current=="laneChangeStarting":
      starts.append(models[i])
  lane=[]
  reasons=Counter()
  for start in starts:
    end_record=next((r for r in models if r["t"]>start["t"] and str(r.get("meta.laneChangeState"))!="laneChangeStarting"),None)
    if end_record is None:
      reasons["incompleteAtRouteEnd"]+=1;continue
    end=end_record["t"]
    state_records=ix.between("carState",start["t"],end)
    if len(state_records)<min_lane_samples:
      reasons["tooFewSteeringSamples"]+=1;continue
    steering_rates=[_num(r.get("steeringRateDeg")) for r in state_records]
    steering_rates=[abs(v) for v in steering_rates if v is not None]
    if len(steering_rates)<min_lane_samples:
      reasons["missingSteeringRate"]+=1;continue
    # Rate derivative is a sensitive jerk-like diagnostic, NOT vehicle lateral jerk.
    d_rates=[]
    for a,b in zip(state_records,state_records[1:]):
      x,y=_num(a.get("steeringRateDeg")),_num(b.get("steeringRateDeg"))
      dt=(b["t"]-a["t"])/NS
      if x is not None and y is not None and .03<=dt<=.2:
        d_rates.append(abs((y-x)/dt))
    lane.append({"durationSeconds":round((end-start["t"])/NS,3),
                 "direction":str(start.get("meta.laneChangeDirection")),
                 "peakSteeringRateDegPerSec":round(max(steering_rates),3),
                 "p90SteeringRateChangeDegPerSec2":_pct(d_rates,.9),
                 "steeringRateDerivativePairs":len(d_rates)})
  return {
    "status":"ok",
    "following":{"validCruiseLeadObservations":len(lead),
                 "leadFilteringRejections":dict(drop),
                 "minimumEpisodeSeconds":minimum_follow_s,
                 "qualifyingEpisodeCount":len(long_follow),
                 "qualifyingEpisodeTotalSeconds":round(follow_duration,3),
                 "medianEpisodeGapSeconds":_pct([x["medianGapSeconds"] for x in episode_stats],.5),
                 "medianObservedGapSeconds":_pct(all_gaps,.5),
                 "p10ObservedGapSeconds":_pct(all_gaps,.1),
                 "fractionObservedBelow1p5Seconds":round(sum(v<1.5 for v in all_gaps)/len(all_gaps),3) if all_gaps else None,
                 "closingTTCMedianSeconds":_pct(closing_ttc,.5),
                 "closingTTCObservations":len(closing_ttc),
                 "modelSourceEpisodeFractionMedian":_pct([x["modelLeadFraction"] for x in episode_stats],.5)},
    "laneChange":{"startingTransitions":len(starts),"qualifiedWindows":len(lane),
                  "rejectedWindows":dict(reasons),
                  "medianStartPhaseSeconds":_pct([x["durationSeconds"] for x in lane],.5),
                  "p90PeakSteeringRateDegPerSec":_pct([x["peakSteeringRateDegPerSec"] for x in lane],.9),
                  "p90RateDerivativeDegPerSec2":_pct([x["p90SteeringRateChangeDegPerSec2"] for x in lane],.9),
                  "derivedRatePairs":sum(x["steeringRateDerivativePairs"] for x in lane)},
    "caveats":["No individual event timestamps, route positions, device IDs or raw CAN frames are exported.",
      "Following measurements require same-segment radar/carState observations within a single selected route.",
      "Following measurements depend on lead-source validity and are not the factory ACC programmed gap.",
      "TTC is distance/closing-speed only; it does not account for acceleration or predict actual collision risk.",
      "An incomplete or short lane-change model phase is excluded from quality percentiles.",
      "Steering-rate derivative is NOT lateral acceleration jerk; measure IMU and model tracking before tuning.",
      "Qlogs can alias high-frequency dynamics; prefer pinned-schema Rlogs for repeatable comparison.",
      "No driving control modifications were made."]}
