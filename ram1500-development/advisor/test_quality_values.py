"""What the service makes of the sums of the log-only quality measures."""
import math
import unittest

import quality_values as qv
import shared_rules

LANE = {"n": 400, "sumSq": 0.49, "scoredS": 40.0, "validS": 45.0, "eligibleS": 50.0, "vMean": 30.0,
        "widthMin": 3.4, "widthMax": 3.8, "probMin": 0.93}
SWINGS = {"n": 6, "straightS": 30.0, "vMean": 28.0}
AY = {"n": 400, "s": 40.0, "sumSq": 0.36, "planSumSq": 1.0, "vMean": 30.0}
CURVE = {"s": 20.0, "events": 2, "belowSum": 1.6, "aboveSum": 0.4, "s04": 16.0, "below04Sum": 0.8, "above04Sum": 0.16,
         "lowS": 4.0, "lowLimitS": 1.0, "lowBelowSum": 1.2, "lowAboveSum": 0.0}


def lag_block(lag_s=0.37, gain=0.9, n=600):
    """Lag sums of a demand x(t) and y(t) = gain x x(t - lag_s), sampled every 0.1 s."""
    x = lambda t: 1.2 + 0.6 * math.sin(2 * math.pi * 0.2 * t)
    times = [10.0 + 0.1 * i for i in range(n)]
    ys = [gain * x(t - lag_s) for t in times]
    return {"s": 0.1 * n, "n": n, "syy": sum(y * y for y in ys),
            "sxy": [sum(y * x(t - 0.1 * k) for t, y in zip(times, ys)) for k in range(11)],
            "sxx": [sum(x(t - 0.1 * k) ** 2 for t in times) for k in range(11)]}


class PoolTests(unittest.TestCase):
    def test_nothing_to_pool(self):
        self.assertIsNone(qv.pool("lane", []))
        self.assertIsNone(qv.pool("lane", [None, None]))

    def test_one_block_stays_as_it_is(self):
        for name, block in (("lane", LANE), ("swings", SWINGS), ("ay", AY), ("curve", CURVE)):
            with self.subTest(block=name):
                self.assertEqual(qv.pool(name, [None, block]), block)

    def test_sums_add_up(self):
        pooled = qv.pool("curve", [CURVE, CURVE, None, CURVE])
        self.assertEqual(pooled["events"], 6)
        self.assertAlmostEqual(pooled["s"], 60.0)
        self.assertAlmostEqual(pooled["lowBelowSum"], 3.6)

    def test_lane_blocks(self):
        other = dict(LANE, n=100, sumSq=0.01, scoredS=10.0, vMean=20.0, widthMin=3.1, widthMax=3.6, probMin=0.97)
        pooled = qv.pool("lane", [LANE, other])
        self.assertEqual((pooled["n"], pooled["scoredS"]), (500, 50.0))
        self.assertAlmostEqual(pooled["sumSq"], 0.50)
        self.assertAlmostEqual(pooled["vMean"], (400 * 30.0 + 100 * 20.0) / 500)       # by scored messages
        self.assertEqual((pooled["widthMin"], pooled["widthMax"], pooled["probMin"]), (3.1, 3.8, 0.93))

    def test_a_block_without_a_scored_message_has_no_speed_to_pool(self):
        empty = dict(LANE, n=0, sumSq=0.0, scoredS=0.0, vMean=None, widthMin=None, widthMax=None, probMin=None)
        pooled = qv.pool("lane", [empty, LANE])
        self.assertEqual((pooled["vMean"], pooled["widthMin"], pooled["probMin"]), (30.0, 3.4, 0.93))
        alone = qv.pool("lane", [empty, empty])
        self.assertEqual((alone["vMean"], alone["widthMin"], alone["widthMax"], alone["probMin"]),
                         (None, None, None, None))

    def test_swing_blocks_pool_their_speed_by_time(self):
        pooled = qv.pool("swings", [SWINGS, {"n": 5, "straightS": 10.0, "vMean": 20.0}])
        self.assertEqual((pooled["n"], pooled["straightS"]), (11, 40.0))
        self.assertAlmostEqual(pooled["vMean"], (30.0 * 28.0 + 10.0 * 20.0) / 40.0)

    def test_lateral_rms_blocks_pool_their_speed_by_sample(self):
        pooled = qv.pool("ay", [AY, {"n": 100, "s": 20.0, "sumSq": 0.04, "planSumSq": 0.1, "vMean": 20.0}])
        self.assertEqual((pooled["n"], pooled["s"]), (500, 60.0))
        self.assertAlmostEqual(pooled["sumSq"], 0.40)
        self.assertAlmostEqual(pooled["vMean"], (400 * 30.0 + 100 * 20.0) / 500)       # by accepted Samples

    def test_lag_blocks_add_step_by_step(self):
        a, b = lag_block(n=300), lag_block(n=200)
        pooled = qv.pool("lag", [a, b])
        self.assertEqual(pooled["n"], 500)
        self.assertEqual(len(pooled["sxy"]), 11)
        for k in range(11):
            self.assertAlmostEqual(pooled["sxy"][k], a["sxy"][k] + b["sxy"][k])
            self.assertAlmostEqual(pooled["sxx"][k], a["sxx"][k] + b["sxx"][k])
        self.assertEqual(len(a["sxy"]), 11)                           # the blocks themselves are left alone


class ValueTests(unittest.TestCase):
    def test_minimum_data_constants(self):
        self.assertEqual((shared_rules.LANE_MIN_SCORED_S, shared_rules.SWING_MIN_STRAIGHT_S, shared_rules.AY_MIN_S),
                         (20.0, 15.0, 30.0))
        self.assertEqual((shared_rules.AY_PERIOD_MIN_S, shared_rules.AY_PERIOD_MAX_S), (0.095, 0.125))

    def test_lane_sway(self):
        self.assertAlmostEqual(qv.lane_sway_sd(LANE), 0.035)
        self.assertAlmostEqual(qv.lane_sway_sd(dict(LANE, scoredS=20.0)), 0.035)
        self.assertIsNone(qv.lane_sway_sd(dict(LANE, scoredS=19.9)))
        self.assertAlmostEqual(qv.lane_sway_sd(dict(LANE, scoredS=3.0), min_scored_s=0.0), 0.035)
        self.assertIsNone(qv.lane_sway_sd(dict(LANE, n=0, sumSq=0.0, scoredS=0.0), min_scored_s=0.0))
        self.assertIsNone(qv.lane_sway_sd(None))

    def test_swing_rate(self):
        self.assertAlmostEqual(qv.swing_rate(SWINGS), 12.0)           # 6 swings in half a straight minute
        self.assertAlmostEqual(qv.swing_rate(dict(SWINGS, straightS=15.0)), 24.0)
        self.assertIsNone(qv.swing_rate(dict(SWINGS, straightS=14.9)))
        self.assertAlmostEqual(qv.swing_rate(dict(SWINGS, straightS=6.0), min_straight_s=0.0), 60.0)
        self.assertIsNone(qv.swing_rate(dict(SWINGS, n=0, straightS=0.0), min_straight_s=0.0))
        self.assertEqual(qv.swing_rate(dict(SWINGS, n=0)), 0.0)
        self.assertIsNone(qv.swing_rate(None))

    def test_lateral_rms(self):
        actual, planned = qv.lat_accel_rms(AY)
        self.assertAlmostEqual(actual, 0.03)
        self.assertAlmostEqual(planned, 0.05)
        self.assertIsNotNone(qv.lat_accel_rms(dict(AY, s=30.0)))
        self.assertIsNone(qv.lat_accel_rms(dict(AY, s=29.9)))
        self.assertIsNotNone(qv.lat_accel_rms(dict(AY, s=5.0), min_s=0.0))
        self.assertIsNone(qv.lat_accel_rms(dict(AY, n=0, s=0.0), min_s=0.0))
        self.assertIsNone(qv.lat_accel_rms(None))

    def test_lateral_rms_only_from_a_window_whose_loop_ran_near_ten_a_second(self):
        for period, scored in ((0.0949, False), (0.095, True), (0.1023, True), (0.125, True), (0.1251, False)):
            with self.subTest(period=period):
                self.assertEqual(qv.lat_accel_rms(AY, loop_period_s=period) is not None, scored)

    def test_curve_tracking(self):
        values = qv.curve_tracking(CURVE)
        self.assertAlmostEqual(values["below"], 0.08)
        self.assertAlmostEqual(values["above"], 0.02)
        self.assertAlmostEqual(values["below04"], 0.05)
        self.assertAlmostEqual(values["above04"], 0.01)
        self.assertAlmostEqual(values["lowBelow"], 0.3)
        self.assertEqual(values["lowAbove"], 0.0)

    def test_curve_tracking_without_seconds(self):
        values = qv.curve_tracking(dict(CURVE, s=0.0, belowSum=0.0, aboveSum=0.0, s04=0.0, lowS=0.0))
        self.assertEqual(set(values.values()), {None})
        self.assertEqual(set(qv.curve_tracking(None).values()), {None})
        self.assertEqual(set(qv.curve_tracking(None)), set(values))

    def test_lag_fit(self):
        for lag_s in (0.0, 0.08, 0.37, 0.50, 0.93, 1.0):
            with self.subTest(lag_s=lag_s):
                lag, gain = qv.lag_fit(lag_block(lag_s))
                self.assertAlmostEqual(lag, lag_s, delta=0.006)
                self.assertAlmostEqual(gain, 0.9, delta=0.02)

    def test_lag_fit_between_two_steps_is_refined_by_a_parabola(self):
        lag, _ = qv.lag_fit(lag_block(0.37))
        self.assertNotAlmostEqual(lag, 0.4, delta=0.01)
        whole, _ = qv.lag_fit(lag_block(0.40))
        self.assertAlmostEqual(whole, 0.40, delta=1e-6)

    def test_at_the_first_and_the_last_step_there_is_no_parabola(self):
        self.assertEqual(qv.lag_fit(lag_block(1.3))[0], 1.0)          # longer than the last step
        self.assertEqual(qv.lag_fit(lag_block(0.03))[0], 0.0)         # nearer to no lag than to one step

    def test_lag_fit_without_data(self):
        self.assertIsNone(qv.lag_fit(None))
        self.assertIsNone(qv.lag_fit({"s": 0.0, "n": 0, "syy": 0.0, "sxy": [0.0] * 11, "sxx": [0.0] * 11}))
        flat = lag_block()
        flat["sxx"][3] = 0.0                                          # a step with no demand at all
        self.assertIsNone(qv.lag_fit(flat))

    def test_pooled_lag(self):
        lag, gain = qv.lag_fit(qv.pool("lag", [lag_block(0.37, n=300), lag_block(0.37, n=250)]))
        self.assertAlmostEqual(lag, 0.37, delta=0.006)


if __name__ == "__main__":
    unittest.main()
