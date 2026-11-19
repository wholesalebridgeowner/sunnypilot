#!/usr/bin/env python3
"""Source-pinned bounded Q-log timestamp ordering for PRIVATE TRIALS ONLY.

This does not recover an original quarantined route or qualify a vehicle tuning
parameter. It uses non-header logMonoTime to reassemble a bounded interleaved
logger stream. Any source, clock, window, or cross-segment violation fails closed.
"""
from __future__ import annotations
import heapq
import math

from auto_review import SCHEMA_COMMIT

MAX_LAG_SECONDS = 2.0  # observed private clock-audit inversions were all <2 s
MAX_PENDING_EVENTS = 10000


class OrderingTrialError(ValueError):
    """Cannot guarantee safe bounded event-time ordering."""


def source_pinned_bounded_messages(paths, reader, counters):
    """Yield bounded event-time-ordered original messages, never raw serialized data.

    Every segment must contain the full exact recorded-build header. The
    application saves a trial only after exhausting this generator successfully.
    Stable sequence numbers preserve logging order for identical timestamps.
    Within one segment, accept at most 2 seconds of arrival reordering; refuse
    any backward step across segments. No messages are dropped or synthesized.
    """
    if counters:
        raise OrderingTrialError("non-empty-counters")
    counters.update({
        "orderingTrialVersion": 1,
        "sourceCommit": SCHEMA_COMMIT,
        "verifiedSegments": 0,
        "observations": 0,
        "arrivalInversionsOverHalfSecond": 0,
        "maxBufferedEvents": 0,
        "reorderingWindowSeconds": MAX_LAG_SECONDS,
        "caution": "Offline measurement trial only; chronological ordering is not controller-phase evidence",
    })
    last_emitted = None
    for path in paths:
        max_seen = None
        pending = []
        headers = 0
        sequence = 0
        for message in reader(path):
            kind = str(message.which())
            if kind == "initData":
                commit = getattr(message.initData, "gitCommit", None)
                if type(commit) is not str or commit != SCHEMA_COMMIT:
                    raise OrderingTrialError("recorded-source-not-verified")
                headers += 1
                continue
            ns = message.logMonoTime
            if type(ns) is not int or ns < 0:
                raise OrderingTrialError("invalid-event-clock")
            t = ns / 1e9
            if not math.isfinite(t):
                raise OrderingTrialError("non-finite-event-clock")
            if max_seen is not None and max_seen - t > MAX_LAG_SECONDS:
                raise OrderingTrialError("late-event-beyond-window")
            if max_seen is not None and max_seen - t > 0.5:
                counters["arrivalInversionsOverHalfSecond"] += 1
            max_seen = t if max_seen is None else max(t, max_seen)
            heapq.heappush(pending, (t, sequence, message))
            sequence += 1
            counters["observations"] += 1
            counters["maxBufferedEvents"] = max(counters["maxBufferedEvents"], len(pending))
            if len(pending) > MAX_PENDING_EVENTS:
                raise OrderingTrialError("bounded-memory-ceiling")
            cutoff = max_seen - MAX_LAG_SECONDS
            while pending and pending[0][0] <= cutoff:
                stamped, _, output = heapq.heappop(pending)
                if last_emitted is not None and stamped < last_emitted:
                    raise OrderingTrialError("cross-segment-or-watermark-regression")
                last_emitted = stamped
                yield output
        if not headers:
            raise OrderingTrialError("missing-segment-source-header")
        # Explicitly finish one segment before the next: never conceal a
        # non-monotonic cross-segment clock reset with a global reordering.
        while pending:
            stamped, _, output = heapq.heappop(pending)
            if last_emitted is not None and stamped < last_emitted:
                raise OrderingTrialError("cross-segment-or-watermark-regression")
            last_emitted = stamped
            yield output
        counters["verifiedSegments"] += 1
