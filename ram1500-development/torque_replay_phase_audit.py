#!/usr/bin/env python3
"""Offline, read-only sensitivity audit of mismatched Ram steering replay changes.

Counterfactual older-input matching is NOT evidence of which controller sample
was used. No vehicle identifiers, absolute timestamps, raw CAN or individual
observations are emitted.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from torque_command_replay import boolean, number, predict_command, STEER_MAX


def age_bin(ms):
    if ms < 10:
        return "0to10ms"
    if ms < 30:
        return "10to30ms"
    if ms < 60:
        return "30to60ms"
    return "60to80ms"


def source_options(stream, stamps, when, max_age_ns, max_older=3):
    """Latest plus up to N earlier distinct-timestamp, never future, records."""
    i = bisect_right(stamps, when) - 1
    options, seen = [], set()
    while i >= 0 and len(options) <= max_older:
        t, row = stream[i]
        if when - t > max_age_ns:
            break
        if t not in seen:
            options.append((t, row))
            seen.add(t)
        i -= 1
    return options


def audit(rows, *, start=1800.0, end=1807.0, max_source_age_ms=80.0,
          max_output_gap_ms=40.0, max_older=3, tolerance=1.0):
    vals = (number(start), number(end), number(max_source_age_ms),
            number(max_output_gap_ms), number(tolerance))
    if any(v is None for v in vals) or not (0 <= start <= end and
            0 < max_source_age_ms <= 80 and 0 < max_output_gap_ms <= 80 and
            0 <= tolerance <= 10 and type(max_older) is int and 0 <= max_older <= 8):
        raise ValueError("invalid audit bounds")

    streams = {k: [] for k in ("carOutput", "carControl", "carState")}
    pre_roll = max(max_source_age_ms, max_output_gap_ms) / 1000
    for row in rows:
        kind = row.get("stream")
        if kind not in streams or boolean(row.get("valid")) is False:
            continue
        try:
            t = int(row["monoTimeNs"])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        sec = number(row.get("seconds"))
        if t < 0 or sec is None or sec < start - pre_roll or sec > end:
            continue
        streams[kind].append((t, row))
    for stream in streams.values():
        stream.sort(key=lambda pair: pair[0])
    stamps = {k: [t for t, _ in v] for k, v in streams.items()}
    counts = Counter()
    mismatch_control_age = Counter()
    mismatch_state_age = Counter()
    match_control_age = Counter()
    error_bands = Counter()
    max_error = 0
    max_source_ns = max_source_age_ms * 1e6
    max_output_ns = max_output_gap_ms * 1e6
    outputs = streams["carOutput"]

    def candidate(control, state, previous):
        if boolean(control.get("carControlLatActive")) is not True:
            return None
        request = number(control.get("requestedTorque"))
        eps = number(state.get("steeringTorqueEps"))
        if request is None or eps is None or not (-1 <= request <= 1 and abs(eps) <= 20000):
            return None
        return predict_command(request, previous, eps)["candidateNative"]

    for i, (when, row) in enumerate(outputs):
        sec = number(row.get("seconds"))
        if sec is None or not start <= sec <= end:
            continue
        counts["outputSamples"] += 1
        observed = number(row.get("outputTorqueCan"))
        if observed is None or abs(observed) > STEER_MAX:
            counts["invalidOutput"] += 1
            continue
        if i == 0 or not (0 < when - outputs[i - 1][0] <= max_output_ns):
            counts["missingFreshPreviousOutput"] += 1
            continue
        previous = number(outputs[i - 1][1].get("outputTorqueCan"))
        if previous is None or abs(previous) > STEER_MAX:
            counts["missingFreshPreviousOutput"] += 1
            continue
        if observed == previous:
            counts["heldOutputSamples"] += 1
            continue
        counts["changedOutputSamples"] += 1
        cs = source_options(streams["carState"], stamps["carState"], when, max_source_ns, max_older)
        cc = source_options(streams["carControl"], stamps["carControl"], when, max_source_ns, max_older)
        if not cs or not cc:
            counts["missingSource"] += 1
            continue
        baseline = candidate(cc[0][1], cs[0][1], previous)
        if baseline is None:
            counts["ineligibleLatestSource"] += 1
            continue
        counts["eligibleChangedOutput"] += 1
        control_age_bin = age_bin((when - cc[0][0]) / 1e6)
        state_age_bin = age_bin((when - cs[0][0]) / 1e6)
        if abs(baseline - observed) <= tolerance:
            counts["baselineMatched"] += 1
            match_control_age[control_age_bin] += 1
            continue
        counts["baselineUnmatched"] += 1
        mismatch_control_age[control_age_bin] += 1
        mismatch_state_age[state_age_bin] += 1
        error = abs(baseline - observed)
        max_error = max(max_error, error)
        error_bands["1to6" if error <= 6 else "7to20" if error <= 20 else "over20"] += 1
        request = number(cc[0][1].get("requestedTorque"))
        counts["unmatchedAtNormalizedCeiling" if abs(request) >= 0.995 else "unmatchedBelowNormalizedCeiling"] += 1
        # Keep previous native output fixed; vary only at-or-before source ages.
        # Priority buckets are mutually exclusive and deliberately retrospective.
        alternatives = (
            ("olderControlOnly", [(a, cs[0]) for a in cc[1:]]),
            ("olderStateOnly", [(cc[0], b) for b in cs[1:]]),
            ("olderControlAndState", [(a, b) for a in cc[1:] for b in cs[1:]]),
        )
        found = False
        for label, combinations in alternatives:
            for (_, control), (_, state) in combinations:
                predicted = candidate(control, state, previous)
                if predicted is not None and abs(predicted - observed) <= tolerance:
                    counts[label] += 1
                    found = True
                    break
            if found:
                break
        if not found:
            counts["notExplainedByOlderInputs"] += 1

    return {
        "parameters": {"windowSeconds": [start, end], "maxSourceAgeMs": max_source_age_ms,
                       "maxOutputGapMs": max_output_gap_ms, "olderSamplesPerStream": max_older,
                       "matchToleranceNative": tolerance},
        "counts": dict(counts),
        "baselineMatchesByControlAge": dict(match_control_age),
        "baselineMismatchesByControlAge": dict(mismatch_control_age),
        "baselineMismatchesByStateAge": dict(mismatch_state_age),
        "baselineMismatchAbsErrorBandsNative": dict(error_bands),
        "maxAbsBaselineErrorNative": max_error,
        "caveats": [
            "Historical torque limits are not verified against the installed runtime.",
            "Retrospective older-input match can occur by chance; it is not proof of controller phase.",
            "Alternate inputs are at or before observed output time and inside age bounds.",
            "No LKAS latch, internal update phase, CAN acknowledgment, or EPS acceptance captured.",
            "Aggregate counts only; no absolute timestamps, GPS, raw CAN or vehicle identifiers.",
        ],
    }
