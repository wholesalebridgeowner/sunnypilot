"""SampleBuilder joins the latest fresh messages onto each carState sample."""
import unittest
from types import SimpleNamespace as ns

from samples import SampleBuilder

CS = ns(vEgo=20.0, aEgo=-0.5, steeringRateDeg=3.0, steeringPressed=False)
T0 = 1_000_000_000


class SampleBuilderTests(unittest.TestCase):
    def test_builds_sample_from_latest_fresh_messages(self):
        b = SampleBuilder(sample_hz=10.0)
        b.ingest("carControl", T0, ns(latActive=True))
        b.ingest("controlsState", T0, ns(desiredCurvature=0.01, curvature=0.009))
        b.ingest("carOutput", T0, ns(actuatorsOutput=ns(torqueOutputCan=-345)))
        b.ingest("radarState", T0, ns(leadOne=ns(status=True, dRel=30.0)))
        b.ingest("drivingModelData", T0, ns(meta=ns(laneChangeState="laneChangeStarting")))
        b.ingest("onroadEvents", T0, [ns(name="steerSaturated")])
        s = b.ingest("carState", T0 + 50_000_000, CS)
        self.assertTrue(s.lat_active)
        self.assertTrue(s.torque_at_limit)
        self.assertTrue(s.lane_change)
        self.assertEqual(s.lead_distance_m, 30.0)
        self.assertEqual(s.desired_curvature, 0.01)
        self.assertEqual(s.alerts, frozenset({"steerSaturated"}))

    def test_rate_limited_to_sample_hz(self):
        b = SampleBuilder(sample_hz=10.0)
        self.assertIsNotNone(b.ingest("carState", 0, CS))
        self.assertIsNone(b.ingest("carState", 50_000_000, CS))
        self.assertIsNotNone(b.ingest("carState", 100_000_000, CS))

    def test_jittered_ten_hz_input_keeps_every_sample(self):
        b = SampleBuilder(sample_hz=10.0)
        stamps = [i * 100_000_000 - (i % 3) * 1_000_000 for i in range(100)]
        self.assertEqual(sum(b.ingest("carState", t, CS) is not None for t in stamps), 100)

    def test_hundred_hz_input_is_reduced_to_ten_hz(self):
        b = SampleBuilder(sample_hz=10.0)
        count = sum(b.ingest("carState", i * 10_000_000, CS) is not None for i in range(1000))
        self.assertAlmostEqual(count, 100, delta=1)

    def test_stale_messages_are_ignored(self):
        b = SampleBuilder(sample_hz=10.0)
        b.ingest("carControl", 0, ns(latActive=True))
        self.assertFalse(b.ingest("carState", 2_000_000_000, CS).lat_active)

    def test_lane_change_off_and_no_lead(self):
        b = SampleBuilder(sample_hz=10.0)
        b.ingest("drivingModelData", 0, ns(meta=ns(laneChangeState="off")))
        b.ingest("radarState", 0, ns(leadOne=ns(status=False, dRel=0.0)))
        s = b.ingest("carState", 10, CS)
        self.assertFalse(s.lane_change)
        self.assertFalse(s.lead_present)
        self.assertIsNone(s.lead_distance_m)


if __name__ == "__main__":
    unittest.main()
