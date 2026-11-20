#!/usr/bin/env python3
"""Bounded private Q-log clock-order audit; no payload, location, or route output.

This diagnostic does NOT repair/reorder messages, qualify driving data, or
invalidate an existing quarantine. It counts first-order timestamp inversions
using the installed-schema reader and exactly verified per-segment headers.
"""
from __future__ import annotations
import math

from auto_review import SCHEMA_COMMIT


class ClockAuditSourceError(ValueError):
    """Missing/contradictory installed source, not a route timing diagnosis."""


def audit_clock_order(paths, reader):
    """Return anonymous aggregates. Retain constant memory while scanning.

    Detect inversions between consecutive *non-initData* events, including
    cross-segment transitions. Never expose event timestamps, event data or
    path strings, even if a decoding exception contains private content.
    """
    result = {
        "auditVersion": 1,
        "sourceCommit": SCHEMA_COMMIT,
        "segmentsVerified": 0,
        "observations": 0,
        "inversionsOverHalfSecond": 0,
        "withinSegmentInversions": 0,
        "crossSegmentInversions": 0,
        "inversionSizeBands": {"underTwoSeconds": 0, "twoToTenSeconds": 0,
                               "overTenSeconds": 0},
        "status": "clock_order_audited",
        "caution": "Diagnostic only: neither clock inversions nor their absence prove valid driving metrics",
    }
    previous_t = None
    previous_segment = None
    for segment_idx, path in enumerate(paths):
        headers = 0
        for message in reader(path):
            kind = str(message.which())
            if kind == "initData":
                source = getattr(message.initData, "gitCommit", None)
                if type(source) is not str or source != SCHEMA_COMMIT:
                    raise ClockAuditSourceError("source-unverified")
                headers += 1
                continue
            ns = message.logMonoTime
            if isinstance(ns, bool) or not isinstance(ns, int) or ns < 0:
                raise ValueError("non-finite-or-invalid-monotonic-clock")
            t = ns / 1e9
            if not math.isfinite(t):
                raise ValueError("non-finite-monotonic-clock")
            if previous_t is not None and previous_t - t > 0.5:
                inversion = previous_t - t
                result["inversionsOverHalfSecond"] += 1
                if previous_segment == segment_idx:
                    result["withinSegmentInversions"] += 1
                else:
                    result["crossSegmentInversions"] += 1
                band = ("underTwoSeconds" if inversion < 2
                        else "twoToTenSeconds" if inversion < 10
                        else "overTenSeconds")
                result["inversionSizeBands"][band] += 1
            previous_t, previous_segment = t, segment_idx
            result["observations"] += 1
        if not headers:
            raise ClockAuditSourceError("source-header-missing")
        result["segmentsVerified"] += 1
    return result
