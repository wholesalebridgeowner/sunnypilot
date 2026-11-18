#!/usr/bin/env python3
"""Read-only, delay-aware EPS-feedback correlation for Ram 1500 CSV exports.

Input: private CSV from analyze_route.py, preferably full-route R-logs.
A same-sign match is NOT proof that the EPS accepted an openpilot command.
Only aggregate results are emitted; never commit raw CSV or vehicle IDs.

Example:
  python3 ram1500-development/eps_feedback_lag.py /private/route.csv --output /private/eps-lag.json
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import csv
import json
import math
from pathlib import Path

# Speed bands in m/s. Exactly zero is kept separate from rolling.
BANDS = (
    ("reported_zero", 0.0, 0.0),
    ("rolling_below_0p5", 0.0, 0.5),
    ("crawl_0p5_to_2", 0.5, 2.0),
    ("low_2_to_5", 2.0, 5.0),
    ("low_5_to_10", 5.0, 10.0),
    ("low_10_to_14p5", 10.0, 14.5),
    ("above_14p5", 14.5, math.inf),
)


def number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def boolean(value):
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in ("1", "true"):
        return True
    if str(value).strip().lower() in ("0", "false"):
        return False
    return None


def band(speed):
    if speed is None or speed < 0:
        return None
    if speed == 0:
        return "reported_zero"
    for name, lo, hi in BANDS[1:]:
        if lo < speed < hi if lo == 0 else lo <= speed < hi:
            return name
    return None


def prior_at(stream, stamps, target_ns, max_age_ns):
    """Return most recent sample no later than target, or None if stale."""
    ix = bisect_right(stamps, target_ns) - 1
    if ix < 0:
        return None
    when, payload = stream[ix]
    return payload if 0 <= target_ns - when <= max_age_ns else None


def analyze(rows, *, delays_ms=(0, 100, 200, 300), join_skew_ms=100.0,
            control_max_age_ms=150.0, min_command_native=50.0,
            min_eps_feedback=50.0, max_driver_torque=60.0):
    delays = tuple(delays_ms)
    if not delays or len(delays) != len(set(delays)):
        raise ValueError("delays_ms must be nonempty and distinct")
    vals = delays + (join_skew_ms, control_max_age_ms,
                      min_command_native, min_eps_feedback, max_driver_torque)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or
           not math.isfinite(v) or v < 0 for v in vals):
        raise ValueError("all parameters must be finite, numeric and nonnegative")
    if join_skew_ms <= 0 or control_max_age_ms <= 0 or min_command_native <= 0 or min_eps_feedback <= 0:
        raise ValueError("timing windows and signal thresholds must be positive")

    streams = {key: [] for key in ("carState", "carControl", "carOutput")}
    rejected = Counter()
    for row in rows:
        kind = row.get("stream")
        if kind not in streams:
            continue
        try:
            t = int(row["monoTimeNs"])
            if t < 0:
                raise ValueError("negative timestamp")
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected["badTimestamp"] += 1
            continue
        if boolean(row.get("valid")) is False:
            rejected["invalidMessage"] += 1
            continue
        if boolean(row.get("valid")) is None:
            rejected["unknownValidity"] += 1
        streams[kind].append((t, row))
    for sequence in streams.values():
        sequence.sort(key=lambda x: x[0])
    stamps = {key: [t for t, _ in seq] for key, seq in streams.items()}
    results = {}
    for name, _, _ in BANDS:
        results[name] = {
            "carStateSamples": 0,
            "eligibleActiveLowDriverSamples": 0,
            "delaysMs": {str(d): {
                "matchedOutput": 0,
                "commandAboveThreshold": 0,
                "sameSignFeedback": 0,
                "oppositeSignFeedback": 0,
                "weakFeedback": 0,
            } for d in delays},
        }
    for t, row in streams["carState"]:
        speed = number(row.get("vEgo_mps"))
        speed_name = band(speed)
        if speed_name is None:
            rejected["invalidSpeed"] += 1
            continue
        result = results[speed_name]
        result["carStateSamples"] += 1
        driver = number(row.get("steeringTorque"))
        eps = number(row.get("steeringTorqueEps"))
        ctrl = prior_at(streams["carControl"], stamps["carControl"], t, control_max_age_ms * 1e6)
        if (driver is None or eps is None or abs(driver) > max_driver_torque or
                ctrl is None or boolean(ctrl.get("carControlLatActive")) is not True):
            continue
        result["eligibleActiveLowDriverSamples"] += 1
        for delay in delays:
            stats = result["delaysMs"][str(delay)]
            # A possible physical response is compared with an older command,
            # never a command that occurred after this carState observation.
            target = t - delay * 1e6
            cmdrow = prior_at(streams["carOutput"], stamps["carOutput"],
                              target, join_skew_ms * 1e6)
            if cmdrow is None:
                continue
            stats["matchedOutput"] += 1
            cmd = number(cmdrow.get("outputTorqueCan"))
            if cmd is None or abs(cmd) < min_command_native:
                continue
            stats["commandAboveThreshold"] += 1
            if abs(eps) < min_eps_feedback:
                stats["weakFeedback"] += 1
            elif cmd * eps > 0:
                stats["sameSignFeedback"] += 1
            else:
                stats["oppositeSignFeedback"] += 1
    return {
        "parameters": {
            "delaysMs": list(delays), "joinSkewMs": join_skew_ms,
            "controlMaxAgeMs": control_max_age_ms,
            "minCommandNative": min_command_native,
            "minEpsFeedback": min_eps_feedback,
            "maxDriverTorque": max_driver_torque,
        },
        "rejectedRows": dict(rejected),
        "speedBands": results,
        "caveats": [
            "Read-only diagnostic: no ECU, Panda, or vehicle command is issued.",
            "The command is LKAS native CAN units; EPS feedback uses another sensor scale. Only direction is compared.",
            "Same-sign EPS feedback is correlation, not proof of EPS acceptance, steering movement, or causal actuation.",
            "The 'eligible' label means active requested lateral control and low measured driver torque, not verified hands-off driving.",
            "Delay sweeps are descriptive, not tuned controller delays; selecting the best correlation would introduce bias.",
            "Samples are serially correlated and do not represent independent maneuvers or statistical confidence intervals.",
            "Zero means exactly the reported vEgo value 0, not verified physical standstill.",
            "Mixed-route or mixed Q-/R-log inputs must be rejected by route_integrity.py before exporting CSV.",
            "Unknown validity messages are included but enumerated and do not establish sensor correctness.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delays-ms", nargs="+", type=float, default=[0, 100, 200, 300])
    parser.add_argument("--join-skew-ms", type=float, default=100.0)
    args = parser.parse_args()
    with args.input_csv.open(newline="", encoding="utf-8") as f:
        result = analyze(csv.DictReader(f), delays_ms=args.delays_ms,
                         join_skew_ms=args.join_skew_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "speedBands": len(result["speedBands"])}))


if __name__ == "__main__":
    main()
