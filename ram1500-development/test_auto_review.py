"""Fixture-only tests: no Comma, credentials, Railway or real driver inputs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parent))
from auto_review import RouteMetrics, compare
from auto_review_job import group_routes, route_key, MAX_COMPRESSED

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

if __name__ == "__main__":
    unittest.main()
