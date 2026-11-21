"""Lateral-motion RMS: sideways acceleration in the 0.1 to 1.0 Hz band on straight road, actual and planned."""
import math
import unittest

import quality_values
import synth
from measure_lat_rms import HIGH_PASS, LOW_PASS, LateralRms, LateralRmsCore
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
V = 25.0
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}


def sine(amplitude, hz, offset=0.0):
    return lambda t_s: offset + amplitude * math.sin(2 * math.pi * hz * t_s)


def butterworth(kind, f0, fs=10.0):
    """Second-order Butterworth section by the bilinear transform: (b0, b1, b2, a1, a2)."""
    k = math.tan(math.pi * f0 / fs)
    norm = 1.0 + math.sqrt(2.0) * k + k * k
    if kind == "high":
        b = (1.0 / norm, -2.0 / norm, 1.0 / norm)
    else:
        b = (k * k / norm, 2.0 * k * k / norm, k * k / norm)
    return b + (2.0 * (k * k - 1.0) / norm, (1.0 - math.sqrt(2.0) * k + k * k) / norm)


def feed(core, actual, planned=None, n=1200, period_ms=100, v=V, accept=lambda t_s: True, key=lambda t_s: 0,
         start_ns=T0):
    """Lateral accelerations (m/s^2) over time, through the core as curvatures."""
    for i in range(n):
        t_ns = start_ns + i * period_ms * MS
        t_s = (t_ns - T0) / SEC
        a = actual(t_s)
        d = (planned or actual)(t_s)
        values = (v, None if a is None else a / (v * v), None if d is None else d / (v * v), period_ms / 1000)
        core.add(t_ns, values, accept(t_s), key(t_s))
    return core.flush()


def rms(block, plan=False):
    return math.sqrt(block["planSumSq" if plan else "sumSq"] / block["n"])


def batches(actual=None, planned=None, n=1200, period_ms=100.0, v=V, extra=None, times=None):
    times = times or synth.ticks(n, period_ms, T0)

    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        out = dict(extra(t_s) or {}) if extra else {}
        a = actual(t_s) if actual else 0.0
        d = planned(t_s) if planned else 0.0
        out.setdefault("carState", dict(v=v))
        out.setdefault("controlsState", dict(desired=d / (v * v) if v else 0.0, actual=a / (v * v) if v else 0.0))
        return out
    return synth.drive(times, change)


def run(actual=None, planned=None, measures=(LateralRms,), **more):
    """(summaries, the ay blocks by window) of one synthetic drive."""
    out = synth.summaries(synth.run(Pipeline(PipelineConfig(measures=measures)), batches(actual, planned, **more)))
    return out, {s["w"]: s["ay"] for s in out if "ay" in s}


class FilterTests(unittest.TestCase):
    def test_coefficients_equal_an_independent_design(self):
        for name, got, want in (("high-pass 0.10 Hz", HIGH_PASS, butterworth("high", 0.10)),
                                ("low-pass 1.0 Hz", LOW_PASS, butterworth("low", 1.0))):
            with self.subTest(filter=name):
                self.assertEqual(len(got), 5)
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, delta=5e-9)

    def test_the_stated_coefficients(self):
        self.assertEqual(HIGH_PASS, (0.95654323, -1.91308645, 0.95654323, -1.91119707, 0.91497583))
        self.assertEqual(LOW_PASS, (0.06745527, 0.13491055, 0.06745527, -1.14298050, 0.41280160))


class LateralRmsCoreTests(unittest.TestCase):
    def test_a_sine_inside_the_band_passes(self):
        # Amplitude 0.05 m/s^2 at 0.2 Hz: RMS 0.0354, less 3 % at this distance from the 0.1 Hz corner.
        block = feed(LateralRmsCore(), sine(0.05, 0.2), accept=lambda t_s: t_s >= 20.0)[0]
        self.assertAlmostEqual(rms(block), 0.0343, delta=0.0005)
        self.assertEqual(block["n"], 1000)
        self.assertAlmostEqual(block["s"], 100.0, places=6)
        self.assertAlmostEqual(block["vMean"], V, places=9)

    def test_slow_and_fast_movement_is_kept_out(self):
        slow = feed(LateralRmsCore(), sine(0.05, 0.02), n=3000, accept=lambda t_s: t_s >= 100.0)[0]
        fast = feed(LateralRmsCore(), sine(0.05, 3.0), accept=lambda t_s: t_s >= 20.0)[0]
        self.assertLess(rms(slow), 0.0354 * 0.05)                     # (0.02 / 0.1)^2 = 4 % of it
        self.assertLess(rms(fast), 0.0354 * 0.15)                     # (1.0 / 3.0)^2 = 11 % of it

    def test_a_steady_cornering_force_reads_nothing_from_the_first_sample(self):
        block = feed(LateralRmsCore(), lambda t_s: 0.8, n=50)[0]
        self.assertLess(rms(block), 1e-5)
        self.assertLess(rms(block, plan=True), 1e-5)

    def test_the_band_is_fixed_in_samples_so_it_moves_with_the_loop_period(self):
        # The filters count Samples, not seconds. What a steady sine reads follows from their design at the
        # period the loop really ran at: a slower loop puts the whole band lower. Movement near the slow corner
        # then reads higher and movement near the fast corner lower; only the middle of the band reads the
        # same. So values from loops of different periods are not to be compared closely, and the service
        # scores no window whose loop ran far from ten Samples a second.
        def design(hz, period_s):
            high = math.tan(math.pi * hz * period_s) / math.tan(math.pi * 0.10 / 10.0)
            low = math.tan(math.pi * hz * period_s) / math.tan(math.pi * 1.0 / 10.0)
            return high ** 2 / math.sqrt(1.0 + high ** 4) / math.sqrt(1.0 + low ** 4)

        def reading(hz, period_ms):
            block = feed(LateralRmsCore(), sine(0.05, hz), n=int(300_000 / period_ms), period_ms=period_ms,
                         accept=lambda t_s: t_s >= 60.0)[0]
            return rms(block)

        for hz in (0.085, 0.2, 0.9):
            for period_ms in (100, 105, 125):
                with self.subTest(hz=hz, period_ms=period_ms):
                    want = 0.05 / math.sqrt(2) * design(hz, period_ms / 1000)
                    self.assertAlmostEqual(reading(hz, period_ms), want, delta=0.01 * want)
        self.assertAlmostEqual(design(0.085, 0.125) / design(0.085, 0.100), 1.28, delta=0.01)
        self.assertAlmostEqual(design(0.085, 0.105) / design(0.085, 0.100), 1.06, delta=0.01)
        self.assertAlmostEqual(design(0.2, 0.125) / design(0.2, 0.100), 1.02, delta=0.01)
        self.assertAlmostEqual(design(0.9, 0.125) / design(0.9, 0.100), 0.79, delta=0.01)

    def test_actual_and_planned_are_filtered_side_by_side(self):
        block = feed(LateralRmsCore(), sine(0.05, 0.2), planned=sine(0.10, 0.2), accept=lambda t_s: t_s >= 20.0)[0]
        self.assertAlmostEqual(rms(block, plan=True), 2 * rms(block), delta=1e-6)
        still = feed(LateralRmsCore(), sine(0.05, 0.2), planned=lambda t_s: 0.0, accept=lambda t_s: t_s >= 20.0)[0]
        self.assertEqual(still["planSumSq"], 0.0)
        self.assertAlmostEqual(rms(still), 0.0343, delta=0.0005)

    def test_the_filters_run_on_samples_that_are_not_accepted(self):
        whole = feed(LateralRmsCore(), sine(0.05, 0.2), key=lambda t_s: t_s >= 60.0)
        part = feed(LateralRmsCore(), sine(0.05, 0.2), accept=lambda t_s: t_s >= 60.0)
        self.assertEqual(part[0]["sumSq"], whole[True]["sumSq"])
        self.assertEqual(part[0]["n"], 600)

    def test_the_mean_speed_is_over_the_accepted_samples_not_over_their_time(self):
        # Ten Samples of 0.1 s at 20 m/s, then ten of 0.2 s at 30 m/s: half of the Samples at each speed.
        core = LateralRmsCore()
        t_ns = T0
        for v, dt in [(20.0, 0.1)] * 10 + [(30.0, 0.2)] * 10:
            t_ns += round(dt * SEC)
            core.add(t_ns, (v, 0.0, 0.0, dt), True, 0)
        block = core.flush()[0]
        self.assertEqual(block["n"], 20)
        self.assertAlmostEqual(block["s"], 3.0, places=9)
        self.assertAlmostEqual(block["vMean"], 25.0, places=9)        # weighted by time it would read 26.7

    def test_nothing_accepted_books_nothing(self):
        self.assertEqual(feed(LateralRmsCore(), sine(0.05, 0.2), accept=lambda t_s: False), {})

    def test_a_missing_curvature_ends_the_run_and_the_next_sample_starts_one(self):
        # A steady 0.8 m/s^2 that is unknown for one Sample: with the filters started afresh on the next
        # Sample there is no transient; carried over a hole they would also read nothing; but a Sample fed as
        # zero would ring for seconds.
        holed = feed(LateralRmsCore(), lambda t_s: None if 10.0 <= t_s < 10.1 else 0.8, n=300)[0]
        self.assertEqual(holed["n"], 299)                             # the Sample without curvature cannot count
        self.assertLess(rms(holed), 1e-5)
        # And a level that changes behind the hole is taken as the new start, not as a step.
        step = feed(LateralRmsCore(), lambda t_s: None if 10.0 <= t_s < 10.1 else (0.8 if t_s < 10.0 else 0.2),
                    n=300)[0]
        self.assertLess(rms(step), 1e-5)
        ringing = feed(LateralRmsCore(), lambda t_s: 0.8 if t_s < 10.0 else 0.2, n=300)[0]
        self.assertGreater(rms(ringing), 0.05)

    def test_only_one_missing_curvature_is_enough_to_end_the_run(self):
        core = LateralRmsCore()
        core.add(T0, (V, 0.8 / V ** 2, 0.8 / V ** 2, 0.1), True, 0)
        core.add(T0 + 100 * MS, (V, None, 0.2 / V ** 2, 0.1), True, 0)
        core.add(T0 + 200 * MS, (V, 0.2 / V ** 2, None, 0.1), True, 0)
        core.add(T0 + 300 * MS, (V, 0.2 / V ** 2, 0.2 / V ** 2, 0.1), True, 0)
        block = core.flush()[0]
        self.assertEqual(block["n"], 2)
        self.assertLess(rms(block), 1e-5)
        self.assertLess(rms(block, plan=True), 1e-5)

    def test_a_new_run_starts_the_filters_afresh(self):
        core = LateralRmsCore()
        feed(core, lambda t_s: 0.8, n=100)
        core.new_run()
        block = feed(core, lambda t_s: 0.2, n=100, start_ns=T0 + 10 * SEC)[0]
        self.assertLess(rms(block), 1e-5)

    def test_each_sample_books_to_the_key_it_is_given(self):
        booked = feed(LateralRmsCore(), sine(0.05, 0.2), key=lambda t_s: int(t_s // 60))
        self.assertEqual({key: block["n"] for key, block in booked.items()}, {0: 600, 1: 600})
        self.assertAlmostEqual(rms(booked[1]), 0.0343, delta=0.0005)

    def test_flush_hands_over_the_sums_and_keeps_the_filter_state(self):
        core = LateralRmsCore()
        feed(core, sine(0.05, 0.2), n=600)
        self.assertEqual(core.flush(), {})
        second = feed(core, sine(0.05, 0.2), n=600, start_ns=T0 + 60 * SEC)[0]
        whole = feed(LateralRmsCore(), sine(0.05, 0.2), key=lambda t_s: int(t_s // 60))[1]
        self.assertEqual(second["sumSq"], whole["sumSq"])

    def test_speed_squared_times_curvature(self):
        slow = feed(LateralRmsCore(), sine(0.05, 0.2), v=20.0, accept=lambda t_s: t_s >= 20.0)[0]
        self.assertAlmostEqual(rms(slow), 0.0343, delta=0.0005)       # feed() divides by the same speed squared
        self.assertAlmostEqual(slow["vMean"], 20.0, places=9)


class LateralRmsTests(unittest.TestCase):
    def test_it_is_a_log_only_measure_named_after_its_block(self):
        self.assertEqual((LateralRms.name, LateralRms.owner_facing), ("ay", False))

    def test_the_block_and_its_rounding(self):
        out, blocks = run(sine(0.05, 0.2), planned=sine(0.08, 0.2), n=1200, v=25.04)
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"ay"})
        self.assertEqual(set(blocks[0]), {"n", "s", "sumSq", "planSumSq", "vMean"})
        self.assertEqual((blocks[0]["n"], blocks[0]["s"], blocks[0]["vMean"]), (489, 48.9, 25.0))
        for key in ("sumSq", "planSumSq"):
            value = blocks[0][key]
            self.assertEqual(value, float("%.5g" % value))            # five significant digits, no fewer
            self.assertNotEqual(value, float("%.4g" % value))
        self.assertAlmostEqual(math.sqrt(blocks[1]["sumSq"] / blocks[1]["n"]), 0.0343, delta=0.001)
        self.assertAlmostEqual(math.sqrt(blocks[1]["planSumSq"] / blocks[1]["n"]), 0.0549, delta=0.001)

    def test_seconds_are_sent_with_one_decimal(self):
        out, blocks = run(sine(0.05, 0.2), n=1200, period_ms=103.0)
        self.assertEqual((blocks[0]["n"], blocks[0]["s"]), (475, 48.9))           # 48.925 s
        self.assertEqual((blocks[1]["n"], blocks[1]["s"]), (583, 60.0))           # 60.049 s

    def test_the_mean_speed_on_an_uneven_loop(self):
        # 30 s at 20 m/s with a Sample every 0.100 s, then 30 m/s with one every 0.125 s. Accepted in the first
        # minute: 249 Samples at 20 m/s (24.9 s) and 192 at 30 m/s (24.0 s). The block counts its Samples, so
        # its mean speed is the mean over them: 24.4 m/s. Weighted by time it would read 24.9.
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(400, 125, head[-1] + 125 * MS)
        out, blocks = run(times=times, extra=lambda t_s: {"carState": dict(v=20.0 if t_s < 30.0 else 30.0)})
        self.assertEqual((blocks[0]["n"], blocks[0]["s"], blocks[0]["vMean"]), (441, 48.9, 24.4))

    def test_the_value_holds_at_any_loop_period(self):
        for period_ms in (100.0, 105.0, 125.0):
            out, blocks = run(sine(0.05, 0.2), n=int(240_000 / period_ms), period_ms=period_ms)
            with self.subTest(period_ms=period_ms):
                values = [quality_values.lat_accel_rms(s.get("ay"), s["windowS"] / s["n"]) for s in out[1:3]]
                for value in values:
                    self.assertAlmostEqual(value[0], 0.035, delta=0.003)
                    self.assertEqual(value[1], 0.0)

    def test_after_a_stall_the_filters_restart_and_nothing_is_accepted_for_five_seconds(self):
        # A steady 0.8 m/s^2 before a 0.3 s stall and a steady 0.2 after it. The filters start afresh on the first
        # Sample after the stall, so the step is not seen; carried across, it would ring through the minute.
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(900, 100, head[-1] + 300 * MS)
        stall_s = (head[-1] - T0) / SEC
        out, blocks = run(lambda t_s: 0.8 if t_s <= stall_s else 0.2, times=times)
        self.assertEqual(sum(s["health"]["gapN"] for s in out), 1)
        self.assertLess(max(math.sqrt(b["sumSq"] / b["n"]) for b in blocks.values()), 1e-4)
        # Accepted: from 5.1 s to 3.0 s before the stall (the road is only known to be straight with 8.0 s of it
        # in view, and the view ends at the stall), then from 5.1 s after the stall to 3.0 s before the end.
        self.assertEqual((stall_s, (times[-1] - T0) / SEC), (29.9, 120.1))
        before = 26.9 - 5.1 + 0.1
        after = (120.1 - 3.0) - (30.2 + 5.1) + 0.1
        self.assertAlmostEqual(sum(b["s"] for b in blocks.values()), before + after, delta=0.051)
        self.assertEqual(sum(b["n"] for b in blocks.values()), 219 + 819)

    def test_the_same_sine_with_a_stall_still_reads_the_same(self):
        head = synth.ticks(600, 100, T0)
        times = head + synth.ticks(1800, 100, head[-1] + 300 * MS)
        out, blocks = run(sine(0.05, 0.2), times=times)
        self.assertEqual(sorted(blocks), [0, 1, 2, 3, 4])
        for b in (blocks[2], blocks[3]):                             # whole minutes, the filters settled again
            self.assertAlmostEqual(math.sqrt(b["sumSq"] / b["n"]), 0.035, delta=0.003)

    def test_no_block_without_straight_road_at_speed(self):
        for name, more in (("slow", dict(v=14.9)),
                           ("curve", dict(actual=lambda t_s: 0.2, planned=lambda t_s: 0.2)),
                           ("steering off", dict(extra=lambda t_s: {"carControl": dict(lat=False)})),
                           ("no curvature", dict(extra=lambda t_s: {"controlsState": synth.ns(desiredCurvature=None,
                                                                                              curvature=None)}))):
            with self.subTest(case=name):
                out, blocks = run(**dict(dict(actual=sine(0.05, 0.2), n=700), **more))
                self.assertEqual(blocks, {})
                self.assertEqual(len(out), 2)

    def test_a_sample_is_booked_six_seconds_late(self):
        out, blocks = run(sine(0.05, 0.2), n=1200)
        # First minute: the Samples from 5.1 s to 53.9 s. Second: 54.0 s to 113.9 s, and at the end of the drive
        # what still waited, less the last 3.0 s.
        self.assertEqual([blocks[w]["n"] for w in (0, 1)], [489, 600 + 30])
        self.assertEqual(blocks[1]["s"], 63.0)

    def test_beside_another_measure(self):
        out, blocks = run(sine(0.05, 0.2), n=700, measures=(LateralRms, SteeringTime))
        self.assertEqual(out[0]["steeringS"], 60.0)
        self.assertIn("ay", out[0])


if __name__ == "__main__":
    unittest.main()
