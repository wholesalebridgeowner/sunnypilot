#!/usr/bin/env python3
"""Offline Drive Advisor replay: decimated log files -> the messages the agent would have sent.

This tool holds no measure logic and no sampling logic of its own. It builds
batches from the decoded events of a log and feeds them to the same per-drive
Pipeline the agent builds (pipeline.py), so a number that differs between a
live drive and its replay can only come from the inputs.

Batches are built as for a decimated log: the events are put in time order
(file order is not time order), and every message of the eight streams goes
into the batch of the first later-or-equal message of the vehicle-state stream.

Usage (decoded with the installed build's schema; prints numbers and fixed
tokens only, never a file name, a route name, a place or a clock time):
  python3 replay_report.py --schema-dir SCHEMA_DIR qlog1.zst [qlog2.zst ...]

Printed, one JSON message per line with the drive named `replay`: the
summaries and the event records. drive_start, drive_ready and drive_end, which
need the settings of the log header, are not printed yet; `header_settings`
below reads them.
"""
from __future__ import annotations

import argparse
import math

import messages
from pipeline import STREAMS, Pipeline, PipelineConfig
from samples import event_payload
from settings_snapshot import KEYS, model_bundle_id

_TICK_STREAM = STREAMS[0]       # the stream whose every message makes a batch
_HEX12 = frozenset("0123456789abcdef")


def decimated_batches(events):
    """Batches for the pipeline from (stream, logMonoTime_ns, message) events, in any order.

    One batch per message of the tick stream, holding the newest message of
    each other stream that came after the previous batch and at or before it.
    On equal times the tick stream goes last.
    """
    wanted = frozenset(STREAMS)
    ordered = sorted((event for event in events if event[0] in wanted),
                     key=lambda event: (event[1], event[0] == _TICK_STREAM))
    batch = {}
    for stream, t_ns, msg in ordered:
        batch[stream] = (t_ns, msg)
        if stream == _TICK_STREAM:
            yield batch
            batch = {}


def replay_messages(events, config: PipelineConfig | None = None) -> list:
    """Every message the pipeline sends for these events, in the agent's envelope."""
    pipeline = Pipeline(config or PipelineConfig.for_decimated_log())
    produced = []
    for batch in decimated_batches(events):
        produced.extend(pipeline.ingest_tick(batch))
    produced.extend(pipeline.end())
    return [messages.fit(messages.envelope(kind, messages.REPLAY_DRIVE_ID, f"{messages.REPLAY_DRIVE_ID}-{i}", data))
            for i, (kind, data) in enumerate(produced, start=1)]


def _text(raw):
    if isinstance(raw, str):
        return raw
    try:
        return bytes(raw).decode("utf-8")
    except (TypeError, ValueError):
        return None


def _switch(raw):
    text = _text(raw) if raw is not None else None
    return None if text is None else text == "1"


def _float(raw):
    try:
        value = float(_text(raw))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _int(raw):
    try:
        return int(_text(raw))
    except (TypeError, ValueError):
        return None


def header_settings(entries: dict) -> dict:
    """The settings snapshot from the parameter list of a log header (name -> raw value).

    A switch is on when its value is `1`. A value that is missing or cannot be
    parsed gives null; the model bundle id is made as the agent makes it.
    """
    build = (_text(entries.get("GitCommit")) or "")[:12].lower()
    snapshot = {"lagdToggle": _switch(entries.get("LagdToggle")),
                "lagdToggleDelayS": _float(entries.get("LagdToggleDelay")),
                "nnlc": _switch(entries.get("NeuralNetworkLateralControl")),
                "laneChangeTimer": _int(entries.get("AutoLaneChangeTimer")),
                "modelBundleId": model_bundle_id(entries.get("ModelManager_ActiveBundle")),
                "build": build if len(build) == 12 and set(build) <= _HEX12 else None}
    return {key: snapshot[key] for key in KEYS}


def _log_events(paths, schema_dir, buffers: list):
    """The events of the eight streams. The decoded messages point into `buffers`, which the caller keeps."""
    import io
    import os

    import capnp
    import zstandard

    log = capnp.load(os.path.join(schema_dir, "log.capnp"), imports=[schema_dir])
    for path in paths:
        with open(path, "rb") as handle:
            data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(handle.read())).read()
        buffers.append(data)
        for event in log.Event.read_multiple_bytes(data):
            kind = event.which()
            if kind in STREAMS:
                yield kind, event.logMonoTime, event_payload(event, kind)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", required=True)
    parser.add_argument("logs", nargs="+")
    args = parser.parse_args()
    buffers = []
    for message in replay_messages(_log_events(args.logs, args.schema_dir, buffers)):
        print(messages.encode(message).decode("utf-8"))


if __name__ == "__main__":
    main()
