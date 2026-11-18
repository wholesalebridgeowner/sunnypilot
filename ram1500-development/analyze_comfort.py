#!/usr/bin/env python3
"""Read-only Ram 1500 longitudinal and lane-change telemetry audit.

Requires a schema-compatible openpilot environment and local *ordered* qlogs/rlogs.
For the Saturday ~50-minute route:
  python3 ram1500-development/analyze_comfort.py /private/sat/*/qlog.zst \
    --output-prefix /tmp/ram-comfort
CSV is sample-level (not interpolated); JSON summarizes measured availability,
lead/time-gap observations and lane-change transitions. No vehicle commands.
"""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path

def get(obj, *keys):
  for key in keys:
    try:
      obj = getattr(obj, key)
    except (AttributeError, ValueError, TypeError):
      return None
  if isinstance(obj, (bool, int, float, str)):
    return obj
  return None

def finite(value):
  return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value)

def message_record(msg):
  typ = str(msg.which())
  p = getattr(msg,typ)
  t = int(msg.logMonoTime)/1e9
  r = {"seconds":t,"stream":typ,"valid":get(msg,"valid")}
  if typ=="carState":
    for k in ("vEgo","aEgo","gasPressed","brakePressed","gas","steeringAngleDeg","steeringRateDeg",
              "steeringTorque","steeringTorqueEps","steeringPressed"):
      r[k]=get(p,k)
    for k in ("available","enabled","speed","standstill"):
      r["cruise_"+k]=get(p,"cruiseState",k)
  elif typ=="carControl":
    r.update(longActive=get(p,"longActive"),latActive=get(p,"latActive"),
             requestedAccel=get(p,"actuators","accel"),
             requestedTorque=get(p,"actuators","torque"),
             requestedLongState=get(p,"actuators","longControlState"))
  elif typ=="carOutput":
    r.update(appliedAccel=get(p,"actuatorsOutput","accel"),
             appliedTorque=get(p,"actuatorsOutput","torque"),
             appliedTorqueCan=get(p,"actuatorsOutput","torqueOutputCan"))
  elif typ=="radarState":
    for n in ("leadOne","leadTwo"):
      r[n+"_status"]=get(p,n,"status")
      for key in ("dRel","vRel","vLead","aRel","radar","modelProb"):
        r[n+"_"+key]=get(p,n,key)
  elif typ=="longitudinalPlan":
    for key in ("hasLead","aTarget","shouldStop","allowThrottle","allowBrake","longitudinalPlanSource"):
      r["plan_"+key]=get(p,key)
    for key in ("accels","speeds","jerks"):
      try:
        v=list(getattr(p,key))
        r["plan_"+key+"_first"]=float(v[0]) if v else None
      except (AttributeError,TypeError,ValueError):
        r["plan_"+key+"_first"]=None
  elif typ=="drivingModelData":
    r["laneChangeState"]=get(p,"meta","laneChangeState")
    r["laneChangeDirection"]=get(p,"meta","laneChangeDirection")
    for key in ("leftY","rightY","leftProb","rightProb"):
      r["lane_"+key]=get(p,"laneLineMeta",key)
  elif typ=="selfdriveState":
    r["selfdrive_active"]=get(p,"active")
    r["selfdrive_enabled"]=get(p,"enabled")
  return r

STREAMS={"carState","carControl","carOutput","radarState","longitudinalPlan","drivingModelData","selfdriveState"}

def summarize(records, max_gap=.35):
  rows=sorted(records,key=lambda x:x["seconds"])
  counts=Counter(row["stream"] for row in rows)
  first=rows[0]["seconds"] if rows else None
  last=rows[-1]["seconds"] if rows else None
  last_state={}
  per_state=[]
  gaps=[]
  lane_changes=[]
  previous_lc=(None,None)
  current_transition=None
  control_long_active=Counter()
  plan_sources=Counter()
  for row in rows:
    typ=row["stream"]
    if typ=="carState":
      last_state["carState"]=row
    elif typ=="radarState":
      last_state["radarState"]=row
      lead_valid=row.get("leadOne_status") is True
      state=last_state.get("carState")
      age=row["seconds"]-state["seconds"] if state else None
      if lead_valid and state and age is not None and 0<=age<=max_gap and finite(row.get("leadOne_dRel")) and finite(state.get("vEgo")) and state["vEgo"]>2:
        gaps.append({"seconds":round(row["seconds"]-first,3),
                     "dRel_m":row["leadOne_dRel"],
                     "vEgo_mps":state["vEgo"],
                     "timeGap_seconds":row["leadOne_dRel"]/state["vEgo"],
                     "vRel_mps":row.get("leadOne_vRel"),
                     "sourceRadar":row.get("leadOne_radar")})
    elif typ=="carControl":
      control_long_active[str(row.get("longActive"))]+=1
    elif typ=="longitudinalPlan":
      plan_sources[str(row.get("plan_longitudinalPlanSource"))]+=1
    elif typ=="drivingModelData":
      state=(str(row.get("laneChangeState")),str(row.get("laneChangeDirection")))
      if state!=previous_lc:
        if current_transition:
          current_transition["endSeconds"]=round(row["seconds"]-first,3)
          lane_changes.append(current_transition)
        current_transition={"startSeconds":round(row["seconds"]-first,3),"state":state[0],"direction":state[1],"endSeconds":None}
        previous_lc=state
  if current_transition:
    current_transition["endSeconds"]=round(last-first,3)
    lane_changes.append(current_transition)
  valid_gaps=sorted(g["timeGap_seconds"] for g in gaps if finite(g["timeGap_seconds"]) and 0<=g["timeGap_seconds"]<=20)
  def percentile(values,p):
    if not values:return None
    idx=(len(values)-1)*p
    lo=int(idx);hi=min(len(values)-1,lo+1)
    return round(values[lo]*(hi-idx)+values[hi]*(idx-lo),3) if lo!=hi else round(values[lo],3)
  return {
    "messages":len(rows),"streamCounts":dict(counts),
    "durationSeconds":round(last-first,2) if first is not None else None,
    "longitudinalActiveSamples":dict(control_long_active),
    "longitudinalPlanSources":dict(plan_sources),
    "leadHeadway":{"matchedSamples":len(valid_gaps),
                   "medianSeconds":percentile(valid_gaps,.5),
                   "p10Seconds":percentile(valid_gaps,.1),
                   "p90Seconds":percentile(valid_gaps,.9),
                   "warning":"RadarState lead may be model-derived (radarUnavailable on Ram port). Time gap is distance/speed only, not a factory ACC target or a comfort score."},
    "laneChangeTransitions":lane_changes,
    "limitations":["No reliable direct throttle/brake command observed from Ram vehicle controller; cruise buttons and factory ACC can govern speed.",
                   "Plan acceleration does not prove actuator application when longActive is false.",
                   "Lane-change model state transitions alone do not quantify lateral jerk.",
                   "Timestamp-adjacent records are not interpolated; headway samples require matching carState within max_gap.",
                   "Do not interpret conditional radarState outputs as proof of physical radar sensing."]
  },gaps

def analyze(files,reader):
  records=[]
  for path in files:
    for msg in reader(str(path)):
      kind=str(msg.which())
      if kind in STREAMS:
        records.append(message_record(msg))
  summary,gaps=summarize(records)
  if records:
    start=min(x["seconds"] for x in records)
    for r in records:r["seconds"]=round(r["seconds"]-start,3)
  return {"summary":summary,"records":records,"headway":gaps}

def write_csv(path,rows):
  keys=sorted(set().union(*(r.keys() for r in rows))) if rows else ["seconds","stream"]
  with open(path,"w",newline="") as out:
    w=csv.DictWriter(out,fieldnames=keys);w.writeheader();w.writerows(rows)

def main():
  p=argparse.ArgumentParser(description=__doc__)
  p.add_argument("logs",type=Path,nargs="+")
  p.add_argument("--output-prefix",required=True,type=Path)
  args=p.parse_args()
  from openpilot.tools.lib.logreader import LogReader
  result=analyze(args.logs,LogReader)
  args.output_prefix.parent.mkdir(parents=True,exist_ok=True)
  args.output_prefix.with_suffix(".json").write_text(json.dumps(result["summary"],indent=2)+"\n")
  write_csv(args.output_prefix.with_name(args.output_prefix.name+"-samples.csv"),result["records"])
  write_csv(args.output_prefix.with_name(args.output_prefix.name+"-headway.csv"),result["headway"])
  print(json.dumps(result["summary"],indent=2))
if __name__=="__main__":
  main()
