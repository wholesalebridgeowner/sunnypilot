#!/usr/bin/env python3
"""Offline, read-only steering comparison from analyze_route.py CSV.

Only compares values after temporal alignment. Native CAN units are NOT
physical torque, and the requested control is a dimensionless normalized input.
No vehicle actuation, networking or raw telemetry upload.

Example (after creating the CSV with analyze_route.py):
    python3 ram1500-development/steering_saturation_report.py \
       /private/ram-171.csv --output /private/ram-171-steering.json
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import csv
import json
import math
from pathlib import Path

CORE = ("carState", "carControl", "carOutput", "controlsState")
VALUES = {
    "carState": ("vEgo_mps", "steeringAngleDeg", "steeringTorque", "steeringTorqueEps", "steeringPressed"),
    "carControl": ("requestedTorque",),
    "carOutput": ("outputTorqueCan",),
    "controlsState": ("lateralError", "lateralSaturated", "actualLateralAccel", "desiredLateralAccel"),
}


def number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def flag(value):
    if value is True or str(value).lower() in ("true", "1"):
        return True
    if value is False or str(value).lower() in ("false", "0"):
        return False
    return None


def quantile(values, q):
    ordered = sorted(x for x in values if number(x) is not None)
    if not ordered:
        return None
    at = (len(ordered) - 1) * q
    low = int(math.floor(at))
    high = int(math.ceil(at))
    if low == high:
        return round(ordered[low], 4)
    return round(ordered[low] * (high-at) + ordered[high] * (at-low), 4)


def series_metric(samples, field):
    vals = [number(row.get(field)) for row in samples]
    vals = [v for v in vals if v is not None]
    return {"count": len(vals), "median": quantile(vals, .5), "p95Abs": quantile([abs(v) for v in vals], .95),
            "maxAbs": max([abs(v) for v in vals], default=None)}


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def grouped_episodes(timestamps, gap_ns=1_000_000_000):
    episodes = []
    for timestamp in sorted(timestamps):
        if episodes and timestamp - episodes[-1]["endNs"] <= gap_ns:
            episodes[-1]["endNs"] = timestamp
            episodes[-1]["samples"] += 1
        else:
            episodes.append({"startNs": timestamp, "endNs": timestamp, "samples": 1})
    return episodes


def analyze(rows, *, max_age_ms=150, steer_max_native=350, event_name="steerSaturated", pre_event_window_ms=1000):
    """Align each controller-state sample with the most recent *earlier* source data.

    Event snapshots also use only controller samples at or before event time.
    No interpolation or future sample look-ahead. Missing/stale fields remain null.
    Matching temporal validity is distinct from sensor/calibration validity.
    """
    if max_age_ms <= 0 or steer_max_native <= 0 or pre_event_window_ms <= 0:
        raise ValueError("max_age_ms, steer_max_native, and pre_event_window_ms must be positive")
    streams = {kind: [] for kind in CORE}
    event_ns = []
    rejected = Counter()
    for row in rows:
        try:
            t = int(row["monoTimeNs"])
            if t < 0:
                raise ValueError("negative monotonic time")
        except (TypeError, KeyError, ValueError, OverflowError):
            rejected["invalidTimestamp"] += 1
            continue
        if flag(row.get("valid")) is False:
            rejected["invalidMessage"] += 1
            continue
        kind = row.get("stream")
        if kind in streams:
            streams[kind].append((t, row))
        elif kind == "onroadEvents" and row.get("event") == event_name:
            event_ns.append(t)
    for kind in CORE:
        streams[kind].sort(key=lambda r: r[0])
    source_times = {kind: [r[0] for r in streams[kind]] for kind in CORE}
    samples = []
    match_counts = Counter()
    for t, source in streams["controlsState"]:
        out = {"monoTimeNs": t, "lateralSaturated": flag(source.get("lateralSaturated"))}
        for col in VALUES["controlsState"]:
            if col != "lateralSaturated":
                out[col] = number(source.get(col))
        for kind in ("carState", "carControl", "carOutput"):
            ix = bisect_right(source_times[kind], t) - 1
            matched = False
            if ix >= 0:
                tt, data = streams[kind][ix]
                age_ms = (t-tt)/1e6
                if age_ms <= max_age_ms:
                    out[kind+"AgeMs"] = round(age_ms, 3)
                    for col in VALUES[kind]:
                        out[col] = flag(data.get(col)) if col == "steeringPressed" else number(data.get(col))
                    matched = True
            if not matched:
                out[kind+"AgeMs"] = None
                for col in VALUES[kind]:
                    out[col] = None
            else:
                match_counts[kind] += 1
        rq = out.get("requestedTorque")
        cmd = out.get("outputTorqueCan")
        out["requestNativeEquivalent"] = rq * steer_max_native if rq is not None else None
        out["requestedVsCommandedGapNative"] = (rq * steer_max_native - cmd) if rq is not None and cmd is not None else None
        samples.append(out)
    partitions = {
        "allControllerStateSamples": samples,
        "controllerSaturated": [s for s in samples if s["lateralSaturated"] is True],
        "controllerNotSaturated": [s for s in samples if s["lateralSaturated"] is False],
    }
    groups = {}
    for group, member in partitions.items():
        metric_fields = ("vEgo_mps", "steeringAngleDeg", "steeringTorque", "steeringTorqueEps", "requestedTorque",
                         "outputTorqueCan", "requestNativeEquivalent", "requestedVsCommandedGapNative",
                         "lateralError", "actualLateralAccel", "desiredLateralAccel")
        groups[group] = {"samples": len(member), "metrics": {f: series_metric(member, f) for f in metric_fields},
                         "steeringPressedTrueSamples": sum(s.get("steeringPressed") is True for s in member),
                         "nearReportedCommandLimitSamples": sum(number(s.get("outputTorqueCan")) is not None
                             and abs(s["outputTorqueCan"]) >= .95 * steer_max_native for s in member)}
    episodes = grouped_episodes(event_ns)
    event_rows = []
    controller_times = source_times["controlsState"]

    def source_at_event(kind, field_name, event_time):
        """Most recent *source* sample at/before alert, with age checked at alert.

        Do not reuse a source value already up to max_age_ms old when attached
        to a controller record: the alert can occur another max_age_ms later.
        Also do not ignore a fresh source recorded between controller and alert.
        """
        ix = bisect_right(source_times[kind], event_time) - 1
        if ix < 0:
            return None, None
        source_time, source_row = streams[kind][ix]
        age_ms = (event_time - source_time) / 1e6
        return (number(source_row.get(field_name)) if age_ms <= max_age_ms else None,
                round(age_ms, 3))

    for ep in episodes:
        # A future controller record may look closer to the alert than the
        # controlling state actually available at alert time. Never use it.
        ix = bisect_right(controller_times, ep["startNs"]) - 1
        preceding = samples[ix] if ix >= 0 else None
        age_ms = (ep["startNs"] - preceding["monoTimeNs"])/1e6 if preceding is not None else None
        matched = age_ms is not None and age_ms <= max_age_ms
        # Sensor and commanded-output freshness is measured at alert time,
        # independently of the controller sample's age and sampling frequency.
        alert_output, alert_output_age_ms = source_at_event("carOutput", "outputTorqueCan", ep["startNs"])
        alert_eps, alert_eps_age_ms = source_at_event("carState", "steeringTorqueEps", ep["startNs"])
        window_ns = int(pre_event_window_ms * 1e6)
        start_ix = bisect_right(controller_times, ep["startNs"] - window_ns - 1)
        window = samples[start_ix:ix + 1] if ix >= start_ix else []
        event_rows.append({
            "eventSamples": ep["samples"], "startNs": ep["startNs"], "endNs": ep["endNs"],
            # Retain the old key for consumers; its semantics are now strictly
            # preceding-controller age (never nearest-in-absolute-time).
            "nearestControllerDelayMs": round(age_ms, 3) if age_ms is not None else None,
            "precedingControllerAgeMs": round(age_ms, 3) if age_ms is not None else None,
            "controllerMatched": matched,
            "nearestControllerSaturated": preceding["lateralSaturated"] if matched else None,
            # Legacy field names retained, but values now come from direct
            # bounded, past-only source joins at the alert timestamp.
            "nearestOutputTorqueCan": alert_output,
            "nearestSteeringTorqueEps": alert_eps,
            "outputTorqueCanAgeAtEventMs": alert_output_age_ms,
            "steeringTorqueEpsAgeAtEventMs": alert_eps_age_ms,
            "precedingWindowMs": pre_event_window_ms,
            "precedingWindowControllerSamples": len(window),
            "precedingWindowSaturatedSamples": sum(s["lateralSaturated"] is True for s in window),
            "precedingWindowNearCommandLimitSamples": sum(
                s["outputTorqueCan"] is not None
                and abs(s["outputTorqueCan"]) >= .95 * steer_max_native for s in window),
        })
    return {"inputRows": len(rows), "discardedRows": dict(rejected),
            "controllerSamples": len(samples), "sourceMatchedWithinAgeSamples": dict(match_counts),
            "eventName": event_name, "eventSampleCount": len(event_ns), "eventEpisodeCount": len(episodes),
            "events": event_rows, "groups": groups, "parameters": {"maxAgeMs": max_age_ms, "steerMaxNative": steer_max_native, "preEventWindowMs": pre_event_window_ms},
            "caveats": [
                "Read-only offline analysis. No control parameters changed.",
                "A steering-saturation notification is not proof of EPS torque saturation.",
                "requestedTorque is normalized; requestNativeEquivalent scales by *source-level* STEER_MAX for comparison with LKAS CAN command, not physical torque.",
                "outputTorqueCan and steeringTorqueEps may have different scaling/calibration; DO NOT subtract or equate them.",
                "A past-sample match within the specified age is not guaranteed to represent simultaneous ECU behavior.",
                "Near limit refers to commanded LKAS CAN units, not actual steering motor capacity.",
                "Event controller state uses the latest controller record at or before the event; steering command and EPS motor feedback are independently as-of joined at event time with freshness capped by maxAgeMs.",
                "A present sensor age with a null value means the latest prior source observation is too old or invalid; source values after the event are never used.",
                "The preceding event window counts observed controller samples; a missing sample is not interpreted as an unsaturated controller.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-age-ms", type=float, default=150)
    parser.add_argument("--steer-max-native", type=int, default=350,
                        help="Native LKAS steering command limit from verified pinned RAM_DT source (default 350)")
    parser.add_argument("--pre-event-window-ms", type=float, default=1000,
                        help="Controller context window preceding each event start (default 1000ms)")
    args = parser.parse_args()
    report = analyze(load_csv(args.input_csv), max_age_ms=args.max_age_ms, steer_max_native=args.steer_max_native,
                     pre_event_window_ms=args.pre_event_window_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "controllerSamples": report["controllerSamples"],
                      "eventSamples": report["eventSampleCount"], "eventEpisodes": report["eventEpisodeCount"]}))


if __name__ == "__main__":
    main()
