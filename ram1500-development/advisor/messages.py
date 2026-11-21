"""Schema v2 messages: the envelope, the wire form, the size cap of a summary
and the three drive records.

The agent and the replay tool both build their messages here, so a replay
prints what a live drive sends. A message body holds numbers, fixed tokens and
hex identifiers only: no place, no name and no clock time.

    {"v": 2, "type": <string>, "driveId": <32 hex or null>, "id": "<bootId>-<seq>", "data": {...}}
"""
from __future__ import annotations

import json

SCHEMA_VERSION = 2
SUMMARY_CAP_BYTES = 2048        # one summary on the wire, envelope included
# A summary over the cap loses log-only blocks in this order until it fits. The
# flat fields, the learned delay and the health block are never removed.
TRIM_ORDER = ("lag", "curve", "ay", "swings", "lane")
REPLAY_DRIVE_ID = "replay"
PROBE_NOT_RUN = {"result": "not_run", "helperSha": None, "probeMs": 0}      # what a replay writes


def envelope(kind: str, drive_id, event_id: str, data: dict) -> dict:
    return {"v": SCHEMA_VERSION, "type": kind, "driveId": drive_id, "id": event_id, "data": data}


def encode(event: dict) -> bytes:
    """The message as it goes out: compact JSON with sorted keys."""
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")


def fit_summary(event: dict, cap: int | None = None) -> dict:
    """The summary, made to fit the size cap.

    A summary within the cap comes back as it is. Otherwise a copy comes back
    without the first blocks of TRIM_ORDER, as many as it takes, and with
    their names in `trimmed`. If it is still too large with all five gone it
    is sent like that: nothing else is ever removed.
    """
    if cap is None:
        cap = SUMMARY_CAP_BYTES
    if len(encode(event)) <= cap:
        return event
    data = dict(event["data"])
    out = dict(event, data=data)
    trimmed = []
    for name in TRIM_ORDER:
        if name not in data:
            continue
        del data[name]
        trimmed.append(name)
        data["trimmed"] = list(trimmed)
        if len(encode(out)) <= cap:
            break
    return out


def fit(event: dict, cap: int | None = None) -> dict:
    """Any message, ready to send: a summary is held to the size cap, every other type is left alone."""
    return fit_summary(event, cap) if event.get("type") == "summary" else event


# --- the drive records ----------------------------------------------------------

def drive_start_data(*, resumed: bool, settings: dict, delay: dict, version: str, measures: list,
                     apply_enabled: bool) -> dict:
    return {"resumed": bool(resumed), "settings": settings, "delay": delay,
            "agent": {"version": version, "measures": list(measures), "applyEnabled": bool(apply_enabled)}}


def drive_ready_data(*, probe: dict, nnlc_model, nnlc_fuzzy) -> dict:
    return {"delayProbe": dict(probe), "nnlcModel": nnlc_model, "nnlcFuzzy": nnlc_fuzzy}


def drive_end_data(fields: dict, *, listening: bool, restore: str, settings: dict, delay: dict, probe) -> dict:
    """`fields` is what the pipeline knows of the drive (Pipeline.drive_end_fields()).
    `probe` is the delay probe as sent in drive_ready, or None when the drive never reached it."""
    data = dict(fields)
    totals = dict(data.get("totals") or {})
    for key in ("windowS", "unobservedS", "steeringS", "movingS"):
        totals.setdefault(key, 0.0)
    data.update({"totals": totals, "listening": bool(listening), "restore": restore, "settings": settings,
                 "delay": delay, "delayProbe": None if probe is None else dict(probe)})
    data.setdefault("windows", 0)
    data.setdefault("failed", [])
    data.setdefault("truncatedLaneChanges", 0)
    return data
