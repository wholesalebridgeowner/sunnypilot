#!/usr/bin/env python3
"""Summarize a local Comma qlog.zst or rlog.zst using the pinned fork's cereal schema.

Run from repository root:
  python3 ram1500-development/analyze_route.py /path/to/0/qlog.zst /path/to/1/qlog.zst

No network calls, vehicle commands, or actuation. Input files stay local.
"""
import argparse
import collections
import json
from pathlib import Path

from openpilot.tools.lib.logreader import LogReader

IMPORTANT = {
  "steerSaturated", "steerUnavailable", "steerTempUnavailable",
  "canError", "controlsMismatch", "commIssue", "commIssueAvgFreq",
  "radarFault", "steerOverride", "steerDisengage",
}

def analyze(files):
  event_counts = collections.Counter()
  message_counts = collections.Counter()
  samples = []
  first_t = None
  end_t = None
  for path in files:
    # LogReader understands compressed Cap'n Proto logs when imported from the matching fork.
    for msg in LogReader(str(path)):
      timestamp = int(msg.logMonoTime)
      first_t = timestamp if first_t is None else min(first_t, timestamp)
      end_t = timestamp if end_t is None else max(end_t, timestamp)
      kind = str(msg.which())
      message_counts[kind] += 1
      if kind != "onroadEvents":
        continue
      for event in msg.onroadEvents:
        name = str(event.name)
        event_counts[name] += 1
        if name in IMPORTANT:
          samples.append({
            "file": path.name,
            "monoTimeNs": timestamp,
            "event": name,
            "secondsFromFirstMessage": None,  # populated below
          })
  for event in samples:
    event["secondsFromFirstMessage"] = round((event["monoTimeNs"] - first_t) / 1e9, 3)
  return {
    "files": [str(p) for p in files],
    "messages": sum(message_counts.values()),
    "messageKinds": dict(message_counts.most_common()),
    "eventSampleCounts": dict(event_counts.most_common()),
    "importantEventSamples": samples,
    "firstMonoTimeNs": first_t,
    "lastMonoTimeNs": end_t,
    "durationSeconds": round((end_t-first_t)/1e9,3) if end_t is not None else None,
    "note": "Event counts are samples, not necessarily distinct fault episodes. Times use monotonic log time."
  }

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("logs", type=Path, nargs="+")
  parser.add_argument("--output", type=Path)
  args = parser.parse_args()
  result = analyze(args.logs)
  output = json.dumps(result, indent=2)
  if args.output:
    args.output.write_text(output+"\n")
  else:
    print(output)

if __name__ == "__main__":
  main()
