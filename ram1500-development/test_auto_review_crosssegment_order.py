"""Synthetic, privacy-safe tests for bounded cross-segment event ordering."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from auto_review import SCHEMA_COMMIT, summarize_log_messages
from auto_review_crosssegment_order import (
    RouteOrderingError, source_pinned_route_messages, MAX_PENDING_EVENTS,
)

class Event:
    def __init__(self, kind, timestamp, payload=None):
        self.kind = kind
        self.logMonoTime = int(round(timestamp * 1e9))
        setattr(self, kind, payload if payload is not None else {})
    def which(self):
        return self.kind

def header(commit=SCHEMA_COMMIT):
    return Event("initData", -100, SimpleNamespace(gitCommit=commit))

def car(t):
    return Event("carState", t, {"vEgo": 16.0, "aEgo": 0.0,
                                "cruiseState": {"enabled": False}})

class CrossSegmentTests(unittest.TestCase):
    def test_reorders_small_boundary_overlap_losslessly(self):
        def reader(path):
            yield header()
            if Path(path).parent.name == "0":
                yield car(10.0)
                yield car(11.0)
                yield car(12.0)
            else:
                yield car(11.8)
                yield car(12.1)
                yield car(14.0)
        counters = {}
        results = list(source_pinned_route_messages(
            [Path("0/qlog.zst"), Path("1/qlog.zst")], reader, counters))
        self.assertEqual([int(x.logMonoTime / 1e8) for x in results],
                         [100, 110, 118, 120, 121, 140])
        self.assertEqual(counters["verifiedSegments"], 2)
        self.assertEqual(counters["observations"], 6)
        self.assertLessEqual(counters["maxBufferedEvents"], MAX_PENDING_EVENTS)
        summary = summarize_log_messages(results)
        self.assertEqual(summary["messageCounts"]["carState"], 6)
        self.assertEqual(summary["durationSeconds"], 4.0)

    def test_rejects_out_of_window_cross_boundary_jump(self):
        def reader(path):
            yield header()
            yield car(15.0 if Path(path).parent.name == "0" else 11.0)
        with self.assertRaisesRegex(RouteOrderingError, "lateness-exceeds-window"):
            list(source_pinned_route_messages(
                [Path("0/qlog.zst"), Path("1/qlog.zst")], reader, {}))

    def test_rejects_wrong_source_header_and_missing_source_header(self):
        def invalid(path):
            yield header("wrong" if Path(path).parent.name == "1" else SCHEMA_COMMIT)
            yield car(11)
        with self.assertRaisesRegex(RouteOrderingError, "recorded-source-not-verified"):
            list(source_pinned_route_messages(
                [Path("0/qlog.zst"), Path("1/qlog.zst")], invalid, {}))
        def no_header(_):
            yield car(11)
        with self.assertRaisesRegex(RouteOrderingError, "missing-source-header"):
            list(source_pinned_route_messages([Path("0/qlog.zst")], no_header, {}))

    def test_rejects_excessive_pending_events(self):
        def reader(_):
            yield header()
            for _ in range(MAX_PENDING_EVENTS + 1):
                yield car(10)
        with self.assertRaisesRegex(RouteOrderingError, "bounded-memory-ceiling"):
            list(source_pinned_route_messages([Path("0/qlog.zst")], reader, {}))

    def test_no_synthesized_or_dropped_alerts(self):
        def reader(path):
            yield header()
            if Path(path).parent.name == "0":
                yield Event("onroadEvents", 10.5, ["steerSaturated"])
                yield Event("onroadEvents", 11.5, [])
            else:
                yield Event("onroadEvents", 11.2, ["steerSaturated"])
                yield Event("onroadEvents", 12.1, ["steerSaturated"])
        messages = list(source_pinned_route_messages(
            [Path("0/qlog.zst"), Path("1/qlog.zst")], reader, {}))
        self.assertEqual(len(messages), 4)
        summary = summarize_log_messages(messages)
        self.assertEqual(summary["steering"]["steerSaturatedEventEpisodes"], 2)

if __name__ == "__main__":
    unittest.main()
