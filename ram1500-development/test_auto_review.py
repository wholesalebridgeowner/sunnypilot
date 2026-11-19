"""Fixture-only tests: no Comma, credentials, Railway or real driver inputs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import json
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent))
from auto_review import RouteMetrics, compare, summarize_log_messages, SCHEMA_COMMIT
from auto_review_job import (group_routes, route_key, MAX_COMPRESSED,
                             pinned_qlog_messages, SourcePinError, run, PREFIX)

T = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)

def obj(route, segment, hours_ago=12, size=100, etag="v1"):
    return {"Key": f"logs/{route}/{segment}/qlog.zst",
            "Size": size, "LastModified": T - timedelta(hours=hours_ago),
            "ETag": etag}

class AnalysisTests(unittest.TestCase):
    def test_measures_lane_change_headway_and_discrete_alert(self):
        a = RouteMetrics()
        a.ingest("carParams", 0, {"openpilotLongitudinalControl": False, "pcmCruise": True})
        a.ingest("carState", 1, {"vEgo": 20.0, "aEgo": 0.2,
                 "gasPressed": False, "brakePressed": False, "cruiseState": {"enabled": True},
                 "steeringRateDeg": 1.5})
        a.ingest("radarState", 1.1, {"leadOne": {"status": True, "dRel": 32}})
        a.ingest("drivingModelData", 1.15, {"meta": {"laneChangeState": "laneChangeStarting",
                                                      "laneChangeDirection": "left"}})
        a.ingest("carState", 1.2, {"vEgo": 20.0, "aEgo": 0.25, "gasPressed": False,
                 "brakePressed": False, "cruiseState": {"enabled": True},
                 "steeringRateDeg": 4.5})
        a.ingest("onroadEvents", 1.3, ["steerSaturated"])
        a.ingest("onroadEvents", 1.4, ["steerSaturated"])
        a.ingest("drivingModelData", 2.5, {"meta": {"laneChangeState": "laneChangeFinishing",
                                                      "laneChangeDirection": "left"}})
        a.ingest("onroadEvents", 3.1, ["steerSaturated"])
        result = a.report()
        self.assertEqual(result["leadHeadway"]["medianSeconds"], 1.6)
        self.assertEqual(result["laneChange"]["completeStartingWindows"], 1)
        self.assertEqual(result["laneChange"]["p90PeakSteeringRateDegPerSecond"], 4.5)
        self.assertEqual(result["steering"]["steerSaturatedEventEpisodes"], 2)
        self.assertFalse(result["cruiseOwnership"]["carParams"]["openpilotLongitudinalControl"])
        self.assertNotIn("route", result)

    def test_prevents_false_headway_when_driver_presses_pedal(self):
        a = RouteMetrics()
        a.ingest("carState", 1, {"vEgo": 20, "gasPressed": True,
                 "brakePressed": False, "cruiseState": {"enabled": True}})
        a.ingest("radarState", 1.1, {"leadOne": {"status": True, "dRel": 40}})
        self.assertEqual(a.report()["leadHeadway"]["eligibleCruiseSamples"], 0)

    def test_comparison_has_no_unsupported_causal_claim(self):
        current = {"laneChange": {"medianStartingSeconds": 2.4},
                   "steering": {"steerSaturatedEventEpisodes": 2}}
        previous = {"laneChange": {"medianStartingSeconds": 3.1},
                    "steering": {"steerSaturatedEventEpisodes": 4}}
        delta = compare(current, previous)
        self.assertTrue(delta["baselineAvailable"])
        self.assertEqual(delta["metrics"]["laneChange.medianStartingSeconds"]["delta"], -.7)
        self.assertIn("Observational", delta["interpretation"])
        self.assertFalse(compare(current, None)["baselineAvailable"])

    def test_route_gate_requires_contiguous_segs_and_30_minutes_quiet(self):
        route = "device|000003d8--abc"
        self.assertEqual(len(group_routes([obj(route, 0), obj(route, 1)], T)), 1)
        self.assertEqual(group_routes([obj(route, 0), obj(route, 2)], T), [])
        # Important: a fresh segment must not be discarded while earlier segments pass.
        self.assertEqual(group_routes([obj(route, 0), obj(route, 1, hours_ago=0.1)], T), [])
        self.assertEqual(len(group_routes([obj(route, 0, hours_ago=0.75)], T)), 1)
        self.assertEqual(group_routes([obj(route, 0, size=MAX_COMPRESSED+1)], T), [])
        self.assertEqual(group_routes([obj(route, 0, size=0)], T), [])
        self.assertEqual(group_routes([obj(route, 0, hours_ago=400)], T), [])

    def test_route_fingerprint_is_idempotent_and_private(self):
        route = "private-id|000003d8--abc"
        a = group_routes([obj(route, 0)], T)[0]
        b = group_routes([obj(route, 0)], T)[0]
        self.assertEqual(route_key(a), route_key(b))
        self.assertNotIn("private-id", route_key(a))
        changed = group_routes([obj(route, 0, etag="v2")], T)[0]
        self.assertNotEqual(route_key(a), route_key(changed))

    def test_out_of_order_route_fails(self):
        a = RouteMetrics()
        a.ingest("carState", 10, {})
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            a.ingest("carState", 8, {})

class FakeMessage:
    def __init__(self, kind, time, payload):
        self.kind = kind
        self.logMonoTime = int(time * 1_000_000_000)
        setattr(self, kind, payload)

    def which(self):
        return self.kind


def source_header(time=-900, commit=SCHEMA_COMMIT):
    return FakeMessage("initData", time, SimpleNamespace(gitCommit=commit))


def car_state(time):
    return FakeMessage("carState", time, {"vEgo": 20.0,
                       "aEgo": 0.0, "cruiseState": {"enabled": False}})


class MissingKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FixtureS3:
    def __init__(self, objects):
        self.objects = objects
        self.reports = {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **_):
        return [{"Contents": self.objects}]

    def get_object(self, *, Key, **_):
        if Key not in self.reports:
            raise MissingKey()
        return {"Body": SimpleNamespace(read=lambda: json.dumps(self.reports[Key]).encode())}

    def put_object(self, *, Key, Body, **_):
        self.reports[Key] = json.loads(Body)

    def download_file(self, _bucket, _key, output):
        Path(output).write_bytes(b"synthetic")


class ContinuityTests(unittest.TestCase):
    def test_replayed_old_initdata_does_not_shift_or_reverse_route_clock(self):
        def read(path):
            segment = int(Path(path).parent.name)
            yield source_header(-900 if segment else 1)
            yield car_state(10 + 10 * segment)
        data = summarize_log_messages(
            pinned_qlog_messages([Path("0/qlog.zst"), Path("1/qlog.zst")], read))
        self.assertEqual(data["messageCounts"]["carState"], 2)
        self.assertEqual(data["durationSeconds"], 10.0)
        self.assertIn("Q-logs are sampled", " ".join(data["limitations"]))

    def test_missing_or_mismatched_per_segment_source_fails_closed(self):
        def wrong(path):
            if Path(path).parent.name == "1":
                yield source_header(commit="bad")
            else:
                yield source_header()
            yield car_state(10)
        with self.assertRaises(SourcePinError):
            list(pinned_qlog_messages([Path("0/qlog.zst"), Path("1/qlog.zst")], wrong))

        def missing(path):
            if Path(path).parent.name != "1":
                yield source_header()
            yield car_state(10)
        with self.assertRaises(SourcePinError):
            list(pinned_qlog_messages([Path("0/qlog.zst"), Path("1/qlog.zst")], missing))

    def test_real_out_of_order_motion_is_not_silently_sorted(self):
        def read(_):
            yield source_header()
            yield car_state(10)
            yield car_state(8)
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            summarize_log_messages(pinned_qlog_messages([Path("0/qlog.zst")], read))

    def test_heartbeat_is_not_a_second_alert_episode(self):
        review = RouteMetrics()
        review.ingest("onroadEvents", 1.0, ["steerSaturated"])
        review.ingest("onroadEvents", 2.002, ["steerSaturated"])
        self.assertEqual(review.report()["steering"]["steerSaturatedEventEpisodes"], 1)
        review.ingest("onroadEvents", 3.004, [])
        review.ingest("onroadEvents", 4.005, ["steerSaturated"])
        self.assertEqual(review.report()["steering"]["steerSaturatedEventEpisodes"], 2)

    def test_failed_route_written_private_then_skipped_on_next_cron(self):
        s3 = FixtureS3([obj("synthetic|000003d8--abc", 0)])
        def fail(_):
            yield source_header()
            raise ValueError("SENSITIVE_UNTRUSTED_EXCEPTION_MESSAGE")
        first = run(s3, "fake-bucket", T, fail)
        self.assertEqual(first["status"], "route_quarantined")
        self.assertEqual(first["reason"], "decode_or_order_invalid")
        self.assertEqual(len(s3.reports), 1)
        self.assertNotIn("SENSITIVE", json.dumps(s3.reports))
        self.assertNotIn("synthetic|", json.dumps(s3.reports))
        second = run(s3, "fake-bucket", T, fail)
        self.assertEqual(second["status"], "no_new_eligible_routes")

    def test_same_route_reupload_is_not_an_independent_comparison(self):
        s3 = FixtureS3([obj("synthetic|000003d8--abc", 0, etag="v2")])
        candidate = group_routes(s3.objects, T)[0]
        s3.reports[PREFIX + "latest.json"] = {
            "routeHash": candidate["hash"],
            "metrics": {"steering": {"steerSaturatedEventEpisodes": 99}},
            "routeUpdatedAt": (T - timedelta(days=1)).isoformat(),
        }
        def read(_):
            yield source_header()
            yield car_state(10)
        result = run(s3, "fake-bucket", T, read)
        self.assertEqual(result["status"], "review_saved")
        self.assertFalse(result["comparisonBaseline"])
        self.assertEqual(s3.reports[route_key(candidate)]["sourceVerifiedSegments"], 1)
        self.assertNotIn("largeDifference", str(compare(
            {"steering": {"steerSaturatedEventEpisodes": 1}},
            {"steering": {"steerSaturatedEventEpisodes": 0}})))


if __name__ == "__main__":
    unittest.main()
