"""Wheel swings: back-and-forth movements of the steering wheel on straight road, counted by time stamp."""
import math
import unittest
from types import SimpleNamespace as ns

import quality_values
import synth
from measure_api import Context
from measure_steering import SteeringTime
from measure_swings import SWING_AMPLITUDE, CentredMean, SwingCore, WheelSwings
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}


def wave(amplitude, hz, offset=0.0):
    return lambda t_s: offset + amplitude * math.sin(2 * math.pi * hz * t_s)


def feed(core, angle, n=600, period_ms=100, v=25.0, accept=lambda t_s: True, key=lambda t_s: 0, late_s=6.0,
         start_ns=T0):
    """Feed a core the way a caller must: every sample is shown at once and evaluated `late_s` later."""
    times = [start_ns + i * period_ms * MS for i in range(n)]
    waiting = []
    for t_ns in times:
        t_s = (t_ns - T0) / SEC
        core.see(t_ns, angle(t_s))
        waiting.append((t_ns, t_s))
        while waiting and waiting[0][0] + round(late_s * SEC) <= t_ns:
            u_ns, u_s = waiting.pop(0)
            core.add(u_ns, (angle(u_s), v, period_ms / 1000), accept(u_s), key(u_s))
    for u_ns, u_s in waiting:
        core.add(u_ns, (angle(u_s), v, period_ms / 1000), accept(u_s), key(u_s))
    return core.flush()


def batches(angle=None, n=1200, period_ms=100.0, v=25.0, extra=None, times=None):
    times = times or synth.ticks(n, period_ms, T0)

    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        out = dict(extra(t_s) or {}) if extra else {}
        out["carState"] = dict(dict(v=v, angle=angle(t_s) if angle else 0.0), **(out.get("carState") or {}))
        return out
    return synth.drive(times, change)


def run(angle=None, measures=(WheelSwings,), **more):
    """(summaries, the swings blocks by window) of one synthetic drive."""
    out = synth.summaries(synth.run(Pipeline(PipelineConfig(measures=measures)), batches(angle, **more)))
    return out, {s["w"]: s["swings"] for s in out if "swings" in s}


class CentredMeanTests(unittest.TestCase):
    def test_the_window_is_closed_at_both_ends(self):
        mean = CentredMean(500 * MS)
        for i, value in enumerate((1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0)):
            mean.push(T0 + i * 100 * MS, value)
        self.assertEqual(mean.at(T0 + 500 * MS), 6.0)                 # samples 0 to 10: both edge samples are in
        self.assertEqual(mean.at(T0 + 600 * MS), 7.0)                 # samples 1 to 11

    def test_a_sample_one_nanosecond_outside_is_out(self):
        mean = CentredMean(500 * MS)
        mean.push(T0 - 1, 100.0)
        mean.push(T0 + 500 * MS, 2.0)
        mean.push(T0 + 1000 * MS + 1, 100.0)
        self.assertEqual(mean.at(T0 + 500 * MS), 2.0)

    def test_at_the_start_of_a_run_the_window_holds_what_exists(self):
        mean = CentredMean(500 * MS)
        for i in range(4):
            mean.push(T0 + i * 100 * MS, float(i))
        self.assertEqual(mean.at(T0), 1.5)

    def test_nothing_shown_gives_no_mean(self):
        self.assertIsNone(CentredMean(500 * MS).at(T0))

    def test_clear_forgets_everything(self):
        mean = CentredMean(500 * MS)
        mean.push(T0, 9.0)
        mean.clear()
        mean.push(T0 + 100 * MS, 1.0)
        self.assertEqual(mean.at(T0 + 100 * MS), 1.0)
        self.assertEqual(mean.held, 1)

    def test_it_holds_no_more_than_the_window_and_what_is_ahead(self):
        mean = CentredMean(500 * MS)
        for i in range(1000):
            mean.push(T0 + i * 100 * MS, 1.0)
            if i >= 10:
                mean.at(T0 + (i - 10) * 100 * MS)
        self.assertLessEqual(mean.held, 16)

    def test_a_long_run_does_not_drift(self):
        mean = CentredMean(500 * MS)
        value = lambda i: 400.0 + 0.1 * (i % 7)
        for i in range(20000):
            mean.push(T0 + i * 100 * MS, value(i))
            if i >= 10:
                got = mean.at(T0 + (i - 10) * 100 * MS)
        self.assertAlmostEqual(got, sum(value(i) for i in range(19984, 19995)) / 11.0, places=9)


def count(residuals, accept=None, v=25.0):
    """Swings counted over residuals given by hand: Samples 20 s apart, so the trend is the angle shown (zero)."""
    core = SwingCore()
    for i, x in enumerate(residuals):
        core.see(T0 + i * 20 * SEC, 0.0)
        core.add(T0 + i * 20 * SEC, (x, v, 0.1), accept is None or accept[i], 0)
    booked = core.flush()
    return booked[0]["n"] if booked else 0


class SwingCoreTests(unittest.TestCase):
    def test_a_full_cycle_is_two_swings(self):
        booked = feed(SwingCore(), wave(3.0, 0.25))                   # 60 s, 15 cycles
        self.assertIn(booked[0]["n"], (28, 29, 30))                   # the first move only sets the direction
        self.assertAlmostEqual(booked[0]["straightS"], 60.0, places=6)
        self.assertAlmostEqual(booked[0]["vMean"], 25.0, places=6)

    def test_a_wave_under_the_amplitude_counts_nothing(self):
        self.assertEqual(SWING_AMPLITUDE, 1.0)
        self.assertEqual(feed(SwingCore(), wave(0.45, 0.25))[0]["n"], 0)       # 0.9 from peak to peak

    def test_the_wheel_angle_is_scaled_by_speed_squared(self):
        fast = 25.0 * math.sqrt(2.5)                                  # 0.45 degrees count as 1.125
        self.assertGreaterEqual(feed(SwingCore(), wave(0.45, 0.25), v=fast)[0]["n"], 28)
        self.assertEqual(feed(SwingCore(), wave(1.0, 0.25), v=25.0 / 2)[0]["n"], 0)       # 1.0 degree counts as 0.25

    def test_the_scale_is_one_at_25_metres_per_second_and_goes_with_the_speed_squared(self):
        self.assertEqual(count([0.0, 1.0, 0.0]), 1)
        self.assertEqual(count([0.0, 1.0, 0.0], v=24.0), 0)           # one degree counts as 0.92
        self.assertEqual(count([0.0, 0.6, 0.0], v=25.0 * math.sqrt(2.0)), 1)      # 0.6 degrees count as 1.2
        self.assertEqual(count([0.0, 0.6, 0.0], v=25.0 * 1.3), 1)                 # ... as 1.01
        self.assertEqual(count([0.0, 0.6, 0.0], v=25.0 * 1.28), 0)                # ... as 0.98

    def test_the_trend_reaches_5_05_s_each_way(self):
        # Three Samples: the first two, 5.03 s apart, share a trend window; the third is alone in its own.
        core = SwingCore()
        rows = ((T0, 10.0), (T0 + 5030 * MS, 0.0), (T0 + 10500 * MS, 0.0))
        for t_ns, angle in rows:
            core.see(t_ns, angle)
        for t_ns, angle in rows:
            core.add(t_ns, (angle, 25.0, 0.1), True, 0)
        # Residuals +5, -5, 0: a direction, then one swing. With 5.0 s each way they would all be zero.
        self.assertEqual(core.flush()[0]["n"], 1)

    def test_a_slow_change_of_the_wheel_angle_is_trend_not_swing(self):
        self.assertEqual(feed(SwingCore(), lambda t_s: 8.0 + 0.3 * t_s)[0]["n"], 0)
        with_offset = feed(SwingCore(), wave(3.0, 0.25, offset=40.0))
        self.assertIn(with_offset[0]["n"], (28, 29, 30))

    def test_the_first_move_sets_the_direction_and_each_reversal_counts_once(self):
        self.assertEqual(count([0.0, 0.5, 1.0]), 0)                   # up by 1.0: direction only
        self.assertEqual(count([0.0, 1.0, 0.1]), 0)                   # back by 0.9: not a reversal
        self.assertEqual(count([0.0, 1.0, 0.0]), 1)
        self.assertEqual(count([0.0, 1.0, 2.0, 1.5, 1.0]), 1)         # measured from the highest point reached
        self.assertEqual(count([0.0, 1.0, 0.0, 1.0, 0.0]), 3)
        self.assertEqual(count([0.0, -1.0, 0.0, -1.0]), 2)
        self.assertEqual(count([0.0, 0.6, -0.5]), 0)                  # first move down by 1.1 from the top: direction
        self.assertEqual(count([0.0, 0.6, -0.5, 0.5]), 1)
        self.assertEqual(count([0.0, -0.5, 0.6]), 0)                  # ... and up by 1.1 from the bottom
        self.assertEqual(count([0.0, -0.5, 0.6, -0.5]), 1)

    def test_a_sample_that_is_not_accepted_empties_the_state(self):
        xs = [0.0, 1.0, 2.0, 0.9, 2.0, 0.9]
        self.assertEqual(count(xs), 3)
        # With the fourth Sample left out, the fifth starts afresh and the sixth only sets a direction.
        self.assertEqual(count(xs, accept=[True, True, True, False, True, True]), 0)
        booked = feed(SwingCore(), wave(3.0, 0.25), accept=lambda t_s: not 30.0 <= t_s < 30.1)
        self.assertAlmostEqual(booked[0]["straightS"], 59.9, places=6)

    def test_a_sample_without_a_wheel_angle_takes_no_part(self):
        # The wheel is held 3 degrees over and one Sample has no angle. It is not in the trend and it is not
        # scored, whatever its flag says. Read as zero it would be a move to the middle and back: a swing.
        booked = feed(SwingCore(), lambda t_s: None if abs(t_s - 30.0) < 0.01 else 3.0)
        self.assertEqual(booked[0]["n"], 0)
        self.assertAlmostEqual(booked[0]["straightS"], 59.9, places=6)
        as_zero = feed(SwingCore(), lambda t_s: 0.0 if abs(t_s - 30.0) < 0.01 else 3.0)
        self.assertEqual(as_zero[0]["n"], 1)
        # Like any Sample that is left out, it empties the swing state.
        self.assertEqual(count([0.0, 1.0, 2.0, 0.9, 2.0, 0.9]), 3)
        self.assertEqual(count([0.0, 1.0, 2.0, None, 2.0, 0.9]), 0)
        core = SwingCore()
        core.see(T0, None)
        self.assertEqual(core.held, 0)                                # nothing is held for the trend in its place

    def test_time_and_speed_are_only_summed_over_accepted_samples(self):
        booked = feed(SwingCore(), wave(3.0, 0.25), accept=lambda t_s: t_s >= 45.0)
        self.assertAlmostEqual(booked[0]["straightS"], 15.0, places=6)
        self.assertIn(booked[0]["n"], (6, 7))

    def test_the_mean_speed_is_weighted_by_time(self):
        # Ten Samples of 0.1 s at 20 m/s, then ten of 0.2 s at 30 m/s: 3 s in all, two thirds of it at 30 m/s.
        core = SwingCore()
        t_ns = T0
        for v, dt in [(20.0, 0.1)] * 10 + [(30.0, 0.2)] * 10:
            t_ns += round(dt * SEC)
            core.see(t_ns, 0.0)
            core.add(t_ns, (0.0, v, dt), True, 0)
        block = core.flush()[0]
        self.assertAlmostEqual(block["straightS"], 3.0, places=9)
        self.assertAlmostEqual(block["vMean"], 80.0 / 3.0, places=9)  # a mean over the Samples would read 25.0

    def test_nothing_accepted_books_nothing(self):
        self.assertEqual(feed(SwingCore(), wave(3.0, 0.25), accept=lambda t_s: False), {})

    def test_each_sample_books_to_the_key_it_is_given(self):
        booked = feed(SwingCore(), wave(3.0, 0.25), key=lambda t_s: "a" if t_s < 20.0 else "b")
        self.assertEqual(set(booked), {"a", "b"})
        self.assertAlmostEqual(booked["a"]["straightS"], 20.0, places=6)
        self.assertAlmostEqual(booked["b"]["straightS"], 40.0, places=6)
        self.assertEqual(booked["a"]["n"] + booked["b"]["n"], feed(SwingCore(), wave(3.0, 0.25))[0]["n"])

    def test_flush_hands_over_the_sums_and_keeps_the_swing_state(self):
        core = SwingCore()
        whole = feed(SwingCore(), wave(3.0, 0.25))[0]["n"]
        first = feed(core, wave(3.0, 0.25), n=300)                                  # flushed in mid-swing
        self.assertEqual(core.flush(), {})
        second = feed(core, wave(3.0, 0.25), n=300, start_ns=T0 + 30 * SEC)
        self.assertEqual(first[0]["n"] + second[0]["n"], whole)

    def test_a_new_run_forgets_the_wheel_angles_and_the_swing_state(self):
        core = SwingCore()
        feed(core, lambda t_s: 300.0, n=100)                          # a wheel held far over
        core.new_run()
        booked = feed(core, wave(3.0, 0.25), start_ns=T0 + 20 * SEC)
        self.assertIn(booked[0]["n"], (28, 29, 30))                   # the old angles are not in the new trend
        self.assertGreater(core.held, 0)
        core.new_run()
        self.assertEqual(core.held, 0)

    def test_a_new_run_empties_the_swing_state(self):
        core = SwingCore()
        for i, x in enumerate((0.0, 1.0)):
            core.see(T0 + i * 20 * SEC, 0.0)
            core.add(T0 + i * 20 * SEC, (x, 25.0, 0.1), True, 0)
        core.new_run()
        core.see(T0 + 40 * SEC, 0.0)
        core.add(T0 + 40 * SEC, (0.0, 25.0, 0.1), True, 0)            # a swing, had the direction been kept
        self.assertEqual(core.flush()[0]["n"], 0)

    def test_the_buffer_stays_at_eleven_seconds(self):
        core = SwingCore()
        feed(core, wave(3.0, 0.25), n=3000)
        self.assertLessEqual(core.held, 113)

    def test_a_sample_never_shown_swings_about_itself(self):
        core = SwingCore()
        core.add(T0, (7.0, 25.0, 0.1), True, 0)
        self.assertEqual(core.flush()[0]["n"], 0)


class WheelSwingsTests(unittest.TestCase):
    def test_it_is_a_log_only_measure_named_after_its_block(self):
        self.assertEqual((WheelSwings.name, WheelSwings.owner_facing), ("swings", False))

    def test_the_block_and_its_rounding(self):
        out, blocks = run(wave(3.0, 0.25), n=1200, v=25.04)
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"swings"})
        self.assertEqual(set(blocks[0]), {"n", "straightS", "vMean"})
        # Nothing is clean in the first 5.0 s; a Sample is evaluated 6.0 s late: 5.1 s to 53.9 s by the first minute.
        self.assertEqual(blocks[0]["straightS"], 48.9)
        self.assertEqual(blocks[0]["vMean"], 25.0)
        self.assertIsInstance(blocks[0]["n"], int)
        self.assertIn(blocks[0]["n"], (23, 24, 25))

    def test_seconds_are_sent_with_one_decimal(self):
        out, blocks = run(wave(3.0, 0.25), n=1200, period_ms=103.0)
        self.assertEqual(blocks[0]["straightS"], 48.9)                # 475 Samples of 0.103 s: 48.925 s
        self.assertEqual(blocks[1]["straightS"], 60.0)                # 583 of them: 60.049 s

    def test_the_mean_speed_on_an_uneven_loop(self):
        # 30 s at 20 m/s with a Sample every 0.100 s, then 30 m/s with one every 0.125 s. Evaluated in the
        # first minute: 249 Samples at 20 m/s (24.9 s) and 192 at 30 m/s (24.0 s). The block has no count of
        # Samples, so its mean speed goes by time: 24.9 m/s. Over the Samples it would read 24.4.
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(400, 125, head[-1] + 125 * MS)
        out, blocks = run(wave(3.0, 0.25), times=times,
                          extra=lambda t_s: {"carState": dict(v=20.0 if t_s < 30.0 else 30.0)})
        self.assertEqual((blocks[0]["straightS"], blocks[0]["vMean"]), (48.9, 24.9))

    def test_a_swing_is_booked_six_seconds_late(self):
        # One left-right of the wheel at 56 s: evaluated after the first minute has closed.
        def angle(t_s):
            return 3.0 * math.sin(2 * math.pi * 0.5 * (t_s - 55.0)) if 55.0 <= t_s < 57.0 else 0.0
        out, blocks = run(angle, n=1200)
        self.assertEqual([blocks[w]["n"] for w in (0, 1)], [0, 2])
        early, _ = run(lambda t_s: angle(t_s + 10.0), n=1200)
        self.assertEqual(early[0]["swings"]["n"], 2)

    def test_the_time_base_is_the_time_stamp_not_the_sample_count(self):
        rates = {}
        for period_ms in (100.0, 110.0, 130.0):                      # 10, 9.1 and 7.7 Hz
            out, blocks = run(wave(3.0, 0.25), n=int(180_000 / period_ms), period_ms=period_ms)
            pooled = quality_values.pool("swings", blocks.values())
            rates[period_ms] = quality_values.swing_rate(pooled)
            self.assertAlmostEqual(pooled["straightS"], 180.0 - 5.0 - 3.0, delta=0.5)
        for period_ms, rate in rates.items():
            with self.subTest(period_ms=period_ms):
                self.assertAlmostEqual(rate, 30.0, delta=1.5)        # two per 4 s cycle
                self.assertAlmostEqual(rate, rates[100.0], delta=0.05 * rates[100.0])

    def test_no_block_without_straight_road_at_speed(self):
        for name, more in (("slow", dict(v=14.9)),
                           ("curve", dict(extra=lambda t_s: {"controlsState": dict(desired=0.00025)})),
                           ("steering off", dict(extra=lambda t_s: {"carControl": dict(lat=False)})),
                           ("parked", dict(v=0.0, extra=lambda t_s: {"carState": dict(gear="park")}))):
            with self.subTest(case=name):
                out, blocks = run(wave(3.0, 0.25), n=700, **more)
                self.assertEqual(blocks, {})
                self.assertEqual(len(out), 2)

    def test_a_wheel_angle_that_is_not_a_number_counts_no_swing(self):
        # The wheel is held 3 degrees over; one carState carries no usable angle.
        out, blocks = run(lambda t_s: float("nan") if abs(t_s - 30.0) < 0.01 else 3.0, n=700)
        self.assertEqual(blocks[0]["n"], 0)
        self.assertEqual(blocks[0]["straightS"], 48.8)                # 48.9 s less that one Sample
        self.assertNotIn("failed", out[0]["health"])

    def short_window(self, angles):
        """The block of a window in which only these Samples took part, each 0.01 s long as in a full-rate log."""
        measure, ctx = WheelSwings(), Context()
        samples = [ns(t_ns=T0 + i * 10 * MS, straight_eligible=True,
                      sample=ns(steering_angle_deg=angle, v_ego=25.0, dt=0.01)) for i, angle in enumerate(angles)]
        for g in samples:
            measure.on_sample(ctx, g)
        for g in samples:
            measure.on_evaluate(ctx, g)
        return measure.window_fields()

    def test_no_block_when_its_time_reads_zero(self):
        # 0.03 s is sent as 0.0, and a block whose straight time is 0 is left out.
        self.assertEqual(self.short_window([0.0, 0.0, 0.0]), {})
        self.assertEqual(self.short_window([0.0] * 6), {"swings": {"n": 0, "straightS": 0.1, "vMean": 25.0}})

    def test_a_swing_is_never_left_out_with_its_block(self):
        # The same 0.03 s with a swing in them: left out for its time, the swing would be lost for good.
        self.assertEqual(self.short_window([0.0, 1.5, 0.0]), {"swings": {"n": 1, "straightS": 0.0, "vMean": 25.0}})

    def test_a_press_on_the_wheel_stops_the_count_around_it(self):
        pressed = lambda t_s: {"carState": dict(pressed=30.0 <= t_s < 30.3)}
        out, blocks = run(wave(3.0, 0.25), n=1200, extra=pressed)
        plain, plain_blocks = run(wave(3.0, 0.25), n=1200)
        lost = sum(b["straightS"] for b in plain_blocks.values()) - sum(b["straightS"] for b in blocks.values())
        self.assertAlmostEqual(lost, 7.2, delta=0.11)                # 2.0 s before the press to 5.0 s after it
        self.assertLess(sum(b["n"] for b in blocks.values()), sum(b["n"] for b in plain_blocks.values()))

    def test_a_stall_ends_the_run_and_the_next_one_starts_from_nothing(self):
        # The wheel is held far over before a 0.3 s stall and swings about zero after it.
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(900, 100, head[-1] + 300 * MS)
        stall_s = (head[-1] - T0) / SEC
        angle = lambda t_s: 200.0 if t_s <= stall_s else 3.0 * math.sin(2 * math.pi * 0.25 * t_s)
        out, blocks = run(angle, times=times)
        self.assertEqual(sum(s["health"]["gapN"] for s in out), 1)
        # Without the reset the trend of the first 5 s after the stall would hold the 200 degrees, and the first
        # scored Samples, 5.0 s after the stall, would read a residual of about -100 scaled degrees.
        self.assertIn(sum(b["n"] for b in blocks.values()), range(2 * 20 - 2, 2 * 21 + 1))

    def test_a_stall_drops_the_wheel_angles_held(self):
        measure = WheelSwings()
        p = Pipeline(PipelineConfig(measures=(lambda: measure,)))
        head = synth.ticks(300, 100, T0)
        for b in batches(times=head):
            p.ingest_tick(b)
        self.assertGreater(measure._core.held, 100)
        p.ingest_tick(batches(times=[head[-1] + 300 * MS])[0])
        self.assertEqual(measure._core.held, 1)                       # the first Sample of the new run

    def test_the_start_and_the_end_of_a_drive_are_not_scored(self):
        # Nothing is clean in the first 5.0 s or the last 2.0 s, and the road is only known to be straight
        # with 8.0 s of it in view: not in the last 3.0 s.
        out, blocks = run(wave(3.0, 0.25), n=300)
        self.assertEqual(blocks[0]["straightS"], 21.9)               # the Samples from 5.1 s to 26.9 s

    def test_swing_state_is_not_reset_at_a_window_boundary(self):
        # The wheel is held 3 degrees over from 50 s to 55 s. Against its 10 s trend that is a step up at 50 s
        # (a swing, evaluated at 56 s) and a step down at 55 s (a swing, evaluated at 61 s, in the next window),
        # then the way back to the trend (a third). A state emptied at the boundary would lose the second one.
        out, blocks = run(lambda t_s: 3.0 if 50.0 <= t_s < 55.0 else 0.0, n=1200)
        self.assertEqual({w: b["n"] for w, b in blocks.items()}, {0: 1, 1: 2})

    def test_beside_another_measure(self):
        out, blocks = run(wave(3.0, 0.25), n=700, measures=(WheelSwings, SteeringTime))
        self.assertEqual(out[0]["steeringS"], 60.0)
        self.assertIn("swings", out[0])


if __name__ == "__main__":
    unittest.main()
