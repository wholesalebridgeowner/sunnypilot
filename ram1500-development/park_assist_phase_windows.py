#!/usr/bin/env python3
"""Compare two *time windows of one recorded R-log session* without CAN injection.

Designed for a supervised, OEM-only automatic-parking maneuver. Both windows
are relative to the first CAN record in the supplied files, NOT wall-clock time,
and therefore need explicit review of the captured session before selection.

No vehicle connection, steering actuation, decoded payloads, or uploads.
Output is aggregate bus/ID message-rate and byte-change comparison only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from park_assist_can_audit import compare, iter_records

NS = 1_000_000_000
REORDER_TOLERANCE_S = 2.0


def parse_window(s: str) -> tuple[float, float]:
    try:
        parts = s.split(":")
        if len(parts) != 2:
            raise ValueError()
        a, b = map(float, parts)
        if not (0 <= a < b <= 86400):
            raise ValueError()
        return a, b
    except ValueError as exc:
        raise ValueError("Window must be START:END seconds, 0 <= START < END <= 86400") from exc


def measure_windows(frames, baseline, parking):
    """One-pass generator input. Exact half-open boundaries; phase isolation."""
    a, b = baseline
    c, d = parking
    if not (a < b <= c < d or c < d <= a < b):
        raise ValueError("windows must not overlap")
    anchor = None
    newest = None
    max_reorder = 0.
    normal, oem = [], []
    count = 0
    for record in frames:
        if len(record) != 5:
            raise ValueError("invalid CAN input tuple")
        t = int(record[0])
        if anchor is None:
            anchor = t
        # loggerd writes per-socket batches, so logMonoTime is not strictly increasing in file order
        # (the drive-build LogReader offers sort_by_time for this). Accept bounded reordering; fail closed beyond it.
        if newest is not None and t < newest:
            lag = (newest - t) / NS
            if lag > REORDER_TOLERANCE_S:
                raise ValueError("R-log files must be supplied in chronological order")
            max_reorder = max(max_reorder, lag)
        newest = t if newest is None else max(newest, t)
        elapsed = (t - anchor) / NS
        if a <= elapsed < b:
            normal.append(record)
        elif c <= elapsed < d:
            oem.append(record)
        count += 1
    if not normal or not oem:
        raise ValueError("No CAN frames in one or both selected windows; inspect times")
    result = compare(normal, oem)
    # Compare rates against the explicitly selected interval, rather than the
    # timestamps of the first/last matching CAN frame.
    # Note: this does not correct for changes in vehicle speed or bus load.
    result["windowDefinitions"] = {
        "timeReference": "first CAN event in supplied R-log files",
        "normalSeconds": [a, b],
        "factoryParkingSeconds": [c, d],
        "normalWindowSeconds": b - a,
        "parkingWindowSeconds": d - c,
        "sourceCANFrameObservations": count,
        "normalWindowFrames": len(normal),
        "parkingWindowFrames": len(oem),
        "maxReorderSeconds": round(max_reorder, 3),
    }
    result["caveats"].append(
        "Windows are user-annotated; without confirmed OEM park-assist state, this cannot prove that parking mode engaged.")
    result["caveats"].append(
        "The first CAN event is an input-file anchor, not a trustworthy route start or wall-clock anchor.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, required=True,
                        help="Pinned, verified drive-build log.capnp with matching car.capnp")
    parser.add_argument("--logs", nargs="+", type=Path, required=True,
                        help="Chronologically ordered private R-log segment files")
    parser.add_argument("--normal", type=parse_window, required=True,
                        help="Low-speed non-parking baseline START:END in seconds from first supplied CAN")
    parser.add_argument("--oem-parking", type=parse_window, required=True,
                        help="Factory parking window START:END in seconds from first supplied CAN")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    if args.out.resolve().is_relative_to(repo_root):
        parser.error("Private telemetry derivatives must not be written inside the git repository")
    if not args.schema.is_file() or not all(path.is_file() for path in args.logs):
        parser.error("Missing private logs or pinned schema")
    try:
        report = measure_windows(iter_records(args.logs, args.schema), args.normal, args.oem_parking)
    except ValueError as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"normalFrames": report["windowDefinitions"]["normalWindowFrames"],
                      "parkingFrames": report["windowDefinitions"]["parkingWindowFrames"],
                      "candidateCANIDs": len(report["changes"])}))


if __name__ == "__main__":
    main()
