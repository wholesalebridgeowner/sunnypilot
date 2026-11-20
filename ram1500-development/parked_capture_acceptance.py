#!/usr/bin/env python3
"""Validate a parked, passive Comma telemetry capture before any longer run.

Reads local sidecar JSONL, emits only privacy-allowlisted health/coverage counts.
No network use, Comma commands, CAN transmission, controller writes, or vehicle
configuration changes. A passing parked diagnostic is NOT release approval.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

SLOT_LIMIT = 15
CPU_LIMIT_ONE_CORE_PERCENT = 15.0
PRIVATE_RSS_LIMIT_KIB = 150 * 1024
MOTION_LIMIT_MS = 0.3
EXPECTED_SECONDS = 120.0
MIN_HEALTH_ROWS = 8
REQUIRED_SERVICES = (
    "can", "sendcan", "carState", "carControl", "carOutput",
    "controlsState", "selfdriveState", "onroadEvents",
    "longitudinalPlan", "radarState", "drivingModelData",
)
BLOCKING_EVENTS = {
    "commIssue", "commIssueAvgFreq", "canError", "controlsMismatch",
    "steerUnavailable", "steerTempUnavailable", "steerSaturated",
    "processNotRunning", "vehicleSensorsInvalid", "usbError",
    "relayMalfunction", "overheat", "fanMalfunction",
}
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def reader_count_checks(start, stop):
    before = start.get("readerCountsBefore")
    after = stop.get("readerCountsAfter")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False, ["missing_reader_count_snapshot"], 0
    issues = []
    verified = 0
    for name in REQUIRED_SERVICES:
        b, a = before.get(name), after.get(name)
        if type(b) is not int or type(a) is not int:
            issues.append("missing_reader_count")
        elif not 0 <= b < SLOT_LIMIT or a != b + 1 or a > SLOT_LIMIT:
            issues.append("subscriber_count_change_invalid")
        else:
            verified += 1
    return not issues, sorted(set(issues)), verified


def validate_rows(rows):
    starts, stops, health = [], [], []
    count = Counter()
    problems = set()
    max_window_cpu = 0.0
    max_private_kib = 0
    max_speed = 0.0
    invalid_car_state = 0
    missing_car_validity = 0
    fault_counts = Counter()
    health_windows = 0

    for row in rows:
        if not isinstance(row, dict):
            problems.add("malformed_capture_record")
            continue
        service = row.get("service")
        if not isinstance(service, str):
            problems.add("missing_service_tag")
            continue
        count[service] += 1
        if service == "sidecar_start":
            starts.append(row)
        elif service == "sidecar_stop":
            stops.append(row)
        elif service == "sidecar_health":
            health.append(row)
            cpu = finite_number(row.get("windowCpuPercentOneCore"))
            if cpu is not None:
                if cpu < 0:
                    problems.add("invalid_cpu_measurement")
                max_window_cpu = max(max_window_cpu, cpu)
                health_windows += 1
            private = row.get("rssAnonKiB")
            if type(private) is int and private >= 0:
                max_private_kib = max(max_private_kib, private)
            else:
                problems.add("missing_private_memory_measurement")
            decoded = row.get("rows")
            if isinstance(decoded, dict) and decoded.get("decode_errors", 0):
                problems.add("message_decode_errors")
        elif service == "carState":
            speed = finite_number(row.get("vEgo"))
            if speed is None:
                problems.add("missing_vehicle_speed")
            else:
                max_speed = max(max_speed, abs(speed))
                if abs(speed) > MOTION_LIMIT_MS:
                    problems.add("vehicle_moved_during_capture")
            if row.get("canValid") is False:
                invalid_car_state += 1
                problems.add("invalid_can_state")
            elif row.get("canValid") is not True:
                missing_car_validity += 1
                problems.add("unknown_can_validity")
        elif service == "onroadEvents":
            events = row.get("events")
            if not isinstance(events, list):
                problems.add("malformed_event_record")
                continue
            for name in events:
                if isinstance(name, str) and name in BLOCKING_EVENTS:
                    fault_counts[name] += 1
                    problems.add("fault_event_observed")

    if len(starts) != 1 or len(stops) != 1:
        problems.add("capture_must_have_one_start_and_stop")
    start = starts[0] if len(starts) == 1 else {}
    stop = stops[0] if len(stops) == 1 else {}
    stop_reason = stop.get("reason")
    if stop_reason != "duration reached":
        problems.add("capture_did_not_finish_normally")
    if start.get("allowMoving") is not False:
        problems.add("moving_capture_not_permitted")
    if start.get("allCanIds") is not False:
        problems.add("unbounded_can_capture_not_permitted")
    sample_hz = start.get("sampleHz")
    if type(sample_hz) is not int or sample_hz > 2 or sample_hz < 1:
        problems.add("sample_rate_outside_parked_pilot")
    if not health or len(health) < MIN_HEALTH_ROWS or health_windows < MIN_HEALTH_ROWS - 1:
        problems.add("insufficient_health_coverage")
    if max_window_cpu > CPU_LIMIT_ONE_CORE_PERCENT:
        problems.add("cpu_overhead_above_pilot_limit")
    if max_private_kib > PRIVATE_RSS_LIMIT_KIB:
        problems.add("private_memory_above_pilot_limit")
    if count.get("carState", 0) < 40:
        problems.add("insufficient_car_state_coverage")
    if count.get("can", 0) == 0:
        problems.add("no_can_frames_observed")
    if count.get("sidecar_start", 0) == 1 and count.get("sidecar_stop", 0) == 1:
        a = finite_number(health[-1].get("wallElapsedS")) if health else None
        if a is None or abs(a - EXPECTED_SECONDS) > 20:
            problems.add("capture_duration_outside_two_minute_gate")
    _, reader_issues, reader_services_verified = reader_count_checks(start, stop)
    problems.update(reader_issues)

    # Only allowlisted scalars and categorical failure labels are returned.
    # Deliberately omit all raw telemetry, filenames, event timestamps, values,
    # route/device identifiers, CAN addresses and input record contents.
    return {
        "status": "parked_diagnostic_accepted" if not problems else "parked_diagnostic_rejected",
        "passed": not problems,
        "blockers": sorted(problems),
        "coverage": {
            "healthRows": len(health),
            "measuredCpuWindows": health_windows,
            "carStateSamples": count.get("carState", 0),
            "canRecords": count.get("can", 0),
            "verifiedSubscriberIncrements": reader_services_verified,
        },
        "limits": {
            "cpuOneCorePercent": CPU_LIMIT_ONE_CORE_PERCENT,
            "maxObservedCpuOneCorePercent": round(max_window_cpu, 2),
            "privateRssMB": 150,
            "maxObservedPrivateRssMB": round(max_private_kib / 1024, 2),
            "parkedSpeedThresholdMs": MOTION_LIMIT_MS,
            "maxObservedSpeedMs": round(max_speed, 3),
        },
        "canValidity": {
            "invalidCarStateSamples": invalid_car_state,
            "unknownCarStateSamples": missing_car_validity,
        },
        "observedFaultEventSampleCounts": dict(sorted(fault_counts.items())),
        "controllerSoftwareValidated": False,
        "vehicleReleaseAuthorized": False,
    }


def capture_rows(paths):
    total_bytes = 0
    for path in paths:
        st = path.stat()
        total_bytes += st.st_size
        if total_bytes > MAX_INPUT_BYTES:
            raise ValueError("capture_input_too_large")
        with path.open(encoding="utf-8") as source:
            for line in source:
                if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
                    raise ValueError("capture_record_too_large")
                try:
                    yield json.loads(line)
                except (ValueError, TypeError):
                    yield None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = validate_rows(capture_rows(args.captures))
    except Exception:
        # Neither exception text nor filenames should escape from private log handling.
        result = {"status": "parked_diagnostic_rejected", "passed": False,
                  "blockers": ["capture_unreadable_or_invalid"], "vehicleReleaseAuthorized": False}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
