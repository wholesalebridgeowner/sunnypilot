"""Curve sums and lag sums: actual cornering against what was commanded, and the sums behind a response lag."""
import math
import unittest

import quality_values
import synth
from measure_curve import CurveCore, CurveLag, CurveSums, LagCore
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
V = 25.0
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}
CURVE_FIELDS = {"s", "events", "belowSum", "aboveSum", "s04", "below04Sum", "above04Sum", "lowS", "lowLimitS",
                "lowBelowSum", "lowAboveSum"}
LAG_FIELDS = {"s", "n", "syy", "sxy", "sxx"}


def span(*pieces):
    """A value over time: (from_s, to_s, value) pieces, zero elsewhere."""
    def at(t_s):
        for start, end, value in pieces:
            if start <= t_s < end:
                return value
        return 0.0
    return at


def delayed(f, by_s):
    return lambda t_s: f(t_s - by_s)


def feed(core, demand, actual=None, n=300, period_ms=100, v=V, accept=lambda t_s: True, key=lambda t_s: 0,
         limit=lambda t_s: False, clean=None, times=None, lag=False):
    """Demand and actual lateral acceleration (m/s^2) over time, through a core as curvatures."""
    times = times or [T0 + round(i * period_ms * MS) for i in range(n)]
    prev = None
    for t_ns in times:
        t_s = (t_ns - T0) / SEC
        dt = 0.1 if prev is None else (t_ns - prev) / SEC
        prev = t_ns
        x, y = demand(t_s), (actual or demand)(t_s)
        speed = v(t_s) if callable(v) else v
        values = (speed, None if x is None else x / speed ** 2, None if y is None else y / speed ** 2, dt, limit(t_s))
        if not lag:
            values += ((accept if clean is None else clean)(t_s),)
        core.add(t_ns, values, accept(t_s), key(t_s))
    return core.flush()


def batches(demand=None, actual=None, n=600, period_ms=100.0, v=V, extra=None, times=None):
    times = times or synth.ticks(n, period_ms, T0)

    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        out = dict(extra(t_s) or {}) if extra else {}
        x = demand(t_s) if demand else 0.0
        y = (actual or demand)(t_s) if (actual or demand) else 0.0
        out.setdefault("carState", dict(v=v))
        out.setdefault("controlsState", dict(desired=x / (v * v), actual=y / (v * v)))
        return out
    return synth.drive(times, change)


def run(demand=None, actual=None, measures=(CurveSums, CurveLag), **more):
    """(summaries, pooled curve block, pooled lag block) of one synthetic drive."""
    out = synth.summaries(synth.run(Pipeline(PipelineConfig(measures=measures)), batches(demand, actual, **more)))
    return (out, quality_values.pool("curve", [s.get("curve") for s in out]),
            quality_values.pool("lag", [s.get("lag") for s in out]))


class CurveCoreTests(unittest.TestCase):
    def test_actual_below_what_was_commanded(self):
        block = feed(CurveCore(), lambda t_s: 1.0, lambda t_s: 0.9, n=100)[0]
        self.assertAlmostEqual(block["s"], 10.0, places=9)
        self.assertAlmostEqual(block["belowSum"], 1.0, places=9)      # 0.10 m/s^2 for 10 s
        self.assertEqual(block["aboveSum"], 0.0)
        self.assertEqual(block["events"], 1)

    def test_actual_above_what_was_commanded(self):
        block = feed(CurveCore(), lambda t_s: 1.0, lambda t_s: 1.25, n=100)[0]
        self.assertAlmostEqual(block["aboveSum"], 2.5, places=9)
        self.assertEqual(block["belowSum"], 0.0)
        self.assertAlmostEqual(block["above04Sum"], 0.25 * 9.6, places=9)         # and against the earlier demand
        self.assertEqual(block["below04Sum"], 0.0)

    def test_a_curve_to_the_other_side_reads_the_same(self):
        block = feed(CurveCore(), lambda t_s: -1.0, lambda t_s: -0.9, n=100)[0]
        self.assertAlmostEqual(block["belowSum"], 1.0, places=9)
        self.assertEqual(block["aboveSum"], 0.0)
        block = feed(CurveCore(), lambda t_s: -1.0, lambda t_s: -1.25, n=100)[0]
        self.assertAlmostEqual(block["aboveSum"], 2.5, places=9)

    def test_a_curve_starts_at_half_a_metre_per_second_squared(self):
        self.assertAlmostEqual(feed(CurveCore(), lambda t_s: 0.5, n=100)[0]["s"], 10.0, places=9)
        self.assertEqual(feed(CurveCore(), lambda t_s: 0.499, n=100), {})
        self.assertEqual(feed(CurveCore(), lambda t_s: -0.499, n=100), {})

    def test_only_accepted_samples_are_curve_samples(self):
        block = feed(CurveCore(), lambda t_s: 1.0, lambda t_s: 0.9, n=100, accept=lambda t_s: t_s >= 6.0,
                     clean=lambda t_s: False)[0]
        self.assertAlmostEqual(block["s"], 4.0, places=9)
        self.assertAlmostEqual(block["belowSum"], 0.4, places=9)

    def test_two_curves_are_one_event_up_to_3_15_s_apart(self):
        for apart_s, events in ((3.1, 1), (3.2, 2)):
            with self.subTest(apart_s=apart_s):
                second = round(11.9 + apart_s, 1)                     # the first curve's last Sample is at 11.9 s
                block = feed(CurveCore(), span((10.0, 12.0, 1.0), (second, second + 2.0, 1.0)), n=300)[0]
                self.assertEqual(block["events"], events)
                self.assertAlmostEqual(block["s"], 4.0, places=9)

    def test_a_curve_exactly_3_15_s_after_the_last_one_is_still_the_same_event(self):
        first = [T0 + i * 100 * MS for i in range(20)]
        for extra_ns, events in ((0, 1), (1, 2)):
            with self.subTest(extra_ns=extra_ns):
                second = [first[-1] + 3150 * MS + extra_ns + i * 100 * MS for i in range(20)]
                self.assertEqual(feed(CurveCore(), lambda t_s: 1.0, times=first + second)[0]["events"], events)

    def test_the_event_count_books_to_the_window_the_curve_starts_in(self):
        booked = feed(CurveCore(), span((10.0, 14.0, 1.0)), n=300, key=lambda t_s: int(t_s // 12))
        self.assertEqual((booked[0]["events"], booked[1]["events"]), (1, 0))
        self.assertAlmostEqual(booked[1]["s"], 2.0, places=9)

    def test_a_new_run_does_not_start_a_new_event_by_itself(self):
        core = CurveCore()
        feed(core, lambda t_s: 1.0, n=20)
        core.new_run()
        block = feed(core, lambda t_s: 1.0, times=[T0 + (20 + i) * 100 * MS for i in range(20)])[0]
        self.assertEqual(block["events"], 0)                          # 0.1 s after the last curve Sample

    def test_the_delayed_pair_takes_the_curve_entry_out(self):
        # The demand steps to 1.0 for 5 s; actual cornering follows 0.4 s later.
        demand = span((10.0, 15.0, 1.0))
        block = feed(CurveCore(), demand, delayed(demand, 0.4), n=300)[0]
        self.assertAlmostEqual(block["s"], 5.0, places=9)
        self.assertAlmostEqual(block["belowSum"], 0.4, places=9)      # the first 0.4 s with nothing delivered yet
        self.assertEqual(block["aboveSum"], 0.0)
        self.assertAlmostEqual(block["s04"], 5.0, places=9)           # 10.4 s to 15.4 s: chosen on the earlier demand
        self.assertEqual((block["below04Sum"], block["above04Sum"]), (0.0, 0.0))

    def test_the_delayed_pair_reads_a_steady_shortfall(self):
        demand = span((10.0, 15.0, 1.0))
        block = feed(CurveCore(), demand, lambda t_s: 0.9 * demand(t_s - 0.4), n=300)[0]
        self.assertAlmostEqual(block["below04Sum"], 0.5, places=9)    # 0.10 m/s^2 for 5 s
        self.assertAlmostEqual(block["belowSum"], 0.4 + 0.46, places=9)

    def test_the_earlier_demand_is_read_by_time_stamp(self):
        # A demand that grows steadily, at a loop of 0.105 s: 0.4 s ago falls between two Samples.
        demand = lambda t_s: 0.6 + 0.2 * t_s
        block = feed(CurveCore(), demand, delayed(demand, 0.4), n=100, period_ms=105)[0]
        self.assertAlmostEqual(block["s04"], (100 - 4) * 0.105, places=9)
        self.assertLess(block["below04Sum"] + block["above04Sum"], 1e-9)
        self.assertAlmostEqual(block["belowSum"], 0.08 * 100 * 0.105 - 0.08 * 0.005, places=6)    # 0.2 x 0.4 behind

    def test_the_sample_0_4_s_back_must_have_been_accepted_too(self):
        demand = lambda t_s: 1.0
        block = feed(CurveCore(), demand, lambda t_s: 0.9, n=100, accept=lambda t_s: t_s >= 6.0)[0]
        self.assertAlmostEqual(block["s"], 4.0, places=9)
        self.assertAlmostEqual(block["s04"], 3.6, places=9)           # from 6.4 s on
        gap_in_acceptance = feed(CurveCore(), demand, lambda t_s: 0.9, n=100,
                                 accept=lambda t_s: not 6.0 <= t_s < 6.1)[0]
        self.assertAlmostEqual(gap_in_acceptance["s04"], 10.0 - 0.4 - 0.1 - 0.1, places=9)   # 6.0 s and 6.4 s are out

    def test_the_sample_that_stands_for_0_4_s_ago_is_at_least_0_38_s_old(self):
        # Samples 0.13 s apart: 0.39 s back is taken as it is, 0.4 s ago being 0.01 s before it.
        times = [T0 + i * 130 * MS for i in range(60)]
        demand = lambda t_s: 0.6 + 0.2 * t_s
        block = feed(CurveCore(), demand, lambda t_s: 0.0, times=times)[0]
        want = sum((demand(i * 0.13 - 0.39)) * 0.13 for i in range(3, 60))
        self.assertAlmostEqual(block["below04Sum"], want, places=9)
        # Samples 0.125 s apart: 0.375 s back is too young; the one before it and the next one are interpolated.
        times = [T0 + i * 125 * MS for i in range(60)]
        block = feed(CurveCore(), demand, lambda t_s: 0.0, times=times)[0]
        want = sum((demand(i * 0.125 - 0.4)) * 0.125 for i in range(4, 60))
        self.assertAlmostEqual(block["below04Sum"], want, places=9)

    def test_a_new_run_forgets_the_earlier_demands(self):
        core = CurveCore()
        feed(core, lambda t_s: 1.0, n=50)
        core.new_run()
        self.assertEqual(core.held, 0)
        block = feed(core, lambda t_s: 1.0, lambda t_s: 0.9, times=[T0 + (53 + i) * 100 * MS for i in range(50)])[0]
        self.assertAlmostEqual(block["s"], 5.0, places=9)
        self.assertAlmostEqual(block["s04"], 4.6, places=9)

    def test_the_history_stays_short(self):
        core = CurveCore()
        feed(core, lambda t_s: 1.0, n=2000)
        self.assertLessEqual(core.held, 14)

    def test_the_low_speed_line(self):
        # 10 m/s: clean, but under the speed floor of the curve sums.
        low = dict(v=10.0, accept=lambda t_s: False, clean=lambda t_s: True)
        block = feed(CurveCore(), lambda t_s: 1.0, lambda t_s: 0.7, n=100, limit=lambda t_s: t_s < 2.0, **low)[0]
        self.assertAlmostEqual(block["lowS"], 10.0, places=9)
        self.assertAlmostEqual(block["lowBelowSum"], 3.0, places=9)
        self.assertEqual(block["lowAboveSum"], 0.0)
        self.assertAlmostEqual(block["lowLimitS"], 2.0, places=9)
        self.assertEqual((block["s"], block["s04"], block["events"]), (0.0, 0.0, 0))
        above = feed(CurveCore(), lambda t_s: -1.0, lambda t_s: -1.2, n=100, **low)[0]
        self.assertAlmostEqual(above["lowAboveSum"], 2.0, places=9)

    def test_the_low_speed_line_runs_from_5_to_15_metres_per_second(self):
        low = dict(accept=lambda t_s: False, clean=lambda t_s: True)
        for v, counted in ((4.99, False), (5.0, True), (14.99, True), (15.0, False)):
            with self.subTest(v=v):
                booked = feed(CurveCore(), lambda t_s: 1.0, n=50, v=v, **low)
                self.assertEqual(bool(booked), counted)
        self.assertEqual(feed(CurveCore(), lambda t_s: 0.49, n=50, v=10.0, **low), {})
        self.assertEqual(feed(CurveCore(), lambda t_s: 1.0, n=50, v=10.0, accept=lambda t_s: False), {})   # not clean

    def test_a_sample_at_the_limit_only_matters_on_the_low_speed_line(self):
        block = feed(CurveCore(), lambda t_s: 1.0, lambda t_s: 0.9, n=100, limit=lambda t_s: True)[0]
        self.assertAlmostEqual(block["s"], 10.0, places=9)
        self.assertEqual(block["lowLimitS"], 0.0)

    def test_an_unknown_curvature_is_never_a_curve_sample(self):
        demand = lambda t_s: None if 5.0 <= t_s < 6.0 else 1.0
        block = feed(CurveCore(), demand, lambda t_s: 0.9, n=100)[0]
        self.assertAlmostEqual(block["s"], 9.0, places=9)
        self.assertAlmostEqual(block["s04"], 10.0 - 0.4 - 1.0 - 0.4, places=9)
        self.assertEqual(feed(CurveCore(), lambda t_s: 1.0, lambda t_s: None, n=50, clean=lambda t_s: True), {})

    def test_each_sample_books_to_the_key_it_is_given(self):
        booked = feed(CurveCore(), lambda t_s: 1.0, lambda t_s: 0.9, n=100, key=lambda t_s: "a" if t_s < 4 else "b")
        self.assertAlmostEqual(booked["a"]["s"], 4.0, places=9)
        self.assertAlmostEqual(booked["b"]["s"], 6.0, places=9)
        self.assertAlmostEqual(booked["a"]["s04"] + booked["b"]["s04"], 9.6, places=9)

    def test_nothing_to_say_books_nothing(self):
        self.assertEqual(feed(CurveCore(), lambda t_s: 0.0, n=100), {})

    def test_the_block_fields(self):
        self.assertEqual(set(feed(CurveCore(), lambda t_s: 1.0, n=10)[0]), CURVE_FIELDS)


def lag_feed(demand, actual=None, **more):
    return feed(LagCore(), demand, actual, lag=True, **more)


class LagCoreTests(unittest.TestCase):
    def test_a_lag_sample_needs_0_98_s_of_good_samples_behind_it(self):
        block = lag_feed(lambda t_s: 1.0, n=100)[0]
        self.assertEqual(block["n"], 90)                              # from the Sample at 1.0 s on
        self.assertAlmostEqual(block["s"], 9.0, places=9)
        self.assertEqual((len(block["sxy"]), len(block["sxx"])), (11, 11))

    def test_the_sums(self):
        block = lag_feed(lambda t_s: 1.0, lambda t_s: 0.8, n=100)[0]
        self.assertAlmostEqual(block["syy"], 90 * 0.64, places=9)
        for k in range(11):
            self.assertAlmostEqual(block["sxy"][k], 90 * 0.8, places=9)
            self.assertAlmostEqual(block["sxx"][k], 90 * 1.0, places=9)

    def test_the_demand_at_each_step_back(self):
        # A steadily growing demand and exactly one lag Sample: x_k is the demand 0.1 k s earlier.
        demand = lambda t_s: 0.6 + 0.2 * t_s
        for period_ms, n in ((100, 11), (105, 11), (120, 10), (140, 8)):
            with self.subTest(period_ms=period_ms):
                block = lag_feed(demand, lambda t_s: 1.0, n=n, period_ms=period_ms)[0]
                self.assertEqual(block["n"], 1)
                t_s = (n - 1) * period_ms / 1000
                oldest_s = max(i * period_ms / 1000 for i in range(n) if i * period_ms / 1000 <= t_s - 0.98 + 1e-9)
                for k in range(11):
                    want = demand(max(t_s - 0.1 * k, oldest_s))      # the oldest Sample in use stands in for 0.02 s
                    self.assertAlmostEqual(block["sxy"][k], want, places=9)
                    self.assertAlmostEqual(block["sxx"][k], want * want, places=9)

    def test_the_oldest_sample_in_use_is_the_newest_one_at_least_0_98_s_old(self):
        # At 0.14 s spacing the seventh Sample back is exactly 0.98 s old. Two lag Samples, at 0.98 s and at
        # 1.12 s: the second rests on the Sample at 0.14 s although the one at 0.0 s is held too, and that Sample
        # stands in for the demand 1.0 s ago (0.02 s before it).
        demand = lambda t_s: 0.6 + 0.2 * t_s
        block = lag_feed(demand, lambda t_s: 1.0, n=9, period_ms=140)[0]
        self.assertEqual(block["n"], 2)
        self.assertAlmostEqual(block["sxy"][10], demand(0.0) + demand(0.14), places=9)
        self.assertAlmostEqual(block["sxy"][9], demand(0.98 - 0.9) + demand(1.12 - 0.9), places=9)
        self.assertAlmostEqual(block["sxy"][0], demand(0.98) + demand(1.12), places=9)

    def test_one_sample_that_is_not_accepted_breaks_the_stretch(self):
        block = lag_feed(lambda t_s: 1.0, n=100, accept=lambda t_s: not 5.0 <= t_s < 5.1)[0]
        self.assertEqual(block["n"], 90 - 1 - 10)                     # 5.0 s itself and the ten Samples after it

    def test_a_sample_at_the_limit_breaks_the_stretch(self):
        block = lag_feed(lambda t_s: 1.0, n=100, limit=lambda t_s: 5.0 <= t_s < 5.1)[0]
        self.assertEqual(block["n"], 90 - 1 - 10)

    def test_a_step_of_more_than_0_15_s_breaks_the_stretch(self):
        ok = [T0 + i * 100 * MS for i in range(50)] + [T0 + (4900 + 150 + i * 100) * MS for i in range(50)]
        self.assertEqual(lag_feed(lambda t_s: 1.0, times=ok)[0]["n"], 90)
        broken = [T0 + i * 100 * MS for i in range(50)] + [T0 + (4900 + 151 + i * 100) * MS for i in range(50)]
        # The Sample after the long step can be the oldest one of a new stretch, not part of the old one.
        self.assertEqual(lag_feed(lambda t_s: 1.0, times=broken)[0]["n"], 40 + 40)

    def test_lag_samples_stop_3_05_s_after_the_last_curve_sample(self):
        # A curve up to 9.9 s, then straight on.
        block = lag_feed(span((0.0, 10.0, 1.0)), n=200)[0]
        self.assertEqual(block["n"], 90 + 30)                         # up to the Sample at 12.9 s
        self.assertEqual(lag_feed(lambda t_s: 0.4, n=200), {})        # never a curve
        late = lag_feed(span((10.0, 20.0, 1.0)), n=200)[0]
        self.assertEqual(late["n"], 100)                              # the stretch began long before the curve

    def test_a_sample_exactly_3_05_s_after_the_last_curve_sample_is_a_lag_sample(self):
        # Samples every 0.05 s; the curve ends with the Sample at 10.0 s.
        block = lag_feed(lambda t_s: 1.0 if t_s <= 10.0 else 0.0, n=400, period_ms=50)[0]
        self.assertEqual(block["n"], 261 - 20 + 1)                    # from 1.0 s to 13.05 s, both included

    def test_a_curve_sample_must_have_been_accepted(self):
        self.assertEqual(lag_feed(lambda t_s: 1.0 if t_s < 5.0 else 0.0, n=200, accept=lambda t_s: t_s >= 5.0), {})

    def test_an_unknown_curvature_breaks_the_stretch(self):
        block = lag_feed(lambda t_s: None if 5.0 <= t_s < 5.1 else 1.0, n=100)[0]
        self.assertEqual(block["n"], 90 - 1 - 10)

    def test_a_new_run_starts_the_stretch_again(self):
        core = LagCore()
        feed(core, lambda t_s: 1.0, n=50, lag=True)
        core.new_run()
        self.assertEqual(core.held, 0)
        block = feed(core, lambda t_s: 1.0, times=[T0 + (50 + i) * 100 * MS for i in range(50)], lag=True)[0]
        self.assertEqual(block["n"], 40)

    def test_the_history_stays_short(self):
        core = LagCore()
        feed(core, lambda t_s: 1.0, n=2000, lag=True)
        self.assertLessEqual(core.held, 14)

    def test_each_sample_books_to_the_key_it_is_given_and_flush_keeps_the_stretch(self):
        booked = lag_feed(lambda t_s: 1.0, n=100, key=lambda t_s: int(t_s // 5))
        self.assertEqual((booked[0]["n"], booked[1]["n"]), (40, 50))
        core = LagCore()
        feed(core, lambda t_s: 1.0, n=50, lag=True)
        block = feed(core, lambda t_s: 1.0, times=[T0 + (50 + i) * 100 * MS for i in range(50)], lag=True)[0]
        self.assertEqual(block["n"], 50)

    def test_the_fitted_lag(self):
        demand = lambda t_s: 1.2 + 0.6 * math.sin(2 * math.pi * 0.2 * t_s)
        for lag_s in (0.0, 0.25, 0.40, 0.55):
            with self.subTest(lag_s=lag_s):
                block = lag_feed(demand, lambda t_s: 0.9 * demand(t_s - lag_s), n=600)[0]
                lag, gain = quality_values.lag_fit(block)
                self.assertAlmostEqual(lag, lag_s, delta=0.01)
                self.assertAlmostEqual(gain, 0.9, delta=0.01)


class CurveMeasureTests(unittest.TestCase):
    def test_they_are_log_only_measures_named_after_their_blocks(self):
        self.assertEqual((CurveSums.name, CurveSums.owner_facing), ("curve", False))
        self.assertEqual((CurveLag.name, CurveLag.owner_facing), ("lag", False))

    def test_the_blocks_and_their_rounding(self):
        demand = lambda t_s: 1.2 + 0.6 * math.sin(2 * math.pi * 0.2 * t_s)
        out, curve, lag = run(demand, lambda t_s: 0.93 * demand(t_s - 0.37), n=700)
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"curve", "lag"})
        block = out[0]["curve"]
        self.assertEqual(set(block), CURVE_FIELDS)
        for key in ("s", "s04", "lowS", "lowLimitS"):
            self.assertEqual(block[key], round(block[key], 1))
        for key in ("belowSum", "aboveSum", "below04Sum", "above04Sum", "lowBelowSum", "lowAboveSum"):
            self.assertEqual(block[key], round(block[key], 4))
        for key in ("belowSum", "below04Sum"):
            self.assertNotEqual(block[key], round(block[key], 3))     # four decimals, no fewer
        self.assertEqual((block["s"], block["events"]), (48.9, 1))    # the Samples from 5.1 s to 53.9 s
        self.assertEqual(block["s04"], 48.5)
        lag_block = out[0]["lag"]
        self.assertEqual(set(lag_block), LAG_FIELDS)
        self.assertEqual((lag_block["s"], lag_block["n"]), (47.9, 479))
        for value in [lag_block["syy"]] + lag_block["sxy"] + lag_block["sxx"]:
            self.assertEqual(value, float("%.5g" % value))            # five significant digits
        self.assertTrue(all(value != float("%.4g" % value) for value in [lag_block["syy"]] + lag_block["sxy"][:3]))
        self.assertEqual((len(lag_block["sxy"]), len(lag_block["sxx"])), (11, 11))

    def test_seconds_are_sent_with_one_decimal(self):
        out, curve, lag = run(lambda t_s: 1.0, lambda t_s: 0.9, n=700, period_ms=103.0)
        self.assertEqual(out[0]["curve"]["s"], 48.9)                  # 475 Samples of 0.103 s: 48.925 s
        self.assertEqual(out[0]["curve"]["s04"], 48.5)                # four fewer: 48.513 s
        self.assertEqual(out[0]["lag"]["s"], 47.9)                    # ten fewer: 47.895 s
        out, curve, lag = run(span((20.0, 30.0, 1.0)), n=400, v=10.0, period_ms=103.0,
                              extra=lambda t_s: {"carOutput": dict(torque=350.0 if 22.0 <= t_s < 25.0 else 0.0)})
        self.assertEqual(out[0]["curve"]["lowS"], 10.0)               # 97 Samples: 9.991 s
        self.assertEqual(out[0]["curve"]["lowLimitS"], 3.0)           # 29 Samples: 2.987 s

    def test_lag_samples_stop_while_the_command_is_at_its_limit(self):
        at_limit = lambda t_s: {"carOutput": dict(torque=350.0 if 30.0 <= t_s < 31.0 else 0.0)}
        plain = run(lambda t_s: 1.0, n=700)[2]
        out, curve, lag = run(lambda t_s: 1.0, n=700, extra=at_limit)
        self.assertEqual(plain["n"] - lag["n"], 10 + 10)              # that second, and the second after it
        self.assertEqual(curve["s"], run(lambda t_s: 1.0, n=700)[1]["s"])         # the curve sums do not mind

    def test_lag_samples_stop_while_the_command_is_not_known(self):
        # No carOutput message for a second. The last one stays fresh for 0.5 s; for the five Samples after
        # that nobody can tell whether the command sits at its limit. Not known is not "off its limit": the
        # stretch ends there, as it does at the limit itself.
        silent = lambda t_s: {"skip": ("carOutput",)} if 30.0 <= t_s < 31.0 else None
        plain_out, plain_curve, plain = run(lambda t_s: 1.0, n=700)
        out, curve, lag = run(lambda t_s: 1.0, n=700, extra=silent)
        self.assertEqual(sum(s["health"]["staleS"].get("co", 0.0) for s in out), 0.5)
        self.assertEqual(plain["n"] - lag["n"], 5 + 10)               # those five, and the second after them
        self.assertEqual(curve, plain_curve)                          # the curve sums do not mind

    def test_no_lag_sample_without_the_command_stream(self):
        gone = lambda t_s: {"skip": ("carOutput",)}
        out, curve, lag = run(lambda t_s: 1.0, lambda t_s: 0.9, n=700, extra=gone)
        self.assertIsNone(lag)
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"curve"})
        self.assertEqual(curve, run(lambda t_s: 1.0, lambda t_s: 0.9, n=700)[1])

    def test_low_speed_time_without_the_command_stream_counts_no_limit_time(self):
        # Not known adds nothing to the time at the limit; the window's own count of the missing stream says so.
        demand = span((20.0, 30.0, 1.0))
        out, curve, lag = run(demand, n=400, v=10.0, extra=lambda t_s: {"skip": ("carOutput",)})
        self.assertAlmostEqual(curve["lowS"], 10.0, delta=0.05)
        self.assertEqual(curve["lowLimitS"], 0.0)
        self.assertEqual(out[0]["health"]["staleS"]["co"], out[0]["windowS"])

    def test_a_stall_drops_the_earlier_demands_held(self):
        sums, lag_sums = CurveSums(), CurveLag()
        p = Pipeline(PipelineConfig(measures=(lambda: sums, lambda: lag_sums)))
        head = synth.ticks(300, 100, T0)
        for b in batches(lambda t_s: 1.0, times=head):
            p.ingest_tick(b)
        self.assertEqual((sums._core.held, lag_sums._core.held), (14, 14))
        p.ingest_tick(batches(lambda t_s: 1.0, times=[head[-1] + 300 * MS])[0])
        # Every Sample that waited has been evaluated, and the history starts again with the next one.
        self.assertEqual((sums._core.held, lag_sums._core.held), (0, 0))

    def test_constant_demand_with_actual_a_tenth_below(self):
        out, curve, lag = run(lambda t_s: 1.0, lambda t_s: 0.9, n=700)
        values = quality_values.curve_tracking(curve)
        self.assertAlmostEqual(values["below"], 0.10, delta=0.0005)
        self.assertEqual(values["above"], 0.0)
        self.assertAlmostEqual(values["below04"], 0.10, delta=0.0005)
        self.assertEqual(curve["events"], 1)
        self.assertEqual(curve["lowS"], 0.0)
        self.assertIsNone(values["lowBelow"])

    def test_two_curves_3_1_s_apart_are_one_event_and_3_2_s_apart_two(self):
        for apart_s, events in ((3.1, 1), (3.2, 2)):
            with self.subTest(apart_s=apart_s):
                second = round(21.9 + apart_s, 1)
                out, curve, lag = run(span((20.0, 22.0, 1.0), (second, second + 2.0, 1.0)), n=400)
                self.assertEqual(curve["events"], events)
                self.assertAlmostEqual(curve["s"], 4.0, delta=0.05)

    def test_a_demand_step_with_actual_following_0_4_s_later(self):
        demand = span((20.0, 30.0, 1.0))
        out, curve, lag = run(demand, delayed(demand, 0.4), n=400)
        values = quality_values.curve_tracking(curve)
        self.assertAlmostEqual(curve["belowSum"], 0.4, delta=0.0001)  # the entry: 0.4 s with nothing delivered
        self.assertAlmostEqual(values["below"], 0.04, delta=0.0001)
        self.assertAlmostEqual(curve["s04"], 10.0, delta=0.05)
        self.assertEqual((values["below04"], values["above04"]), (0.0, 0.0))

    def test_the_same_at_10_metres_per_second_lands_in_the_low_speed_line_only(self):
        demand = span((20.0, 30.0, 1.0))
        out, curve, lag = run(demand, delayed(demand, 0.4), n=400, v=10.0)
        self.assertEqual((curve["s"], curve["s04"], curve["events"]), (0.0, 0.0, 0))
        self.assertAlmostEqual(curve["lowS"], 10.0, delta=0.05)
        self.assertAlmostEqual(curve["lowBelowSum"], 0.4, delta=0.0001)
        self.assertEqual((curve["lowAboveSum"], curve["lowLimitS"]), (0.0, 0.0))
        self.assertIsNone(lag)
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"curve"})

    def test_low_speed_time_with_the_command_at_its_limit(self):
        demand = span((20.0, 30.0, 1.0))
        at_limit = lambda t_s: {"carOutput": dict(torque=-350.0 if 22.0 <= t_s < 25.0 else 0.0)}
        out, curve, lag = run(demand, n=400, v=10.0, extra=at_limit)
        self.assertAlmostEqual(curve["lowLimitS"], 3.0, delta=0.05)

    def test_the_fitted_lag_holds_at_any_loop_period(self):
        demand = lambda t_s: 1.2 + 0.6 * math.sin(2 * math.pi * 0.2 * t_s)
        for period_ms in (100.0, 105.0, 120.0):
            with self.subTest(period_ms=period_ms):
                out, curve, lag = run(demand, delayed(demand, 0.40), n=int(120_000 / period_ms), period_ms=period_ms)
                fitted, gain = quality_values.lag_fit(lag)
                self.assertAlmostEqual(fitted, 0.40, delta=0.02)
                self.assertAlmostEqual(gain, 1.0, delta=0.02)
                self.assertGreater(lag["s"], 100.0)

    def test_no_block_on_a_straight_or_without_steering(self):
        for name, more in (("straight", dict(demand=lambda t_s: 0.3)),
                           ("steering off", dict(demand=lambda t_s: 1.0,
                                                 extra=lambda t_s: {"carControl": dict(lat=False)})),
                           ("driver steering", dict(demand=lambda t_s: 1.0,
                                                    extra=lambda t_s: {"carState": dict(v=V, pressed=True)}))):
            with self.subTest(case=name):
                out, curve, lag = run(n=700, **more)
                self.assertEqual((curve, lag), (None, None))
                self.assertEqual(len(out), 2)

    def test_a_stall_drops_the_history_and_the_stretch(self):
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(300, 100, head[-1] + 300 * MS)
        out, curve, lag = run(lambda t_s: 1.0, lambda t_s: 0.9, times=times)
        self.assertEqual(sum(s["health"]["gapN"] for s in out), 1)
        # Accepted: 5.1 s to 28.1 s (nothing is clean within 2.0 s of the first Sample after the stall, at
        # 30.2 s), then 35.3 s to 58.1 s (2.0 s before the end).
        self.assertAlmostEqual(curve["s"], 23.1 + 22.9, delta=0.051)
        self.assertAlmostEqual(curve["s04"], 23.1 - 0.4 + 22.9 - 0.4, delta=0.051)
        self.assertAlmostEqual(lag["s"], 23.1 - 1.0 + 22.9 - 1.0, delta=0.051)
        self.assertEqual(curve["events"], 2)                          # 7.2 s without a curve Sample

    def test_sums_are_booked_six_seconds_late_and_events_count_once(self):
        out, curve, lag = run(span((50.0, 58.0, 1.0)), n=1200)
        self.assertEqual([s["curve"]["s"] for s in out], [4.0, 4.0])  # 50.0 to 53.9 s are evaluated by 59.9 s
        self.assertEqual([s["curve"]["events"] for s in out], [1, 0])
        self.assertEqual(out[0]["lag"]["n"] + out[1]["lag"]["n"], 80 + 30)

    def test_one_of_the_two_can_run_without_the_other(self):
        demand = lambda t_s: 1.0
        out, curve, lag = run(demand, n=700, measures=(CurveLag,))
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"lag"})
        out, curve, lag = run(demand, n=700, measures=(CurveSums, SteeringTime))
        self.assertIn("curve", out[0])
        self.assertNotIn("lag", out[0])
        self.assertEqual(out[0]["steeringS"], 60.0)


if __name__ == "__main__":
    unittest.main()
