#!/usr/bin/env python3
"""Pinned 1f7233cb Ram drive comfort summary; no remote access or vehicle commands.

Run from fork root with private log files:
 python3 ram1500-development/investigate_comfort.py \
    --schema-dir ~/.cache/ram1500/schema-1f7233cb \
    --out ~/ram-analysis/comfort.json \
    ~/comma-logs/000003cc--27a46b2116--*/qlog.zst

Use investigate_route.py --fetch-schema to create the pinned schema first.
Never place derived driving summaries in a public git repository.
"""
import argparse
import json
from pathlib import Path

from investigate_route import expand, inside_repo
from ramtools.comfort import metrics
from ramtools.comfort_episodes import evaluate
from ramtools.acc_motion_integrity import analyze as analyze_acc_motion
from ramtools.lane_phase_quality import analyze as analyze_lane_phases
from ramtools.logio import load_records
from ramtools.input_scope import require_one_route
from ramtools.schema import verify_schema_dir, load_log_schema

def main():
  p=argparse.ArgumentParser(description=__doc__)
  p.add_argument("logs",nargs="+")
  p.add_argument("--schema-dir",required=True,type=Path)
  p.add_argument("--out",required=True,type=Path)
  args=p.parse_args()
  if inside_repo(args.out.expanduser()):
    p.error("Private-derived output must be outside git repository")
  if any(v!="ok" for v in verify_schema_dir(args.schema_dir.expanduser()).values()):
    p.error("Schema not pinned to the recorded 1f7233cb drive build")
  try:
    paths=require_one_route(expand(args.logs))
  except ValueError as exc:
    p.error(str(exc))
  records,read_results=load_records(paths,load_log_schema(args.schema_dir.expanduser()))
  result=metrics(records)
  result["comfortEpisodes"]=evaluate(records)
  result["accMotionIntegrity"]=analyze_acc_motion(records)
  result["laneModelPhases"]=analyze_lane_phases(records)
  result["readFiles"]=len(read_results)
  result["truncatedFiles"]=sum(bool(r.error) for r in read_results)
  result["driveBuild"]="1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
  args.out.expanduser().parent.mkdir(parents=True,exist_ok=True)
  args.out.expanduser().write_text(json.dumps(result,indent=2)+"\n")
  print(f"Wrote {args.out}; valid matched cruise headway samples: {result.get('headway',{}).get('matchedCruiseSamples',0)}")

if __name__=="__main__":
  main()
