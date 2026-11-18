#!/usr/bin/env python3
"""Offline input-integrity gate for a single Comma route's Q- or R-logs.

This checks file layout, segment numbering and on-disk existence only.
It cannot establish that the final segment was recorded unless expected_segments
is independently known. No telemetry is read or transmitted.

Example:
    python3 route_integrity.py --expected-segments 51 /private/route/*/qlog.zst
    python3 analyze_route.py /private/route/*/qlog.zst --focus-seconds 171 ...

Do NOT combine qlogs and rlogs in an analysis input: they contain overlapping
telemetry, so an apparently larger sample count can be misleading.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

LOG_PATTERN = re.compile(r"^(qlog|rlog)(?:\.(?:zst|bz2))?$")


def inspect(files, *, expected_segments=None, kind=None):
    files = list(files)
    if expected_segments is not None and (isinstance(expected_segments, bool) or
                                         not isinstance(expected_segments, int) or expected_segments <= 0):
        raise ValueError("expected_segments must be a positive integer")
    if kind not in (None, "qlog", "rlog"):
        raise ValueError("kind must be qlog or rlog")
    errors = []
    items = []
    for filename in files:
        path = Path(filename)
        match = LOG_PATTERN.fullmatch(path.name)
        if not match:
            errors.append("unrecognizedLogFilename")
            continue
        log_kind = match.group(1)
        if kind is not None and log_kind != kind:
            errors.append("wrongLogKind")
            continue
        if not path.parent.name.isascii() or not path.parent.name.isdecimal():
            errors.append("invalidSegmentDirectory")
            continue
        segment = int(path.parent.name)
        if segment < 0:
            errors.append("invalidSegmentDirectory")
            continue
        try:
            size = path.stat().st_size
        except OSError:
            errors.append("missingOrUnreadableFile")
            continue
        if size == 0:
            errors.append("emptyLogFile")
        items.append((path, segment, log_kind, size))

    observed_kinds = sorted({kind for _, _, kind, _ in items})
    if len(observed_kinds) > 1:
        errors.append("mixedQlogRlog")
    roots = {str(path.parent.parent.resolve()) for path, _, _, _ in items}
    if len(roots) > 1:
        errors.append("multipleRouteDirectories")
    indices = [segment for _, segment, _, _ in items]
    unique_indices = sorted(set(indices))
    if len(unique_indices) < len(indices):
        errors.append("duplicateSegment")
    if not unique_indices:
        errors.append("noValidLogs")
    observed_max = max(unique_indices) if unique_indices else -1
    observed_missing = sorted(set(range(observed_max + 1)) - set(unique_indices))
    if observed_missing:
        errors.append("segmentGap")
    missing_expected = (sorted(set(range(expected_segments)) - set(unique_indices))
                        if expected_segments is not None else None)
    extra_expected = (sorted(set(unique_indices) - set(range(expected_segments)))
                      if expected_segments is not None else None)
    if missing_expected:
        errors.append("missingExpectedSegments")
    if extra_expected:
        errors.append("unexpectedSegments")

    # Report neither raw filenames nor the parent directory. They might contain
    # dongle identifiers, local usernames, or route IDs. The caller knows them.
    return {
        "inputFiles": len(files),
        "validFilenameReadableFiles": len(items),
        "kinds": observed_kinds,
        "observedSegments": unique_indices,
        "missingSegmentsThroughObservedMaximum": observed_missing,
        "expectedSegments": expected_segments,
        "missingExpectedSegments": missing_expected,
        "unexpectedSegments": extra_expected,
        "bytes": sum(size for _, _, _, size in items),
        "errors": sorted(set(errors)),
        "contiguousFromZero": bool(unique_indices and not observed_missing),
        "expectedRangeFullyPresent": (expected_segments is not None and
                                      not errors and len(unique_indices) == expected_segments),
        "caveat": "A complete segment list cannot prove internal Cap'n Proto validity, continuity, or that the route ended there.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--expected-segments", type=int, required=True,
                        help="Expected segment count from an independent route manifest, not a guess")
    parser.add_argument("--kind", choices=("qlog", "rlog"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect(args.logs, expected_segments=args.expected_segments, kind=args.kind)
    body = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    raise SystemExit(0 if result["expectedRangeFullyPresent"] else 2)


if __name__ == "__main__":
    main()
