#!/usr/bin/env python3
"""Read-only rlog torque-cap inspection pinned to actual installed build 1f7233cb.

Intended for the Nov 17 drive, segments 29/30/31:
  python ram1500-development/investigate_torque_caps.py \
    --schema-dir ~/.cache/ram1500/schema-1f7233cb \
    --out ~/ram-analysis/nov17-torque-caps.json \
    ~/private-rlogs/000003d8--72cb4c20bf--{29,30,31}/rlog.zst

No network / device access. Creates anonymized aggregate JSON outside the repository.
Use investigate_route.py --fetch-schema to obtain/verify original schema first.
"""
import argparse
import json
from pathlib import Path
from investigate_route import expand,inside_repo
from ramtools.schema import verify_schema_dir,load_log_schema
from ramtools.logio import load_records
from ramtools.torque_cap import analyze

def main():
  p=argparse.ArgumentParser(description=__doc__)
  p.add_argument("logs",nargs="+")
  p.add_argument("--schema-dir",type=Path,required=True)
  p.add_argument("--out",type=Path,required=True)
  p.add_argument("--threshold",type=float,default=.95)
  p.add_argument("--min-seconds",type=float,default=.10)
  args=p.parse_args()
  dest=args.out.expanduser()
  if inside_repo(dest):
    p.error("Output derived from private driving data must be outside git")
  verify=verify_schema_dir(args.schema_dir.expanduser())
  if any(x!="ok" for x in verify.values()):
    p.error("Use verified pinned 1f7233cb cereal schema")
  files=expand(args.logs)
  records,results=load_records(files,load_log_schema(args.schema_dir.expanduser()))
  report=analyze(records,threshold=args.threshold,min_seconds=args.min_seconds)
  report["sourceFiles"]=len(results)
  report["erroredSourceFiles"]=sum(bool(r.error) for r in results)
  report["sourceSegmentNumbers"]=sorted({r.segment for r in results if r.segment is not None})
  report["schemaCommit"]="1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
  dest.parent.mkdir(parents=True,exist_ok=True)
  dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
  print(json.dumps({"output":str(dest),"runs":report["runCountAll"],
                    "sourceFiles":report["sourceFiles"],"readErrors":report["erroredSourceFiles"]}))

if __name__=="__main__":
  main()
