#!/usr/bin/env python3
"""Summarize local comma qlog/rlog files: message counts, event samples AND event episodes.

Run from the repository root:
  python ram1500-development/analyze_route.py --schema-dir ~/.cache/ram1500/schema-1f7233cb \
      /path/to/000003cc--27a46b2116--0/qlog.zst /path/to/000003cc--27a46b2116--1/qlog.zst ...

No network calls (unless --fetch-schema), no vehicle commands, no actuation. Input files stay local.

Changes vs. the first version (see reports/2026-11-17-claude-independent-review.md, section 5):
  * decodes with the pinned drive-build schema (--schema-dir) instead of whatever schema this branch has;
    the old LogReader path still works with --unpinned and is labelled as such in the output
  * each sample carries its segment number (every file is named qlog.zst, so the file name was ambiguous)
  * event episodes with exact durations are reported next to raw sample counts
  * truncated / unreadable segments are reported instead of aborting the whole run
"""
import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ramtools import episodes as E
from ramtools.logio import segment_of

IMPORTANT = {
  "steerSaturated", "steerUnavailable", "steerTempUnavailable", "steerTempUnavailableSilent",
  "canError", "canBusMissing", "controlsMismatch", "commIssue", "commIssueAvgFreq",
  "radarFault", "steerOverride", "steerDisengage", "steerTimeLimit", "usbError", "relayMalfunction",
}


def _iter_pinned(files, schema_dir, fetch):
  from ramtools.logio import FileReadResult, iter_file_records
  from ramtools.schema import fetch_pinned_schema, load_log_schema
  if fetch:
    fetch_pinned_schema(schema_dir)
  schema = load_log_schema(schema_dir)
  for p in files:
    res = FileReadResult(path=str(p), segment=segment_of(p)[1])
    for rec in iter_file_records(p, schema, res):
      yield rec, None
    yield None, res


def _iter_unpinned(files):
  from openpilot.tools.lib.logreader import LogReader
  for p in files:
    res = {"path": str(p), "segment": segment_of(p)[1], "messages": 0, "error": None}
    try:
      for msg in LogReader(str(p)):
        res["messages"] += 1
        kind = str(msg.which())
        rec = {"t": int(msg.logMonoTime), "service": kind, "segment": res["segment"]}
        if kind == "onroadEvents":
          rec["names"] = [str(e.name) for e in msg.onroadEvents]
        yield rec, None
    except Exception as e:
      res["error"] = f"{type(e).__name__}: {e}"[:300]
    yield None, res


def analyze(files, schema_dir=None, fetch=False, unpinned=False):
  files = sorted(files, key=lambda p: (segment_of(p)[1] if segment_of(p)[1] is not None else 10**9, str(p)))
  it = _iter_unpinned(files) if unpinned else _iter_pinned(files, schema_dir, fetch)
  message_counts = collections.Counter()
  event_counts = collections.Counter()
  event_msgs, file_results = [], []
  first_t = end_t = None
  for rec, res in it:
    if res is not None:
      file_results.append(res if isinstance(res, dict) else vars(res))
      continue
    t = rec["t"]
    first_t = t if first_t is None else min(first_t, t)
    end_t = t if end_t is None else max(end_t, t)
    message_counts[rec["service"]] += 1
    if rec["service"] == "onroadEvents":
      names = rec.get("names", [])
      event_counts.update(names)
      event_msgs.append((t, frozenset(names), rec.get("segment")))
  # pinned decoding keeps only whitelisted services; the true total comes from the per-file counts
  samples = [{"segment": seg, "monoTimeNs": t, "event": n,
              "secondsFromFirstMessage": round((t - first_t) / 1e9, 3)}
             for t, names, seg in sorted(event_msgs) for n in sorted(names) if n in IMPORTANT]
  eps = E.build_episodes([(t, names) for t, names, _ in event_msgs])
  important_eps = [e.as_dict(first_t) for name in sorted(eps) if name in IMPORTANT for e in eps[name]]
  total_messages = sum(r.get("messages", 0) for r in file_results)
  return {
    "schema": "UNPINNED (this branch's cereal)" if unpinned else f"pinned drive build ({schema_dir})",
    "files": file_results,
    "messages": total_messages,
    "messageKindsKept": dict(message_counts.most_common()),
    "eventSampleCounts": dict(event_counts.most_common()),
    "eventEpisodeSummary": E.summarize(eps),
    "importantEventEpisodes": important_eps,
    "importantEventSamples": samples,
    "onroadEventsGaps": E.message_gaps([(t, n) for t, n, _ in event_msgs]),
    "firstMonoTimeNs": first_t,
    "lastMonoTimeNs": end_t,
    "durationSeconds": round((end_t - first_t) / 1e9, 3) if end_t is not None else None,
    "note": ("Samples are onroadEvents messages (1 Hz repeats + every change), not incidents or seconds. " +
             "Episodes are reconstructed from on-change messages; duration is exact to one 10 ms frame. " +
             "Times are logMonoTime relative to the first message of the supplied files."),
  }


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("logs", type=Path, nargs="+")
  parser.add_argument("--schema-dir", type=Path, help="pinned drive-build schema directory")
  parser.add_argument("--fetch-schema", action="store_true")
  parser.add_argument("--unpinned", action="store_true", help="decode with this branch's openpilot LogReader (not pinned)")
  parser.add_argument("--output", type=Path)
  args = parser.parse_args()
  if not args.unpinned and args.schema_dir is None:
    parser.error("--schema-dir is required unless --unpinned is given")
  result = analyze(args.logs, args.schema_dir.expanduser() if args.schema_dir else None, args.fetch_schema, args.unpinned)
  output = json.dumps(result, indent=2, default=str)
  if args.output:
    args.output.write_text(output + "\n")
  else:
    print(output)


if __name__ == "__main__":
  main()
