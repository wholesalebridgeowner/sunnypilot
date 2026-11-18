#!/usr/bin/env python3
"""Summarize passive CAN capture, no DBC guesses, network or commands.

Input: local NDJSON emitted by device_telemetry.py
Output: JSON bus/message-rate checks and stream counts, no VIN/GPS/private frames.
"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path

def summarize(lines):
  frames=Counter()
  services=Counter()
  first={}
  last={}
  largest_gap={}
  previous={}
  malformed=0
  start=end=None
  for line in lines:
    try:
      row=json.loads(line)
      ts=int(row["monoTimeNs"])
      service=str(row["service"])
    except (KeyError,ValueError,TypeError,json.JSONDecodeError):
      malformed+=1
      continue
    if ts<0:
      malformed+=1
      continue
    services[service]+=1
    start=ts if start is None else min(start,ts)
    end=ts if end is None else max(end,ts)
    for packet in row.get("packets",[]):
      try:
        bus=int(packet["bus"])
        address=int(packet["id"])
        raw=bytes.fromhex(packet["dataHex"])
        if not 0<=bus<=255 or not 0<=address<=0x1fffffff or len(raw)>64:
          raise ValueError("invalid CAN frame")
      except (KeyError,ValueError,TypeError):
        malformed+=1
        continue
      key=(service,bus,address)
      frames[key]+=1
      first[key]=ts if key not in first else min(first[key],ts)
      last[key]=ts if key not in last else max(last[key],ts)
      if key in previous:
        gap=ts-previous[key]
        if gap>=0:
          largest_gap[key]=max(largest_gap.get(key,0),gap)
      previous[key]=ts
  out=[]
  for key,count in sorted(frames.items()):
    duration=(last[key]-first[key])/1e9
    out.append({"direction":key[0],"bus":key[1],"idHex":f"0x{key[2]:X}",
                "frames":count,"durationSeconds":round(duration,3),
                "averageHz":round((count-1)/duration,2) if duration>0 else None,
                "maxGapMs":round(largest_gap.get(key,0)/1e6,2) if count>1 else None})
  return {"totalDurationSeconds":round((end-start)/1e9,3) if start is not None else None,
          "serviceSampleCounts":dict(services),
          "canFrameDiagnostics":out,"malformedRecords":malformed,
          "notes":["Non-blocking subscriptions may drop samples. Rates describe observed traffic, not guaranteed bus frequencies.",
                   "CAN IDs are not decoded into physical values; verify DBC and OEM firmware before interpreting bytes.",
                   "No raw CAN frame bytes, coordinates, device identifiers, or private payloads are included in summary."]}

def main():
  ap=argparse.ArgumentParser(description=__doc__)
  ap.add_argument("captures",nargs="+",type=Path)
  ap.add_argument("--output",required=True,type=Path)
  a=ap.parse_args()
  def records():
    for path in a.captures:
      with path.open(encoding="utf8") as f:
        yield from f
  summary=summarize(records())
  a.output.parent.mkdir(parents=True,exist_ok=True)
  a.output.write_text(json.dumps(summary,indent=2)+"\n")
  print(f"wrote {a.output} ({len(summary['canFrameDiagnostics'])} observed CAN IDs)")
if __name__=="__main__":
  main()
