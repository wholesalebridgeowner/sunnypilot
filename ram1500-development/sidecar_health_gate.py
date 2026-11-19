"""Read-only offline QA of anonymized Comma parked-sidecar health rows.

Input is *only* the JSONL sidecar_start/sidecar_health/sidecar_stop
records, extracted privately from a completed, operator-approved, parked
session. Do not provide raw CAN, device identifiers, or full capture files.
The result is aggregate device-overhead evidence, never a safety/release approval.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

KINDS = {"sidecar_start", "sidecar_health", "sidecar_stop"}
READER_CAPACITY = 15  # Version-specific, verified for the historical device image.


def _number(value):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0)


def _counts_are_valid(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict) or not before:
        return False
    if set(before) != set(after):
        return False
    return all(type(before[k]) is int and type(after[k]) is int
               and 0 <= before[k] < READER_CAPACITY
               and after[k] == before[k] + 1 for k in before)


def assess(rows, *, expected_seconds=120, cpu_limit=15.0, private_memory_limit_mb=150):
    """Fail closed. Never return an install/road-safety approval.

    rows must contain health-only rows from exactly one capture session.
    No direct file paths or keys are returned, even on invalid input.
    """
    if not (_number(expected_seconds) and expected_seconds > 0
            and _number(cpu_limit) and cpu_limit > 0
            and _number(private_memory_limit_mb) and private_memory_limit_mb > 0):
        raise ValueError("limits must be positive finite numbers")
    result = {
        "status": "indeterminate", "metricGate": "not_established",
        "sessionCount": 0, "healthSamples": 0, "cpuWindows": 0,
        "durationSeconds": None, "cpuPeakPercentOneCore": None,
        "privateRssStartMiB": None, "privateRssEndMiB": None,
        "privateRssGrowthMiBPerMinute": None,
        "sharedRssEndMiB": None, "readersAddedWithoutEviction": False,
        "decodeErrorFree": False, "telemetryStreamProgressConfirmed": False,
        "requestedRateEvidenceConfirmed": False,
        "reasons": [],
        "separateGatesNotEvaluated": [
            "vehicle_parked_attestation", "driver_alerts_and_can_validity",
            "loggerd_pandad_card_controlsd_pid_stability", "hardware_build_safety_review"],
        "scope": "offline_health_only_not_installation_or_vehicle_safety",
    }
    problems = result["reasons"]
    try:
        series = list(rows)
        if not series or any(not isinstance(r, dict) or r.get("service") not in KINDS for r in series):
            problems.append("health_only_input_required")
            return result
        starts = [r for r in series if r["service"] == "sidecar_start"]
        stops = [r for r in series if r["service"] == "sidecar_stop"]
        health = [r for r in series if r["service"] == "sidecar_health"]
        result["sessionCount"] = len(starts)
        result["healthSamples"] = len(health)
        if len(starts) != 1 or len(stops) != 1 or len(health) < 3 or series[0] is not starts[0] or series[-1] is not stops[0]:
            problems.append("incomplete_or_multiple_sessions")
            return result
        start, stop = starts[0], stops[0]
        if start.get("allowMoving") is not False or start.get("allCanIds") is not False:
            problems.append("parked_low_overhead_mode_not_confirmed")
        sample_hz = start.get("sampleHz")
        if type(sample_hz) is not int or not 1 <= sample_hz <= 5:
            problems.append("unreviewed_sample_rate")
        result["readersAddedWithoutEviction"] = _counts_are_valid(
            start.get("readerCountsBefore"), stop.get("readerCountsAfter"))
        if not result["readersAddedWithoutEviction"]:
            problems.append("subscriber_counts_invalid_or_missing")
        if stop.get("reason") != "duration reached":
            problems.append("capture_did_not_finish_normally")
        durations = [h.get("wallElapsedS") for h in health]
        # The on-device logger emits a final health row in its finally block,
        # immediately before sidecar_stop. That terminal row has NO window CPU
        # metric, and its 0.01 s-rounded clock may equal the prior timed row.
        # Ordinary timed health observations must still increase strictly.
        if (not all(_number(d) for d in durations)
                or any(b <= a for a, b in zip(durations[:-2], durations[1:-1]))
                or durations[-1] < durations[-2]
                or (durations[-1] == durations[-2]
                    and health[-1].get("windowCpuPercentOneCore") is not None)):
            problems.append("invalid_health_clock")
            return result
        # Counting many samples at the END is not coverage of the start.
        # The source's HEALTH_INTERVAL_S is 10 seconds; allow one delayed
        # window, but never accept an unobserved first 20+ seconds.
        if not 0 < durations[0] <= 20.0:
            problems.append("initial_health_window_missing")
        duration = durations[-1]
        result["durationSeconds"] = round(duration, 2)
        if duration < 0.95 * expected_seconds or duration > 1.20 * expected_seconds:
            problems.append("duration_outside_expected_window")
        if len(health) < max(3, math.floor(expected_seconds / 10) - 1):
            problems.append("too_few_health_observations")
        if any((b-a) > 20.0 for a, b in zip(durations, durations[1:])):
            problems.append("health_sample_gap_excessive")
        # Window CPU is elapsed CPU / elapsed wall time since the previous
        # periodic emission. Source advances next_health by 10s even after
        # an overdue emission, so it can emit a catch-up burst of near-zero
        # windows. Such windows cannot establish sustained resource overhead.
        if any(end - begin < 5.0 for begin, end in
               zip([0.0] + durations[:-2], durations[:-1])):
            problems.append("cpu_window_duration_too_short")
        cpu_values = [h.get("windowCpuPercentOneCore") for h in health]
        valid_windows = [v for v in cpu_values if _number(v)]
        result["cpuWindows"] = len(valid_windows)
        if len(valid_windows) < max(2, math.floor(expected_seconds / 10) - 2):
            problems.append("insufficient_cpu_windows")
        # Historical device_telemetry.py emits a timed CPU figure for EVERY
        # periodic health row; the final unwindowed health row is intentionally
        # None. Missing an interior CPU window means an unobserved potential
        # overhead spike, even when the total window count looks sufficient.
        if any(not _number(v) for v in cpu_values[:-1]):
            problems.append("missing_or_invalid_periodic_cpu_window")
        if ("windowCpuPercentOneCore" not in health[-1]
                or cpu_values[-1] is not None):
            problems.append("unwindowed_terminal_health_missing")
        if valid_windows:
            result["cpuPeakPercentOneCore"] = round(max(valid_windows), 2)
            if max(valid_windows) > cpu_limit:
                problems.append("sidecar_cpu_limit_exceeded")
        private = [h.get("rssAnonKiB") for h in health]
        shared = [h.get("rssShmemKiB") for h in health]
        if not all(_number(v) for v in private) or not all(_number(v) for v in shared):
            problems.append("split_memory_metrics_missing_or_invalid")
        else:
            result["privateRssStartMiB"] = round(private[0]/1024, 2)
            result["privateRssEndMiB"] = round(private[-1]/1024, 2)
            result["sharedRssEndMiB"] = round(shared[-1]/1024, 2)
            span = durations[-1] - durations[0]
            if span > 0:
                result["privateRssGrowthMiBPerMinute"] = round((private[-1] - private[0]) * 60 / 1024 / span, 3)
            if max(private) > 1024 * private_memory_limit_mb:
                problems.append("private_memory_limit_exceeded")
        # A low CPU reading is meaningless if the logger is not actually
        # decoding its expected parked CAN and carState streams. The pinned
        # device_telemetry.py uses Counter and records a cumulative snapshot
        # in every timed and terminal health row. Validate those snapshots
        # without reading or exporting a single CAN payload.
        count_keys = {
            "can", "sendcan", "carState", "carControl", "carOutput",
            "controlsState", "selfdriveState", "onroadEvents",
            "longitudinalPlan", "radarState", "drivingModelData",
            "decode_errors",
        }
        samples = [h.get("rows") for h in health]
        if any(
            not isinstance(row, dict) or any(
                key not in count_keys or type(val) is not int or val < 0
                for key, val in row.items()
            )
            for row in samples
        ):
            problems.append("missing_or_invalid_telemetry_counters")
        else:
            if any(
                curr.get(key, 0) < prev.get(key, 0)
                for prev, curr in zip(samples, samples[1:])
                for key in count_keys
            ):
                problems.append("telemetry_counter_regression")
            if any(row.get("decode_errors", 0) for row in samples):
                problems.append("telemetry_decode_errors_observed")
            else:
                result["decodeErrorFree"] = True
            # Terminal health may share a rounded clock and identical counters
            # with the final periodic row. Require progress between PERIODIC
            # observations, not between periodic and terminal.
            timed = samples[:-1]
            critical = ("can", "carState")
            if (not all(timed[0].get(k, 0) > 0 for k in critical)
                    or any(
                        curr.get(k, 0) <= prev.get(k, 0)
                        for prev, curr in zip(timed, timed[1:])
                        for k in critical
                    )):
                problems.append("parked_capture_stream_progress_missing")
            else:
                result["telemetryStreamProgressConfirmed"] = True
            # Monotone counters alone admit one carState message per 10s,
            # which is not representative of a requested 1-5 Hz sample
            # rate. Gate on >=50% of requested carState rate per CPU window.
            # A CAN-silent/idle parked truck can fail this evidence gate
            # without being diagnosed as faulty; raw CAN rates are unknown.
            if type(sample_hz) is int and 1 <= sample_hz <= 5:
                rate_sufficient = True
                for i, row in enumerate(timed):
                    interval = durations[i] - (durations[i-1] if i else 0.0)
                    previous = timed[i-1].get("carState", 0) if i else 0
                    observed = row.get("carState", 0) - previous
                    minimum = max(1, math.floor(0.5 * sample_hz * interval))
                    if observed < minimum:
                        rate_sufficient = False
                        break
                if not rate_sufficient:
                    problems.append("carstate_rate_not_representative")
                else:
                    result["requestedRateEvidenceConfirmed"] = True

        result["metricGate"] = "passed" if not problems else "failed_or_incomplete"
        result["status"] = "metrics_pass_other_gates_pending" if not problems else "not_validated"
        return result
    except (TypeError, ValueError, KeyError):
        problems.append("malformed_health_summary")
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("health_only_jsonl", type=Path)
    p.add_argument("--expected-seconds", type=float, default=120)
    a = p.parse_args()
    with a.health_only_jsonl.open(encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    print(json.dumps(assess(records, expected_seconds=a.expected_seconds), sort_keys=True))


if __name__ == "__main__":
    main()
