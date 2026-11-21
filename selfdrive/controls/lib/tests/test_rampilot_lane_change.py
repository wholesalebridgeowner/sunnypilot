"""RamPilot lane-change smoother: only the rate of the request is limited, only during a lane change and its tail.
Runs without cereal or a device: python -m unittest from this folder, or pytest."""
import importlib.util
import math
import pathlib
import re
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("rampilot_lane_change", _HERE.parent / "rampilot_lane_change.py")
rlc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rlc)
LaneChangeSmoother = rlc.LaneChangeSmoother

V = 30.0                      # m/s
STEP = rlc.LC_MAX_LAT_JERK / V ** 2 * rlc.DT_CTRL


class SmootherTests(unittest.TestCase):
    def test_default_rate_for_first_supervised_candidate(self):
        smoother = LaneChangeSmoother()
        for target in (-0.004, 0.004):
            requested = smoother.update(True, V, 0.0, target)
            self.assertAlmostEqual(requested * V ** 2 / rlc.DT_CTRL, math.copysign(0.75, target))

    def test_outside_a_lane_change_the_request_passes_unchanged(self):
        s = LaneChangeSmoother()
        self.assertEqual(s.update(False, V, 0.0, 0.004), 0.004)
        self.assertFalse(s.active)

    def test_during_a_lane_change_only_the_rate_is_limited(self):
        s = LaneChangeSmoother()
        prev, target = 0.0, 0.8 / V ** 2          # a 0.8 m/s^2 request appearing at once
        out = s.update(True, V, prev, target)
        self.assertAlmostEqual(out, STEP)
        for _ in range(200):                      # 2 s later the full request is reached: no cap on size
            prev = s.update(True, V, prev, target)
        self.assertAlmostEqual(prev, target)

    def test_jerk_in_lateral_acceleration_is_the_limit(self):
        s = LaneChangeSmoother()
        prev = 0.0
        for _ in range(10):                       # 0.1 s
            prev = s.update(True, V, prev, 1.0 / V ** 2)
        self.assertAlmostEqual(prev * V ** 2, rlc.LC_MAX_LAT_JERK * 0.1, places=9)

    def test_the_limit_holds_for_the_tail_then_releases(self):
        s = LaneChangeSmoother()
        s.update(True, V, 0.0, 0.0)
        steps = int(round(rlc.LC_TAIL_S / rlc.DT_CTRL))
        for _ in range(steps - 1):
            self.assertAlmostEqual(s.update(False, V, 0.0, 0.01), STEP)
        s.update(False, V, 0.0, 0.01)
        self.assertEqual(s.update(False, V, 0.0, 0.01), 0.01)   # released

    def test_below_the_minimum_speed_nothing_changes(self):
        s = LaneChangeSmoother()
        self.assertEqual(s.update(True, rlc.LC_MIN_SPEED - 0.1, 0.0, 0.01), 0.01)

    def test_crossing_below_threshold_clears_smoothing_during_change_and_tail(self):
        below = math.nextafter(rlc.LC_MIN_SPEED, -math.inf)
        for in_lane_change in (True, False):
            with self.subTest(in_lane_change=in_lane_change):
                s = LaneChangeSmoother()
                prev = s.update(True, V, 0.0, 0.01)
                if not in_lane_change:
                    prev = s.update(False, V, prev, 0.01)
                self.assertTrue(s.active)
                self.assertEqual(s.update(in_lane_change, below, prev, -0.01), -0.01)
                self.assertFalse(s.active)
                self.assertEqual(s.update(False, V, -0.01, 0.02), 0.02)

    def test_active_lane_change_can_reenter_at_exactly_the_threshold(self):
        s = LaneChangeSmoother()
        prev = s.update(True, V, 0.0, 0.01)
        prev = s.update(True, math.nextafter(rlc.LC_MIN_SPEED, -math.inf), prev, 0.01)
        out = s.update(True, rlc.LC_MIN_SPEED, prev, -0.01)
        expected_step = rlc.LC_MAX_LAT_JERK / rlc.LC_MIN_SPEED ** 2 * rlc.DT_CTRL
        self.assertAlmostEqual(out, prev - expected_step)
        self.assertTrue(s.active)

    def test_reset_ends_the_tail(self):
        s = LaneChangeSmoother()
        s.update(True, V, 0.0, 0.0)
        s.reset()
        self.assertEqual(s.update(False, V, 0.0, 0.01), 0.01)

    def test_controlsd_applies_it_before_the_stock_limits_and_never_flags_saturation(self):
        src = (_HERE.parents[1] / "controlsd.py").read_text(encoding="utf-8")
        hook = src.index("self.lc_smoother.update(")
        clip = src.index("clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature")
        self.assertLess(hook, clip)               # before clip_curvature: stock limits still apply after it
        self.assertIsNone(re.search(r"curvature_limited\s*=.*lc_smoother", src))


if __name__ == "__main__":
    unittest.main()
