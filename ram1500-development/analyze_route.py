#!/usr/bin/env python3
"""Read-only Comma Q-/R-log analyzer. Use matching pinned cereal schema.

From repository root:
 python3 ram1500-development/analyze_route.py /private/route/{0,1,2}/qlog.zst --focus-seconds 171 --window 30 --output-prefix /tmp/ram-171
Supply all 51 ordered segments for absolute time relative to route start.
"""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

STREAMS = {"carState", "carControl", "carOutput", "controlsState", "liveTorqueParameters", "selfdriveState"}
COLUMNS = ("seconds", "monoTimeNs", "stream", "valid", "event", "vEgo_mps", "steeringAngleDeg",
           "steeringTorque", "steeringTorqueEps", "steeringPressed", "requestedSteer",
           "requestedSteeringAngleDeg", "outputSteer", "outputTorqueCan", "outputSteeringAngleDeg",
           "lateralState", "lateralActive", "lateralOutput", "lateralError",
           "lateralSaturated", "actualLateralAccel", "desiredLateralAccel",
           "desiredCurvature", "liveTorqueValid", "latAccelFactorFiltered",
           "frictionCoefficientFiltered", "selfdriveActive", "selfdriveEnabled")

def field(obj, *path):
  for key in path:
    if obj is None:
      return None
    try:
      obj = getattr(obj, key)
    except (AttributeError, TypeError, ValueError):
      return None
  return obj if isinstance(obj, (bool, int, float, str)) else None

def timeline(msg, kind):
  t = int(msg.logMonoTime)
  p = getattr(msg, kind)
  row = {"monoTimeNs": t, "stream": kind, "valid": field(msg, "valid")}
  if kind == "carState":
    for key, out in [("vEgo","vEgo_mps"),("steeringAngleDeg","steeringAngleDeg"),
                     ("steeringTorque","steeringTorque"),("steeringTorqueEps","steeringTorqueEps"),
                     ("steeringPressed","steeringPressed")]:
      row[out] = field(p,key)
  elif kind in ("carControl","carOutput"):
    prefix = "requested" if kind == "carControl" else "output"
    row[prefix+"Steer"] = field(p,"actuators","steer")
    row[prefix+"SteeringAngleDeg"] = field(p,"actuators","steeringAngleDeg")
    if kind == "carOutput":
      row["outputTorqueCan"] = field(p,"actuators","torqueOutputCan")
  elif kind == "controlsState":
    row["desiredCurvature"] = field(p, "desiredCurvature")
    try:
      kind_of_control = p.lateralControlState.which()
      state = getattr(p.lateralControlState,kind_of_control)
    except (AttributeError, TypeError, ValueError):
      kind_of_control = None
      state = None
    row["lateralState"] = kind_of_control
    for inp, out in (("active","lateralActive"),("output","lateralOutput"),
                     ("error","lateralError"),("saturated","lateralSaturated"),
                     ("actualLateralAccel","actualLateralAccel"),
                     ("desiredLateralAccel","desiredLateralAccel")):
      row[out] = field(state,inp)
  elif kind == "liveTorqueParameters":
    for inp,out in (("liveValid","liveTorqueValid"),
                    ("latAccelFactorFiltered","latAccelFactorFiltered"),
                    ("frictionCoefficientFiltered","frictionCoefficientFiltered")):
      row[out] = field(p,inp)
  elif kind == "selfdriveState":
    row["selfdriveActive"] = field(p,"active")
    row["selfdriveEnabled"] = field(p,"enabled")
  return row

def analyze(files, reader, focus_seconds=171.0, window=30.0):
  if focus_seconds < 0 or window < 0:
    raise ValueError("focus and window must be nonnegative")
  kinds, event_counts = Counter(),Counter()
  samples, rows = [],[]
  first = last = None
  for f in files:
    for msg in reader(str(f)):
      t = int(msg.logMonoTime)
      first = t if first is None else min(first,t)
      last = t if last is None else max(last,t)
      kind = str(msg.which())
      kinds[kind] += 1
      if kind == "onroadEvents":
        for event in msg.onroadEvents:
          name = str(event.name)
          event_counts[name] += 1
          samples.append((t,name))
          rows.append({"monoTimeNs":t,"stream":kind,"valid":field(msg,"valid"),"event":name})
      elif kind in STREAMS:
        rows.append(timeline(msg,kind))
  if first is None:
    raise ValueError("No messages read")
  for r in rows:
    r["seconds"] = round((r["monoTimeNs"]-first)/1e9,3)
  rows = [r for r in rows if abs(r["seconds"]-focus_seconds)<=window]
  rows.sort(key=lambda r:(r["monoTimeNs"],r["stream"]))
  episodes = defaultdict(list)
  for t,name in sorted(samples):
    previous = episodes[name][-1] if episodes[name] else None
    if previous is not None and t-previous["lastNs"]<=1_000_000_000:
      previous["lastNs"]=t
      previous["samples"]+=1
    else:
      episodes[name].append({"firstNs":t,"lastNs":t,"samples":1})
  episode_summary={name:[{"startSeconds":round((v["firstNs"]-first)/1e9,3),
                          "endSeconds":round((v["lastNs"]-first)/1e9,3),
                          "samples":v["samples"]} for v in periods]
                   for name,periods in episodes.items()}
  return {"summary":{"files":[str(f) for f in files],"messageCount":sum(kinds.values()),
                     "messageKinds":dict(kinds),"eventSampleCounts":dict(event_counts),
                     "eventEpisodes":episode_summary,"firstMonoTimeNs":first,
                     "lastMonoTimeNs":last,"durationSeconds":round((last-first)/1e9,3),
                     "focusSeconds":focus_seconds,"windowSeconds":window,
                     "interpretation":"Each CSV row is a recorded sample; no interpolation. Event episodes use 1s gap and are not unique failures. Missing values are blank. Controller output is not calibrated physical rack torque."},
          "rows":rows}

def save(result,prefix):
  prefix=Path(prefix)
  prefix.parent.mkdir(parents=True,exist_ok=True)
  with prefix.with_suffix(".json").open("w") as f:
    json.dump(result["summary"],f,indent=2)
    f.write("\n")
  with prefix.with_suffix(".csv").open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=COLUMNS,extrasaction="ignore")
    w.writeheader()
    w.writerows(result["rows"])

def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument("logs",type=Path,nargs="+")
  parser.add_argument("--focus-seconds",type=float,default=171.)
  parser.add_argument("--window",type=float,default=30.)
  parser.add_argument("--output-prefix",type=Path,required=True)
  a=parser.parse_args()
  from openpilot.tools.lib.logreader import LogReader
  result=analyze(a.logs,LogReader,a.focus_seconds,a.window)
  save(result,a.output_prefix)
  print(json.dumps({"summary":str(a.output_prefix.with_suffix(".json")),
                    "csv":str(a.output_prefix.with_suffix(".csv")),
                    "messages":result["summary"]["messageCount"],
                    "windowSamples":len(result["rows"])},indent=2))

if __name__=="__main__":
  main()
