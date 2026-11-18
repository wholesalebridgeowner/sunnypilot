#!/usr/bin/env python3
"""Offline, causal, read-only RAM_DT steering-command limit replay.

Reconstruct one candidate command from source-level RAM_DT limits for changed
carOutput samples using only earlier/at-time carControl, carState and carOutput.
The capture does NOT expose LKAS-enable latch, controller STEER_STEP phase, or
physical EPS acceptance. An unmatched candidate is not proof of a defect.

Constants are from sunnypilot/opendbc@df807f8 (historical source review),
not independently confirmed from the installed vehicle's runtime.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import csv
import json
import math
from pathlib import Path

STEER_MAX = 350
STEER_ERROR_MAX = 80
STEER_DELTA_UP = 6
STEER_DELTA_DOWN = 6


def number(x):
    if x is None or x == "" or isinstance(x, bool):
        return None
    try:
        n = float(x)
    except (ValueError, TypeError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def boolean(x):
    if x is True or x is False:
        return x
    if str(x).strip().lower() in ("true", "1"):
        return True
    if str(x).strip().lower() in ("false", "0"):
        return False
    return None


def clip(x, low, high):
    return max(low, min(high, x))


def predict_command(request, previous, eps):
    """Mirror historical apply_meas_steer_torque_limits in native software units.

    Return intermediate classifications for the measured-torque bound and the
    subsequent per-update rate bound. Hypothetical ACTIVE LKAS update only.
    """
    vals = [number(request), number(previous), number(eps)]
    if any(v is None for v in vals):
        raise ValueError("invalid replay sample")
    request, previous, eps = vals
    if not (-1.0 <= request <= 1.0 and abs(previous) <= STEER_MAX and abs(eps) <= 20000):
        raise ValueError("out-of-range replay sample")
    target = int(round(request * STEER_MAX))
    meas_high = min(max(eps + STEER_ERROR_MAX, STEER_ERROR_MAX), STEER_MAX)
    meas_low = max(min(eps - STEER_ERROR_MAX, -STEER_ERROR_MAX), -STEER_MAX)
    measured = clip(target, meas_low, meas_high)
    if previous > 0:
        rate_low = max(previous - STEER_DELTA_DOWN, -STEER_DELTA_UP)
        rate_high = previous + STEER_DELTA_UP
    else:
        rate_low = previous - STEER_DELTA_UP
        rate_high = min(previous + STEER_DELTA_DOWN, STEER_DELTA_UP)
    candidate = int(round(clip(measured, rate_low, rate_high)))
    return {
        "candidateNative": candidate,
        "targetNative": target,
        "measurementClipped": measured != target,
        "rateClipped": clip(measured, rate_low, rate_high) != measured,
        "measLow": meas_low,
        "measHigh": meas_high,
    }


def analyze(rows, *, start=1800.0, end=1807.0, max_source_age_ms=80.0,
            max_output_interval_ms=40.0, near_normalized=0.995,
            match_tolerance_native=1.0):
    """Aggregate only changed carOutput samples (not 100-Hz held observations).

    Most recent at-or-before carControl and carState are bounded by freshness;
    the prior carOutput observation must be strictly earlier. Never output
    timestamps, route keys, private traces, VIN, or raw CAN frames.
    """
    params = (start, end, max_source_age_ms, max_output_interval_ms,
              near_normalized, match_tolerance_native)
    if any(number(v) is None for v in params) or not (0 <= start <= end and
            0 < max_source_age_ms <= 500 and 0 < max_output_interval_ms <= 500 and
            0 < near_normalized <= 1 and 0 <= match_tolerance_native <= 10):
        raise ValueError("invalid replay parameters")
    streams = {k: [] for k in ("carState", "carControl", "carOutput")}
    rejected = Counter()
    for row in rows:
        kind = row.get("stream")
        if kind not in streams:
            continue
        try:
            stamp = int(row["monoTimeNs"])
            if stamp < 0:
                raise ValueError("negative timestamp")
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected["badTimestamp"] += 1
            continue
        if boolean(row.get("valid")) is False:
            rejected["invalidMessage"] += 1
            continue
        if boolean(row.get("valid")) is None:
            rejected["unknownValidity"] += 1
        seconds = number(row.get("seconds"))
        if seconds is None or not (start - max_source_age_ms / 1000 <= seconds <= end):
            continue
        streams[kind].append((stamp, row))
    for stream in streams.values():
        stream.sort(key=lambda item: item[0])
    times = {k: [t for t, _ in stream] for k, stream in streams.items()}
    counts = Counter()
    max_age_ns = max_source_age_ms * 1e6
    max_output_gap_ns = max_output_interval_ms * 1e6

    def prior(kind, at):
        ix = bisect_right(times[kind], at) - 1
        if ix < 0:
            return None
        stamp, row = streams[kind][ix]
        return row if 0 <= at - stamp <= max_age_ns else None

    outputs = streams["carOutput"]
    for i, (t, row) in enumerate(outputs):
        second = number(row.get("seconds"))
        if second is None or not start <= second <= end:
            continue
        counts["outputSamples"] += 1
        observed = number(row.get("outputTorqueCan"))
        if observed is None or abs(observed) > STEER_MAX:
            counts["invalidNativeOutput"] += 1
            continue
        if i == 0 or not 0 < t - outputs[i - 1][0] <= max_output_gap_ns:
            counts["missingFreshPreviousOutput"] += 1
            continue
        prev = number(outputs[i - 1][1].get("outputTorqueCan"))
        if prev is None or abs(prev) > STEER_MAX:
            counts["missingFreshPreviousOutput"] += 1
            continue
        if observed == prev:
            counts["heldOutputSamples"] += 1
            continue
        counts["changedOutputSamples"] += 1
        cc = prior("carControl", t)
        cs = prior("carState", t)
        if cc is None or cs is None:
            counts["missingFreshSources"] += 1
            continue
        if boolean(cc.get("carControlLatActive")) is not True:
            counts["inactiveOrUnknownRequest"] += 1
            continue
        counts["activeChangedOutputSamples"] += 1
        requested = number(cc.get("requestedTorque"))
        motor = number(cs.get("steeringTorqueEps"))
        if requested is None or motor is None or abs(requested) > 1.00001:
            counts["invalidRequestOrEps"] += 1
            continue
        counts["modelEligibleChangedSamples"] += 1
        capped = abs(requested) >= near_normalized
        counts["requestAtNormalizedCeiling" if capped else "requestBelowNormalizedCeiling"] += 1
        # Source code compares EPS sensor and native command with an 80-unit
        # software margin. This does NOT prove ECU acceptance or rack torque.
        model = predict_command(clip(requested, -1, 1), prev, motor)
        if model["measurementClipped"]:
            counts["sourceMeasurementBoundActive"] += 1
        if model["rateClipped"]:
            counts["sourceRateBoundActive"] += 1
        matched = abs(observed - model["candidateNative"]) <= match_tolerance_native
        counts["matchesHypotheticalActiveUpdate" if matched else "notMatchedUnknownPhaseOrGating"] += 1
        if capped:
            counts["capMatched" if matched else "capNotMatched"] += 1
    return {
        "intervalSeconds": [start, end],
        "sourceConstants": {"STEER_MAX": STEER_MAX, "STEER_ERROR_MAX": STEER_ERROR_MAX,
                            "STEER_DELTA_UP": STEER_DELTA_UP, "STEER_DELTA_DOWN": STEER_DELTA_DOWN},
        "parameters": {"maxSourceAgeMs": max_source_age_ms,
                       "maxOutputIntervalMs": max_output_interval_ms,
                       "matchToleranceNative": match_tolerance_native,
                       "nearNormalizedThreshold": near_normalized},
        "counts": dict(counts),
        "rejectedRows": dict(rejected),
        "caveats": [
            "Historical source constants are not a verified vehicle-specific runtime calibration.",
            "A changed carOutput sample is an observed transition, not a guaranteed controller update.",
            "At-time/earlier sources may belong to different asynchronous control cycles.",
            "LKAS control-bit latch, controller phase and EPS CAN acceptance are not captured here.",
            "Source measurement bound is a SOFTWARE limit relative to EPS sensor units, not physical rack torque.",
            "Unmatched estimates can reflect 50 Hz output aliasing or LKAS gating; not proof of a malfunction.",
            "Output is read-only aggregate with no private vehicle identifiers or raw CAN traces.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, default=1800.0)
    parser.add_argument("--end", type=float, default=1807.0)
    args = parser.parse_args()
    with args.input_csv.open(newline="", encoding="utf-8") as file:
        report = analyze(csv.DictReader(file), start=args.start, end=args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "outputSamples": report["counts"].get("outputSamples", 0)}))


if __name__ == "__main__":
    main()
