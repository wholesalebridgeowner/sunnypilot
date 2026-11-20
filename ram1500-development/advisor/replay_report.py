#!/usr/bin/env python3
"""Offline Drive Advisor replay: Q-log files -> per-minute summaries and suggestions.

Usage (decoded with the installed build's schema; prints numbers only, never
route names, locations or identifiers):
  python3 replay_report.py --schema-dir SCHEMA_DIR qlog1.zst [qlog2.zst ...]
"""
from __future__ import annotations

import argparse
import json

from metrics import MinuteSummary
from rules import live_suggestion
from samples import SampleBuilder

SERVICES = ("carState", "carControl", "controlsState", "carOutput", "radarState",
            "drivingModelData", "onroadEvents")


def summaries_from_events(events, *, sample_hz: float = 10.0, window_s: float = 60.0) -> list:
    builder = SampleBuilder(sample_hz=sample_hz)
    summaries, current = [], None
    for service, t_ns, msg in events:
        sample = builder.ingest(service, t_ns, msg)
        if sample is None:
            continue
        if current is not None and sample.t - current.start_t >= window_s:
            summaries.append(current.finish(sample_hz))
            current = None
        if current is None:
            current = MinuteSummary(start_t=sample.t)
        current.add(sample)
    if current is not None and current.samples:
        summaries.append(current.finish(sample_hz))
    return summaries


def _qlog_events(paths, schema_dir):
    import io
    import os

    import capnp
    import zstandard

    log = capnp.load(os.path.join(schema_dir, "log.capnp"), imports=[schema_dir])
    for path in paths:
        with open(path, "rb") as handle:
            data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(handle.read())).read()
        for event in log.Event.read_multiple_bytes(data):
            kind = event.which()
            if kind in SERVICES:
                body = getattr(event, kind)
                yield kind, event.logMonoTime, (list(body) if kind == "onroadEvents" else body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", required=True)
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("qlogs", nargs="+")
    args = parser.parse_args()
    summaries = summaries_from_events(_qlog_events(args.qlogs, args.schema_dir), sample_hz=args.sample_hz)
    for i, summary in enumerate(summaries):
        summary = {k: v for k, v in summary.items() if k != "startT"}
        suggestion = live_suggestion(summaries[: i + 1], trials_this_drive=0, trial_active=False)
        print(json.dumps({"minute": i, "summary": summary, "liveSuggestion": suggestion}, sort_keys=True))


if __name__ == "__main__":
    main()
