"""Read-only comfort measurements from the EXACT drive-build pinned ramtools records.

No actuator code, no remote access. Qlogs are independently decimated: every
headway pairing reports age. Measurements are observations, not ACC targets.
"""
from __future__ import annotations

from collections import Counter
import math
from statistics import median

from .timeline import Index

NS = 1e9
MAX_STATE_AGE_S = 0.15


def finite(x):
  return isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x)


def percentile(values, q):
  xs = sorted(v for v in values if finite(v))
  if not xs:
    return None
  p = (len(xs) - 1) * q
  lo = int(p)
  hi = min(len(xs)-1, lo+1)
  return round(xs[lo] + (xs[hi]-xs[lo]) * (p-lo), 3)


def metrics(records: list[dict]):
  """Analyze privately decoded records without exporting identifiers or raw logs."""
  ix = Index(sorted((r for r in records if r.get("valid") is not False),key=lambda r:r["t"]))
  first = ix.first_t()
  if first is None:
    return {"status":"no_data","headway":{},"longitudinal":{},"laneChanges":[]}
  gaps = []
  sources = Counter()
  for lead in ix.by_service.get("radarState", []):
    if lead.get("leadOne.status") is not True:
      continue
    d = lead.get("leadOne.dRel")
    if not finite(d) or not 0 < d < 300:
      continue
    state,age = ix.asof("carState",lead["t"],MAX_STATE_AGE_S)
    if state is None:
      continue
    v = state.get("vEgo")
    if not finite(v) or v <= 2:
      continue
    if state.get("cruiseState.enabled") is not True:
      continue
    if state.get("gasPressed") is True or state.get("brakePressed") is True:
      continue
    gap = d/v
    if not 0 < gap <= 20:
      continue
    sources[str(lead.get("leadOne.radar"))] += 1
    gaps.append({"seconds":round((lead["t"]-first)/NS,3),"headway":gap,
                 "vRel":lead.get("leadOne.vRel"),"ageMs":round(age*1000,1)})
  # Only carState with factory cruise active and no driver pedal, 10Hz qlogs.
  cruise = [r for r in ix.by_service.get("carState",[])
            if r.get("cruiseState.enabled") is True and r.get("gasPressed") is not True
            and r.get("brakePressed") is not True]
  accelerations=[r["aEgo"] for r in cruise if finite(r.get("aEgo"))]
  jerks=[]
  for a,b in zip(cruise,cruise[1:]):
    dt=(b["t"]-a["t"])/NS
    if .05 <= dt <= .3 and finite(a.get("aEgo")) and finite(b.get("aEgo")):
      jerks.append(abs((b["aEgo"]-a["aEgo"])/dt))
  # Lane change windows are state transitions; not a physical crossing event.
  models=ix.by_service.get("drivingModelData",[])
  intervals=[]
  if models:
    state0=None
    started=None
    direction=None
    for rec in models:
      state=str(rec.get("meta.laneChangeState") or "")
      if state!=state0:
        if started is not None:
          intervals.append((started,rec["t"],direction))
          started=None
        if state=="laneChangeStarting":
          started=rec["t"]
          direction=str(rec.get("meta.laneChangeDirection") or "")
        state0=state
    if started is not None:
      intervals.append((started,models[-1]["t"],direction))
  lane=[]
  for start,end,direction in intervals:
    observed=[s for s in ix.between("carState",start,end) if finite(s.get("steeringRateDeg"))]
    rates=[abs(s["steeringRateDeg"]) for s in observed]
    lane.append({"startSeconds":round((start-first)/NS,3),"direction":direction,
                 "durationSeconds":round((end-start)/NS,3),
                 "steeringRateSamples":len(rates),"peakAbsSteeringRateDegPerSecond":round(max(rates),3) if rates else None,
                 "medianAbsSteeringRateDegPerSecond":percentile(rates,.5)})
  cp=ix.by_service.get("carParams",[])
  owner=Counter(f"openpilotLongitudinalControl={r.get('openpilotLongitudinalControl')},pcmCruise={r.get('pcmCruise')}" for r in cp)
  controls=Counter(str(r.get("longActive")) for r in ix.by_service.get("carControl",[]))
  return {"status":"ok",
          "headway":{"matchedCruiseSamples":len(gaps),"sourceRadarFlagCounts":dict(sources),
                     "medianSeconds":percentile([x["headway"] for x in gaps],.5),
                     "p10Seconds":percentile([x["headway"] for x in gaps],.1),
                     "p90Seconds":percentile([x["headway"] for x in gaps],.9),
                     "maxPairAgeMs":max((x["ageMs"] for x in gaps),default=None)},
          "longitudinal":{"carParamsFlags":dict(owner),"controlLongActiveSamples":dict(controls),
                           "cruiseNoPedalCarStateSamples":len(cruise),
                           "medianAccelMps2":percentile(accelerations,.5),
                           "p90AbsAccelMps2":percentile([abs(x) for x in accelerations],.9),
                           "jerkSamples":len(jerks),
                           "p90AbsRawJerkMps3":percentile(jerks,.9)},
          "laneChanges":lane,
          "caveats":["This is read-only analysis on pinned-schema data; it cannot command the vehicle.",
                     "A lead reported by radarState can be camera/model-derived; reported headway is not the factory ACC target.",
                     "Raw finite-difference aEgo jerk is noise-sensitive; do not tune from this number alone.",
                     "LaneChangeStarting boundaries are planner/model states, not proof of physical lane crossing.",
                     "Do not interpret planner acceleration as direct throttle actuation without a separate vehicle control audit."]}
