"""Per-minute metrics from normalized samples."""
import unittest

from metrics import MinuteSummary, Sample

HZ = 10.0


def sample(t, **overrides):
    values = dict(t=t, v_ego=25.0, a_ego=0.0, steering_rate_deg_s=0.0, steering_pressed=False,
                  lat_active=True, desired_curvature=0.0, actual_curvature=0.0, torque_at_limit=False,
                  lead_present=False, lead_distance_m=None, lane_change=False, alerts=frozenset())
    values.update(overrides)
    return Sample(**values)


class MinuteSummaryTests(unittest.TestCase):
    def summarize(self, samples):
        summary = MinuteSummary(start_t=samples[0].t)
        for s in samples:
            summary.add(s)
        return summary.finish(HZ)

    def test_steady_straight_driving_has_no_weaving(self):
        out = self.summarize([sample(i / HZ) for i in range(600)])
        self.assertEqual(out["engagedMinutes"], 1.0)
        self.assertEqual(out["weaveReversals"], 0)
        self.assertEqual(out["weavePerStraightMinute"], 0.0)

    def test_side_to_side_steering_counts_reversals(self):
        rates = [5.0 if (i // 10) % 2 == 0 else -5.0 for i in range(600)]
        out = self.summarize([sample(i / HZ, steering_rate_deg_s=r) for i, r in enumerate(rates)])
        self.assertEqual(out["weaveReversals"], 59)
        self.assertAlmostEqual(out["weavePerStraightMinute"], 59.0)

    def test_small_steering_rates_are_ignored(self):
        rates = [1.0 if i % 2 else -1.0 for i in range(600)]
        out = self.summarize([sample(i / HZ, steering_rate_deg_s=r) for i, r in enumerate(rates)])
        self.assertEqual(out["weaveReversals"], 0)

    def test_curve_relative_error(self):
        out = self.summarize([sample(i / HZ, desired_curvature=0.01, actual_curvature=0.008)
                              for i in range(600)])
        self.assertAlmostEqual(out["curveRelError"], 0.2, places=3)
        self.assertEqual(out["curveSamples"], 600)
        self.assertIsNone(out["weavePerStraightMinute"])

    def test_warning_lane_change_and_hard_brake_count_once(self):
        out = self.summarize([
            sample(0.0),
            sample(0.1, alerts=frozenset({"steerSaturated"}), lane_change=True, a_ego=-4.0,
                   lead_present=True, lead_distance_m=12.34),
            sample(0.2, alerts=frozenset({"steerSaturated"}), lane_change=True, a_ego=-4.5,
                   lead_present=True, lead_distance_m=11.0),
            sample(0.3),
        ])
        self.assertEqual(out["steerWarningEpisodes"], 1)
        self.assertEqual(out["laneChanges"], 1)
        self.assertEqual(out["hardBrakes"], [{"leadPresent": True, "leadDistanceM": 12.3, "speedMs": 25.0}])

    def test_event_spanning_a_minute_boundary_counts_once(self):
        first = MinuteSummary(start_t=0.0)
        first.add(sample(59.9, lane_change=True, alerts=frozenset({"steerSaturated"}), a_ego=-4.0))
        second = MinuteSummary(start_t=60.0, prev=first.last_sample)
        second.add(sample(60.0, lane_change=True, alerts=frozenset({"steerSaturated"}), a_ego=-4.0))
        out = second.finish(HZ)
        self.assertEqual((out["laneChanges"], out["steerWarningEpisodes"], len(out["hardBrakes"])), (0, 0, 0))

    def test_disengaged_driving_does_not_count_for_steering(self):
        out = self.summarize([sample(i / HZ, lat_active=False,
                                     steering_rate_deg_s=5.0 if i % 20 < 10 else -5.0) for i in range(600)])
        self.assertEqual(out["engagedMinutes"], 0.0)
        self.assertEqual(out["weaveReversals"], 0)
        self.assertIsNone(out["torqueLimitFraction"])

    def test_overrides_and_torque_limit(self):
        out = self.summarize([sample(i / HZ, steering_pressed=(10 <= i < 20), torque_at_limit=(i < 60))
                              for i in range(600)])
        self.assertEqual(out["overrideEpisodes"], 1)
        self.assertAlmostEqual(out["torqueLimitFraction"], 0.1)


if __name__ == "__main__":
    unittest.main()
