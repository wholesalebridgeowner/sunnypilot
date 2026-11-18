#!/usr/bin/env python3
"""Offline timestamp-coverage audit for analyze_route.py's focused CSV export.

Do not publish the input CSV: it contains vehicle measurements and event times.
This report contains aggregate sample counts/gaps and relative event times only.
A row is a sample, not independent evidence of a hardware response.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import csv
import json
import math
from pathlib import Path

STREAMS = ("carState", "carControl", "carOutput", "controlsState",
           "liveTorqueParameters", "selfdriveState")


def _valid(value):
    if value is True or str(value).strip().lower() in ("true", "1"):
        return True
    if value is False or str(value).strip().lower() in ("false", "0"):
        return False
    return None


def _percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return round(ordered[lo] * (hi-pos) + ordered[hi] * (pos-lo), 3)


def analyze(rows, *, max_age_ms=150.0, large_gap_ms=500.0, episode_gap_ms=1000.0):
    """Report source-stream freshness at event time, without future look-ahead.

    Assumes all input timestamps share a single logMonoTime clock and are one
    contiguous route. The upstream route-integrity check must be run first.
    """
    for value in (max_age_ms, large_gap_ms, episode_gap_ms):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("thresholds must be positive finite milliseconds")
    streams = {name: [] for name in STREAMS}
    counts = {name: Counter() for name in STREAMS}
    events = []
    rejected = Counter()
    total = 0
    for row in rows:
        total += 1
        try:
            t = int(row["monoTimeNs"])
            if t < 0:
                raise ValueError("negative time")
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected["invalidTimestamp"] += 1
            continue
        name = row.get("stream")
        valid = _valid(row.get("valid"))
        if name in streams:
            counts[name]["rows"] += 1
            if valid is False:
                counts[name]["invalid"] += 1
                continue
            if valid is None:
                counts[name]["unknownValidity"] += 1
            streams[name].append(t)
        elif name == "onroadEvents" and row.get("event") == "steerSaturated":
            if valid is False:
                rejected["invalidEvent"] += 1
            else:
                events.append(t)
    stats = {}
    for name in STREAMS:
        stamps = sorted(streams[name])
        distinct = sorted(set(stamps))
        gaps_ms = [(b-a) / 1e6 for a,b in zip(distinct, distinct[1:])]
        stats[name] = {
            "inputRows": counts[name]["rows"],
            "validityRejectedRows": counts[name]["invalid"],
            "unknownValidityRows": counts[name]["unknownValidity"],
            "usableSamples": len(stamps),
            "duplicateTimestampSamples": len(stamps) - len(distinct),
            "medianInterSampleMs": _percentile(gaps_ms, .5),
            "p95InterSampleMs": _percentile(gaps_ms, .95),
            "maxInterSampleMs": round(max(gaps_ms), 3) if gaps_ms else None,
            "gapsOverThreshold": sum(g > large_gap_ms for g in gaps_ms),
        }
        streams[name] = distinct
    events.sort()
    episodes = []
    for t in events:
        if episodes and (t-episodes[-1]["lastNs"]) / 1e6 <= episode_gap_ms:
            episodes[-1]["lastNs"] = t
            episodes[-1]["samples"] += 1
        else:
            episodes.append({"firstNs": t, "lastNs": t, "samples": 1})
    event_reports = []
    first = min((t for seq in streams.values() for t in seq), default=None)
    if events:
        first = min(events[0], first) if first is not None else events[0]
    for ep in episodes:
        t = ep["firstNs"]
        sources = {}
        for name in STREAMS:
            seq = streams[name]
            ix = bisect_right(seq, t) - 1
            age = (t - seq[ix]) / 1e6 if ix >= 0 else None
            sources[name] = {
                "precedingAgeMs": round(age, 3) if age is not None else None,
                "withinAgeThreshold": age is not None and age <= max_age_ms,
            }
        event_reports.append({
            "secondsSinceFirstIncludedSample": round((t-first)/1e9, 3),
            "eventSamplesInEpisode": ep["samples"],
            "sources": sources,
        })
    return {
        "inputRows": total,
        "discardedRows": dict(rejected),
        "thresholds": {"maxSourceAgeMs": max_age_ms, "largeGapMs": large_gap_ms,
                       "episodeGapMs": episode_gap_ms},
        "streamCoverage": stats,
        "steerSaturatedSampleCount": len(events),
        "steerSaturatedEpisodeCount": len(episodes),
        "steerSaturatedEpisodes": event_reports,
        "caveats": [
            "Read-only log quality analysis. No vehicle control or command is issued.",
            "Source freshness uses the most recent non-invalid source at or before the event; future values are never used.",
            "Unknown validity is included but counted separately and does not certify measurement correctness.",
            "Sampling gaps are measured between distinct recorded timestamps within the supplied CSV window only; this does not establish full-route continuity.",
            "The source-age threshold is a diagnostic choice, not an ECU acceptance threshold; slow streams may normally exceed it.",
            "No actuator response, EPS authorization, physical steering torque or root cause is established by timestamp coverage.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-age-ms", type=float, default=150.0)
    parser.add_argument("--large-gap-ms", type=float, default=500.0)
    args = parser.parse_args()
    with args.input_csv.open(newline="", encoding="utf-8") as f:
        report = analyze(csv.DictReader(f), max_age_ms=args.max_age_ms, large_gap_ms=args.large_gap_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "steerSaturatedEpisodes": report["steerSaturatedEpisodeCount"],
                      "inputRows": report["inputRows"]}))


if __name__ == "__main__":
    main()
