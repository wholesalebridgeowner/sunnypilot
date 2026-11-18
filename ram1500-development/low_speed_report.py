#!/usr/bin/env python3
"""Passive low-speed lateral-command audit for RAM_1500_5TH_GEN.

Input is a CSV exported by analyze_route.py with enough time coverage to
include deceleration and a standstill. This NEVER commands a vehicle, connects
to a device, or claims OEM EPS authorization from the presence of CAN output.

Example:
    python3 ram1500-development/low_speed_report.py /private/route.csv \
        --output /private/low-speed.json

The default analyze_route.py focus (+171 seconds +/-30) may not include a stop.
For route-wide evidence, export with --focus-seconds 0 --window 999999.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import csv
import json
import math
from pathlib import Path

# Half-open bins in m/s. Do not label "zero steering": carState.vEgo
# measures speed, not whether a steering command reached the EPS.
SPEED_BINS = (
    # Keep reported zero separate from a slowly moving vehicle.
    ("reported_zero_mps", 0.0, 0.0),
    ("rolling_below_0p5_mps", 0.0, 0.5),
    ("crawl_0p5_to_2_mps", 0.5, 2.0),
    ("low_2_to_14p5_mps", 2.0, 14.5),
    ("at_least_14p5_mps", 14.5, math.inf),
)


def finite_number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def boolean(value):
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in ("1", "true"):
        return True
    if str(value).strip().lower() in ("0", "false"):
        return False
    return None


def speed_bin(v):
    if v is None or v < 0:
        return None
    if v == 0.0:
        return "reported_zero_mps"
    return next((name for name, lo, hi in SPEED_BINS[1:]
                 if (lo < v < hi if lo == 0.0 else lo <= v < hi)), None)


def eligible_rows(rows):
    streams = {"carState": [], "carControl": [], "carOutput": []}
    rejected = Counter()
    for row in rows:
        kind = row.get("stream")
        if kind not in streams:
            continue
        try:
            timestamp = int(row["monoTimeNs"])
            if timestamp < 0:
                raise ValueError("negative time")
        except (TypeError, ValueError, KeyError, OverflowError):
            rejected["invalidTimestamp"] += 1
            continue
        if boolean(row.get("valid")) is False:
            rejected["invalidMessage"] += 1
            continue
        streams[kind].append((timestamp, row))
    for v in streams.values():
        v.sort(key=lambda sample: sample[0])
    return streams, rejected


def newest_past(stream, timestamps, when, max_age_ns):
    """Do not attribute a future (or stale) actuation sample to a speed."""
    i = bisect_right(timestamps, when) - 1
    if i < 0:
        return None
    t, payload = stream[i]
    return payload if 0 <= when - t <= max_age_ns else None


def analyze(rows, *, max_age_ms=150.0, command_deadband=0.0):
    if not math.isfinite(max_age_ms) or max_age_ms <= 0:
        raise ValueError("max_age_ms must be a finite positive number")
    if not math.isfinite(command_deadband) or command_deadband < 0:
        raise ValueError("command_deadband must be finite and nonnegative")
    streams, rejected = eligible_rows(rows)
    times = {kind: [t for t, _ in samples] for kind, samples in streams.items()}
    age_ns = max_age_ms * 1e6
    bins = {}
    for name, _, _ in SPEED_BINS:
        bins[name] = {
            "speedSamples": 0,
            "carControlMatched": 0,
            "carControlLatActiveTrue": 0,
            "carControlLatActiveFalse": 0,
            "carControlLatActiveUnknown": 0,
            "carOutputMatched": 0,
            "nonzeroRequestedCanOutputSamples": 0,
            "activeAndNonzeroOutputSamples": 0,
            "activeAndZeroOutputSamples": 0,
            "nonzeroOutputWithInactiveLatSamples": 0,
        }
    for when, row in streams["carState"]:
        v = finite_number(row.get("vEgo_mps"))
        name = speed_bin(v)
        if name is None:
            rejected["invalidSpeed"] += 1
            continue
        stats = bins[name]
        stats["speedSamples"] += 1
        control = newest_past(streams["carControl"], times["carControl"], when, age_ns)
        output = newest_past(streams["carOutput"], times["carOutput"], when, age_ns)
        active = None
        nonzero = None
        if control is not None:
            stats["carControlMatched"] += 1
            active = boolean(control.get("carControlLatActive"))
            key = ("carControlLatActiveTrue" if active is True else
                   "carControlLatActiveFalse" if active is False else
                   "carControlLatActiveUnknown")
            stats[key] += 1
        if output is not None:
            stats["carOutputMatched"] += 1
            cmd = finite_number(output.get("outputTorqueCan"))
            if cmd is not None:
                nonzero = abs(cmd) > command_deadband
                if nonzero:
                    stats["nonzeroRequestedCanOutputSamples"] += 1
        if active is True and nonzero is True:
            stats["activeAndNonzeroOutputSamples"] += 1
        if active is True and nonzero is False:
            stats["activeAndZeroOutputSamples"] += 1
        if active is False and nonzero is True:
            stats["nonzeroOutputWithInactiveLatSamples"] += 1
    return {
        "source": "read-only analyze_route.py CSV (no raw telemetry included)",
        "parameters": {"maxAgeMs": max_age_ms, "commandDeadbandNative": command_deadband},
        "rejectedRows": dict(rejected),
        "speedBinsMps": {
            "reported_zero_mps": {"equalTo": 0.0},
            "rolling_below_0p5_mps": {"greaterThan": 0.0, "lessThan": 0.5},
            "crawl_0p5_to_2_mps": {"lowerInclusive": 0.5, "upperExclusive": 2.0},
            "low_2_to_14p5_mps": {"lowerInclusive": 2.0, "upperExclusive": 14.5},
            "at_least_14p5_mps": {"lowerInclusive": 14.5, "upperExclusive": None},
        },
        "bins": bins,
        "caveats": [
            "Counts are recorded carState samples, not seconds, independent maneuvers, or a proof of zero-speed engagement.",
            "reported_zero_mps means vEgo was exactly 0.0 in the decoded record; it does not prove absolute physical immobility or EPS acceptance.",
            "Only at-or-before carControl/carOutput records no older than maxAgeMs can be associated with vehicle speed.",
            "carControl.latActive is the internal requested lateral-active flag, not proof an LKAS control bit was accepted.",
            "carOutput.actuatorsOutput.torqueOutputCan is requested native CAN output, NOT measured EPS actuation.",
            "EPS steering feedback, wheel movement, and factory self-parking authorization are NOT established by this audit.",
            "Blank/unmatched data remain unknown and are never treated as zero or an inactive state.",
            "Nominal engagement threshold, EPS firmware identity and installed control implementation require separate evidence.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-age-ms", type=float, default=150.0)
    parser.add_argument("--command-deadband", type=float, default=0.0)
    args = parser.parse_args()
    with args.input_csv.open(newline="", encoding="utf-8") as fp:
        result = analyze(csv.DictReader(fp), max_age_ms=args.max_age_ms,
                         command_deadband=args.command_deadband)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output),
                      "speedSamples": sum(x["speedSamples"] for x in result["bins"].values()),
                      "nonzeroOutputSamples": sum(x["nonzeroRequestedCanOutputSamples"] for x in result["bins"].values())}))


if __name__ == "__main__":
    main()
