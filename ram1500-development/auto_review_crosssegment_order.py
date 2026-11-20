#!/usr/bin/env python3
"""Bounded event-time ordering across Comma Q-log segment boundaries.

Read-only diagnostics. Retains all source observations, requires an exact
per-segment firmware source header, and fails when a late observation exceeds
the validated window. Unlike the original bounded trial, it does not flush
the pending heap at a segment boundary; that lets small, normal overlaps be
ordered without masking a reset or creating synthetic data.
"""
from __future__ import annotations
import heapq
import math

from auto_review import SCHEMA_COMMIT

MAX_LAG_SECONDS = 2.0
MAX_PENDING_EVENTS = 10000

class RouteOrderingError(ValueError):
    """A route cannot be assigned a bounded causal event order."""

def source_pinned_route_messages(paths, reader, counters):
    if counters:
        raise RouteOrderingError("non-empty-counters")
    counters.update({
        "orderingVersion": 2,
        "verifiedSegments": 0,
        "observations": 0,
        "arrivalInversionsOverHalfSecond": 0,
        "intraSegmentInversions": 0,
        "crossSegmentInversions": 0,
        "maxBufferedEvents": 0,
        "reorderingWindowSeconds": MAX_LAG_SECONDS,
    })
    max_seen = None
    last_arrival = None
    last_arrival_segment = None
    last_emitted = None
    pending = []
    sequence = 0

    for index, path in enumerate(paths):
        headers = 0
        for message in reader(path):
            kind = str(message.which())
            if kind == "initData":
                commit = getattr(message.initData, "gitCommit", None)
                if type(commit) is not str or commit != SCHEMA_COMMIT:
                    raise RouteOrderingError("recorded-source-not-verified")
                headers += 1
                continue
            ns = message.logMonoTime
            if type(ns) is not int or ns < 0:
                raise RouteOrderingError("invalid-event-clock")
            timestamp = ns / 1e9
            if not math.isfinite(timestamp):
                raise RouteOrderingError("invalid-event-clock")
            if max_seen is not None and max_seen - timestamp > MAX_LAG_SECONDS:
                raise RouteOrderingError("lateness-exceeds-window")
            if last_arrival is not None and last_arrival - timestamp > 0.5:
                counters["arrivalInversionsOverHalfSecond"] += 1
                counter = ("intraSegmentInversions" if last_arrival_segment == index
                           else "crossSegmentInversions")
                counters[counter] += 1
            last_arrival = timestamp
            last_arrival_segment = index
            max_seen = timestamp if max_seen is None else max(max_seen, timestamp)
            heapq.heappush(pending, (timestamp, sequence, message))
            counters["observations"] += 1
            sequence += 1
            counters["maxBufferedEvents"] = max(counters["maxBufferedEvents"], len(pending))
            if len(pending) > MAX_PENDING_EVENTS:
                raise RouteOrderingError("bounded-memory-ceiling")
            watermark = max_seen - MAX_LAG_SECONDS
            while pending and pending[0][0] <= watermark:
                stamp, _, outgoing = heapq.heappop(pending)
                if last_emitted is not None and stamp < last_emitted:
                    raise RouteOrderingError("emission-regression")
                last_emitted = stamp
                yield outgoing
        if headers == 0:
            raise RouteOrderingError("missing-source-header")
        counters["verifiedSegments"] += 1

    while pending:
        stamp, _, outgoing = heapq.heappop(pending)
        if last_emitted is not None and stamp < last_emitted:
            raise RouteOrderingError("emission-regression")
        last_emitted = stamp
        yield outgoing
