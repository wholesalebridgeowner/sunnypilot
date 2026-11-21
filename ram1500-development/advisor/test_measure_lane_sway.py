"""Lane sway: slow side-to-side drift of the truck between the lane lines on straight road."""
import math
import unittest

import quality_values
import synth
from measure_lane_sway import LaneSway, LaneSwayCore
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}
BLOCK = {"n", "sumSq", "scoredS", "validS", "eligibleS", "vMean", "widthMin", "widthMax", "probMin"}
HALF_WIDTH = 1.8


def sine(amplitude, hz):
    return lambda t_s: amplitude * math.sin(2 * math.pi * hz * t_s)


def feed(core, offset=lambda t_s: 0.0, n=300, period_ms=100, width=lambda t_s: 2 * HALF_WIDTH,
         left_prob=lambda t_s: 0.95, right_prob=lambda t_s: 0.95, accept=lambda t_s: True,
         straight=lambda t_s: True, key=lambda t_s: 0, v=lambda t_s: 25.0, slow=False, times=None, lines=None):
    """Lane lines over time through a core: every message is shown first, then each is scored, as the rules
    of the core allow. `lines(t_s)` gives (left_y, right_y) directly."""
    times = times or [T0 + i * period_ms * MS for i in range(n)]
    prev = None
    for t_ns in times:
        t_s = (t_ns - T0) / SEC
        dt_m = 0.0 if prev is None else min((t_ns - prev) / SEC, 1.0)
        prev = t_ns
        if lines:
            left_y, right_y = lines(t_s)
        else:
            left_y, right_y = offset(t_s) - width(t_s) / 2, offset(t_s) + width(t_s) / 2
        core.see(t_ns, (dt_m, left_y, right_y, left_prob(t_s), right_prob(t_s), slow), accept(t_s))
    for t_ns in times:
        t_s = (t_ns - T0) / SEC
        core.add(t_ns, v(t_s), straight(t_s), key(t_s))
    return core.flush()


def sd(block):
    return math.sqrt(block["sumSq"] / block["n"])


def batches(offset=None, n=1200, period_ms=100.0, v=25.0, model=None, extra=None, times=None):
    times = times or synth.ticks(n, period_ms, T0)

    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        out = dict(extra(t_s) or {}) if extra else {}
        centre = offset(t_s) if offset else 0.0
        lines = dict(left_y=centre - HALF_WIDTH, right_y=centre + HALF_WIDTH)
        lines.update((model(t_s) if model else None) or {})
        out.setdefault("carState", dict(v=v))
        out.setdefault("drivingModelData", lines)
        return out
    return synth.drive(times, change)


def run(offset=None, measures=(LaneSway,), **more):
    """(summaries, the lane blocks by window, all of them added up) of one synthetic drive."""
    out = synth.summaries(synth.run(Pipeline(PipelineConfig(measures=measures)), batches(offset, **more)))
    blocks = {s["w"]: s["lane"] for s in out if "lane" in s}
    return out, blocks, quality_values.pool("lane", blocks.values())


class LaneSwayCoreTests(unittest.TestCase):
    def test_a_truck_that_holds_its_place_reads_zero(self):
        block = feed(LaneSwayCore(), lambda t_s: 0.31)[0]
        self.assertEqual(block["n"], 298)                             # all but the first and the last message
        self.assertLess(block["sumSq"], 1e-20)
        self.assertAlmostEqual(block["scoredS"], 29.8, places=9)

    def test_a_slow_side_to_side_wave(self):
        # 0.05 m at 0.1 Hz: one whole period fills the 10 s window, so the window mean is about zero and the
        # value is the wave's own RMS, 0.035 m.
        block = feed(LaneSwayCore(), sine(0.05, 0.1), n=1200)[0]
        self.assertAlmostEqual(sd(block), 0.036, delta=0.004)
        self.assertAlmostEqual(sd(block), 0.05 / math.sqrt(2), delta=0.001)

    def test_a_wave_twice_as_slow_is_mostly_taken_as_the_lane_itself(self):
        self.assertAlmostEqual(sd(feed(LaneSwayCore(), sine(0.05, 0.05), n=1200)[0]), 0.013, delta=0.002)

    def test_the_window_reaches_exactly_five_seconds_each_way(self):
        # One message stands 0.2 m aside. It is in the window of every message up to 5.0 s away, ends included:
        # 100 neighbours read -0.2/101 and the message itself 0.2 x 100/101.
        block = feed(LaneSwayCore(), lambda t_s: 0.2 if abs(t_s - 15.0) < 0.01 else 0.0)[0]
        want = 100 * (0.2 / 101) ** 2 + (0.2 * 100 / 101) ** 2
        self.assertAlmostEqual(block["sumSq"], want, delta=1e-12)

    def test_a_message_5_01_s_away_is_outside_the_window(self):
        # Messages every 0.167 s: the thirtieth neighbour is 5.01 s away. The window holds 29 each side.
        block = feed(LaneSwayCore(), lambda t_s: 0.2 if abs(t_s - 100 * 0.167) < 0.01 else 0.0, n=300,
                     period_ms=167)[0]
        self.assertAlmostEqual(block["sumSq"], 58 * (0.2 / 59) ** 2 + (0.2 * 58 / 59) ** 2, delta=1e-12)

    def test_the_offset_is_the_middle_of_the_two_lines(self):
        # The left line stands still and the right one moves by 0.1 m: the middle moves by half of that.
        wave = sine(0.1, 0.1)
        block = feed(LaneSwayCore(), lines=lambda t_s: (-1.8, 1.8 + wave(t_s)), n=1200)[0]
        self.assertAlmostEqual(sd(block), 0.05 / math.sqrt(2), delta=0.001)

    def test_only_valid_messages_are_in_the_mean(self):
        # One second of low line probability during which the lines read 0.25 m aside.
        aside = lambda t_s: 10.0 <= t_s < 11.0
        block = feed(LaneSwayCore(), lambda t_s: 0.25 if aside(t_s) else 0.0,
                     left_prob=lambda t_s: 0.5 if aside(t_s) else 0.95)[0]
        self.assertEqual(block["n"], 298 - 10)
        self.assertLess(block["sumSq"], 1e-20)

    def test_at_two_messages_a_second_the_window_holds_twenty_one(self):
        block = feed(LaneSwayCore(), lambda t_s: 0.2 if abs(t_s - 30.0) < 0.01 else 0.0, n=120, period_ms=500,
                     slow=True)[0]
        self.assertAlmostEqual(block["sumSq"], 20 * (0.2 / 21) ** 2 + (0.2 * 20 / 21) ** 2, delta=1e-12)

    def test_a_message_counts_for_the_time_since_the_one_before_it(self):
        block = feed(LaneSwayCore(), n=60, period_ms=500, slow=True)[0]
        self.assertEqual(block["n"], 58)
        self.assertAlmostEqual(block["vMean"], 25.0, places=9)        # a mean over messages, whatever their time
        self.assertAlmostEqual(block["scoredS"], 29.0, places=9)
        self.assertAlmostEqual(block["validS"], 29.5, places=9)       # the first message stands for no time
        self.assertAlmostEqual(block["eligibleS"], 29.5, places=9)

    def test_both_line_probabilities_must_be_at_least_0_90(self):
        low = lambda t_s: 0.89 if 10.0 <= t_s < 11.0 else 0.95
        for side in ("left_prob", "right_prob"):
            with self.subTest(side=side):
                block = feed(LaneSwayCore(), **{side: low})[0]
                self.assertAlmostEqual(block["validS"], 29.9 - 1.0, places=9)
                self.assertAlmostEqual(block["eligibleS"], 29.9, places=9)
                self.assertEqual(block["n"], 298 - 10)                # the windows around them are still 90 % valid
                self.assertEqual(block["probMin"], 0.89)
        self.assertEqual(feed(LaneSwayCore(), left_prob=lambda t_s: 0.90)[0]["n"], 298)

    def test_the_lane_must_be_2_5_to_4_5_m_wide(self):
        for half, valid in ((1.245, False), (1.25, True), (2.25, True), (2.255, False)):
            with self.subTest(width=2 * half):
                block = feed(LaneSwayCore(), lines=lambda t_s: (-half, half))[0]
                self.assertAlmostEqual(block["validS"], 29.9 if valid else 0.0, places=9)
                self.assertEqual((block["widthMin"], block["widthMax"]), (2 * half, 2 * half))
        # A wide stretch of one second: not valid, and the rest goes on being scored.
        block = feed(LaneSwayCore(), lines=lambda t_s: (-2.3, 2.3) if 10.0 <= t_s < 11.0 else (-1.8, 1.8))[0]
        self.assertAlmostEqual(block["validS"], 29.9 - 1.0, places=9)
        self.assertEqual(block["n"], 298 - 10)
        self.assertEqual((block["widthMin"], block["widthMax"]), (3.6, 4.6))

    def test_a_message_that_is_not_accepted_is_not_valid_and_not_eligible(self):
        block = feed(LaneSwayCore(), accept=lambda t_s: not 10.0 <= t_s < 11.0, width=lambda t_s: 3.0 + 0.01 * t_s,
                     left_prob=lambda t_s: 0.99 if t_s < 10.0 else 0.93)[0]
        self.assertAlmostEqual(block["eligibleS"], 29.9 - 1.0, places=9)
        self.assertAlmostEqual(block["validS"], 29.9 - 1.0, places=9)
        self.assertEqual(block["n"], 298 - 10)
        # Width and probability are looked at over the accepted messages only.
        self.assertAlmostEqual(block["widthMin"], 3.0, places=9)
        self.assertAlmostEqual(block["widthMax"], 3.299, places=9)
        self.assertEqual(block["probMin"], 0.93)
        seen_only = feed(LaneSwayCore(), accept=lambda t_s: not 10.0 <= t_s < 11.0,
                         left_prob=lambda t_s: 0.2 if 10.0 <= t_s < 11.0 else 0.95)[0]
        self.assertEqual(seen_only["probMin"], 0.95)

    def test_a_jump_takes_out_one_second_before_it_and_two_after_it(self):
        # The lane centre jumps by 0.4 m between the messages at 14.9 s and 15.0 s.
        block = feed(LaneSwayCore(), lambda t_s: 0.4 if t_s >= 15.0 else 0.0, n=400)[0]
        self.assertAlmostEqual(block["validS"], 39.9 - 3.1, places=9)             # 14.0 s to 17.0 s, ends included
        self.assertAlmostEqual(block["eligibleS"], 39.9, places=9)
        # It takes more than 0.30 m.
        under = feed(LaneSwayCore(), lambda t_s: 0.29 if t_s >= 15.0 else 0.0, n=400)[0]
        self.assertAlmostEqual(under["validS"], 39.9, places=9)
        over = feed(LaneSwayCore(), lambda t_s: 0.31 if t_s >= 15.0 else 0.0, n=400)[0]
        self.assertAlmostEqual(over["validS"], 39.9 - 3.1, places=9)

    def test_the_reach_of_a_jump_is_1_0_s_back_and_2_0_s_on(self):
        # Messages every 0.102 s: the tenth before the jump is 1.02 s back, the twentieth after it 2.04 s on.
        block = feed(LaneSwayCore(), lambda t_s: 0.4 if t_s >= 200 * 0.102 - 0.01 else 0.0, n=400, period_ms=102)[0]
        self.assertAlmostEqual(block["validS"], (399 - 29) * 0.102, places=9)     # 9 before, the jump, 19 after

    def test_a_jump_in_width_counts_like_a_jump_of_the_centre(self):
        block = feed(LaneSwayCore(), width=lambda t_s: 4.0 if t_s >= 15.0 else 3.6, n=400)[0]
        self.assertAlmostEqual(block["validS"], 39.9 - 3.1, places=9)

    def test_the_messages_after_a_jump_are_scored_against_the_new_place(self):
        block = feed(LaneSwayCore(), lambda t_s: 0.4 if t_s >= 15.0 else 0.0, n=600)[0]
        # Nothing is scored where under 80 % of the window is valid, and what is scored reads its own side.
        self.assertGreater(block["n"], 400)
        self.assertLess(sd(block), 0.2)

    def test_a_jump_needs_two_valid_messages_close_together(self):
        jump = lambda t_s: 0.4 if t_s >= 15.0 else 0.0
        far = [T0 + i * 100 * MS for i in range(150)] + [T0 + (14900 + 260 + i * 100) * MS for i in range(150)]
        self.assertAlmostEqual(feed(LaneSwayCore(), jump, times=far)[0]["validS"], 29.9 + 0.16, places=9)
        near = [T0 + i * 100 * MS for i in range(150)] + [T0 + (14900 + 250 + i * 100) * MS for i in range(150)]
        self.assertLess(feed(LaneSwayCore(), jump, times=near)[0]["validS"], 27.5)
        # On a slow stream the two may be 0.75 s apart.
        slow = [T0 + i * 500 * MS for i in range(30)] + [T0 + (14500 + 750 + i * 500) * MS for i in range(30)]
        self.assertLess(feed(LaneSwayCore(), jump, times=slow, slow=True)[0]["validS"], 29.75 - 2.0)
        slow_far = [T0 + i * 500 * MS for i in range(30)] + [T0 + (14500 + 760 + i * 500) * MS for i in range(30)]
        self.assertAlmostEqual(feed(LaneSwayCore(), jump, times=slow_far, slow=True)[0]["validS"], 29.76, places=9)
        # And no jump is seen from or to a message that is not valid by itself.
        dim = feed(LaneSwayCore(), jump, n=300, left_prob=lambda t_s: 0.5 if abs(t_s - 14.9) < 0.01 else 0.95)[0]
        self.assertAlmostEqual(dim["validS"], 29.9 - 0.1, places=9)

    def test_a_window_with_under_80_percent_valid_time_scores_nothing(self):
        # 2.1 s of low line probability: every window that holds all of it is 79.2 % valid.
        low = lambda t_s: 0.5 if 20.0 <= t_s < 22.1 else 0.95
        block = feed(LaneSwayCore(), left_prob=low, n=420)[0]
        # Scored: up to the message at 16.9 s (its window ends at 21.9 s: 80.2 % valid) and from 25.1 s on.
        self.assertEqual(block["n"], 169 + (419 - 251))
        enough = feed(LaneSwayCore(), left_prob=lambda t_s: 0.5 if 20.0 <= t_s < 22.0 else 0.95, n=420)[0]
        self.assertEqual(enough["n"], 418 - 20)                       # 80.2 % at worst: only the 20 are missing

    def test_a_window_must_hold_five_seconds_of_messages(self):
        self.assertEqual(feed(LaneSwayCore(), n=50)[0]["n"], 0)       # 4.9 s in all
        block = feed(LaneSwayCore(), n=52)[0]                          # 5.1 s in all
        self.assertEqual(block["n"], 50)
        self.assertAlmostEqual(block["validS"], 5.1, places=9)

    def test_there_must_be_a_valid_message_on_each_side(self):
        # Only the first and the last message of a run have none before or after them.
        self.assertEqual(feed(LaneSwayCore(), n=300)[0]["n"], 298)
        # The first valid message after a stretch of invalid ones has valid ones before it within 5.0 s or not.
        low = lambda t_s: 0.5 if 10.0 <= t_s < 15.5 else 0.95
        block = feed(LaneSwayCore(), left_prob=low, n=1200)[0]
        short = feed(LaneSwayCore(), left_prob=lambda t_s: 0.5 if 10.0 <= t_s < 11.0 else 0.95, n=1200)[0]
        self.assertLess(block["n"], short["n"])

    def test_only_messages_on_straight_road_are_scored(self):
        block = feed(LaneSwayCore(), sine(0.05, 0.1), straight=lambda t_s: t_s < 10.0, n=300)[0]
        self.assertEqual(block["n"], 99)                              # 0.1 s to 9.9 s
        self.assertAlmostEqual(block["validS"], 29.9, places=9)       # valid all the same: they are in the windows

    def test_the_mean_speed_is_that_of_the_scored_messages(self):
        block = feed(LaneSwayCore(), v=lambda t_s: 20.0 if t_s < 15.0 else 30.0, straight=lambda t_s: t_s >= 10.0)[0]
        self.assertAlmostEqual(block["vMean"], (50 * 20.0 + 149 * 30.0) / 199, places=9)

    def test_nothing_accepted_books_nothing(self):
        self.assertEqual(feed(LaneSwayCore(), accept=lambda t_s: False), {})

    def test_accepted_but_never_scored_still_reports_its_time(self):
        block = feed(LaneSwayCore(), straight=lambda t_s: False)[0]
        self.assertEqual((block["n"], block["sumSq"], block["scoredS"], block["vMean"]), (0, 0.0, 0.0, None))
        self.assertAlmostEqual(block["eligibleS"], 29.9, places=9)
        self.assertEqual((block["widthMin"], block["widthMax"], block["probMin"]), (3.6, 3.6, 0.95))

    def test_a_message_without_lane_lines_is_received_but_not_valid(self):
        gone = lambda t_s: (None, 1.8) if 10.0 <= t_s < 11.0 else (-1.8, 1.8)
        block = feed(LaneSwayCore(), lines=gone)[0]
        self.assertAlmostEqual(block["validS"], 29.9 - 1.0, places=9)
        self.assertAlmostEqual(block["eligibleS"], 29.9, places=9)
        self.assertEqual(block["widthMin"], 3.6)
        no_prob = feed(LaneSwayCore(), left_prob=lambda t_s: None if 10.0 <= t_s < 11.0 else 0.95)[0]
        self.assertAlmostEqual(no_prob["validS"], 29.9 - 1.0, places=9)
        self.assertEqual(no_prob["probMin"], 0.95)

    def test_each_message_books_to_the_key_it_is_scored_with(self):
        booked = feed(LaneSwayCore(), sine(0.05, 0.1), n=1200, key=lambda t_s: int(t_s // 60))
        self.assertEqual((booked[0]["n"], booked[1]["n"]), (599, 599))
        self.assertAlmostEqual(booked[0]["eligibleS"] + booked[1]["eligibleS"], 119.9, places=9)

    def test_messages_are_scored_in_the_order_they_were_shown(self):
        core = LaneSwayCore()
        for i in range(3):
            core.see(T0 + i * 100 * MS, (0.1, -1.8, 1.8, 0.95, 0.95, False), True)
        core.add(T0, 25.0, True, 0)
        with self.assertRaises(ValueError):
            core.add(T0 + 200 * MS, 25.0, True, 0)                    # the one at 0.1 s was skipped
        with self.assertRaises(ValueError):
            LaneSwayCore().add(T0, 25.0, True, 0)                     # never shown

    def test_a_new_run_starts_from_nothing(self):
        core = LaneSwayCore()
        feed(core, lambda t_s: 5.0, n=100)
        core.new_run()
        self.assertEqual(core.held, 0)
        block = feed(core, lambda t_s: 0.0, times=[T0 + (100 + i) * 100 * MS for i in range(100)])[0]
        self.assertEqual(block["n"], 98)                              # its own first and last message are not scored
        self.assertLess(block["sumSq"], 1e-20)                        # and the old place is in no window

    def test_a_new_run_forgets_the_jump_too(self):
        core = LaneSwayCore()
        feed(core, lambda t_s: 0.4 if t_s >= 9.9 else 0.0, n=100)     # a jump on the last message
        core.new_run()
        block = feed(core, lambda t_s: 0.4, times=[T0 + (100 + i) * 100 * MS for i in range(100)])[0]
        self.assertAlmostEqual(block["validS"], 9.9, places=9)        # valid from its first message on

    def test_a_message_scored_too_early_does_not_leave_stale_sums(self):
        # The caller scores half of the messages before the rest has been shown, and then a jump is shown that
        # takes back messages already counted in the window. The next scores must not use them.
        core = LaneSwayCore()
        offset = lambda i: 0.2 if 90 <= i < 100 else (0.6 if i >= 100 else 0.0)
        show = lambda i: core.see(T0 + i * 100 * MS, (0.1, offset(i) - 1.8, offset(i) + 1.8, 0.95, 0.95, False), True)
        for i in range(100):
            show(i)
        for i in range(50):
            core.add(T0 + i * 100 * MS, 25.0, True, "early")          # the window of the last one reaches row 99
        for i in range(100, 200):
            show(i)                                                   # row 100 is a jump: rows 90 to 99 are taken back
        for i in range(50, 60):
            core.add(T0 + i * 100 * MS, 25.0, True, "late")
        booked = core.flush()
        self.assertEqual(booked["late"]["n"], 10)
        self.assertLess(booked["late"]["sumSq"], 1e-20)               # the mean holds rows 5 to 89 only: all zero
        self.assertGreater(booked["early"]["sumSq"], 1e-6)            # what was scored too early stays as it was

    def test_flush_hands_over_the_sums_and_keeps_the_messages(self):
        core = LaneSwayCore()
        times = [T0 + i * 100 * MS for i in range(200)]
        for t_ns in times:
            core.see(t_ns, (0.1, -1.8, 1.8, 0.95, 0.95, False), True)
        for t_ns in times[:100]:
            core.add(t_ns, 25.0, True, 0)
        first = core.flush()[0]
        self.assertEqual(core.flush(), {})
        for t_ns in times[100:]:
            core.add(t_ns, 25.0, True, 1)
        self.assertEqual(first["n"] + core.flush()[1]["n"], 198)

    def test_the_buffer_stays_bounded(self):
        core = LaneSwayCore()
        for i in range(20000):
            core.see(T0 + i * 100 * MS, (0.1, -1.8, 1.8, 0.95, 0.95, False), True)
            if i >= 86:
                core.add(T0 + (i - 86) * 100 * MS, 25.0, True, 0)
        self.assertLessEqual(core.held, 51 + 87 + 64)                 # 5.0 s behind, 8.6 s ahead, one lump
        self.assertEqual(core.flush()[0]["n"], 20000 - 86 - 1)

    def test_a_long_run_does_not_drift(self):
        core = LaneSwayCore()
        offset = lambda i: 0.3 + 0.02 * ((i * 7) % 11)
        for i in range(30000):
            core.see(T0 + i * 100 * MS, (0.1, offset(i) - 1.8, offset(i) + 1.8, 0.95, 0.95, False), True)
            if i >= 60:
                core.add(T0 + (i - 60) * 100 * MS, 25.0, True, (i - 60) // 1000)
        last = core.flush()[28]
        fresh = LaneSwayCore()
        for i in range(27000, 30000):
            fresh.see(T0 + i * 100 * MS, (0.1, offset(i) - 1.8, offset(i) + 1.8, 0.95, 0.95, False), True)
        for i in range(27000, 29000):
            fresh.add(T0 + i * 100 * MS, 25.0, True, i // 1000)
        self.assertAlmostEqual(last["sumSq"], fresh.flush()[28]["sumSq"], places=9)


class LaneSwayTests(unittest.TestCase):
    def test_it_is_a_log_only_measure_named_after_its_block(self):
        self.assertEqual((LaneSway.name, LaneSway.owner_facing), ("lane", False))

    def test_the_block_and_its_rounding(self):
        out, blocks, pooled = run(sine(0.05, 0.1), n=1300, v=25.04,
                                  model=lambda t_s: dict(left_prob=0.912 + 0.0001 * (t_s > 30), right_prob=0.987))
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"lane"})
        block = blocks[0]
        self.assertEqual(set(block), BLOCK)
        self.assertIsInstance(block["n"], int)
        self.assertEqual(block["sumSq"], float("%.5g" % block["sumSq"]))          # five significant digits
        self.assertNotEqual(block["sumSq"], round(block["sumSq"], 3))
        for key in ("scoredS", "validS", "eligibleS", "vMean"):
            self.assertEqual(block[key], round(block[key], 1))
        self.assertEqual((block["vMean"], block["widthMin"], block["widthMax"], block["probMin"]),
                         (25.0, 3.6, 3.6, 0.91))

    def test_seconds_one_decimal_and_metres_two(self):
        out, blocks, pooled = run(n=1300, period_ms=103.0, model=lambda t_s: dict(left_y=-1.823, right_y=1.823))
        block = blocks[0]
        # Evaluated in the first minute: the messages from 5.047 s to 51.294 s, 450 of 0.103 s each: 46.35 s.
        self.assertEqual((block["eligibleS"], block["validS"]), (46.4, 46.4))
        self.assertEqual(block["scoredS"], 43.4)                      # 421 of them: 43.363 s
        self.assertEqual((block["widthMin"], block["widthMax"]), (3.65, 3.65))    # 3.646 m

    def test_a_known_wave_reads_its_own_rms_and_is_booked_8_6_s_late(self):
        out, blocks, pooled = run(sine(0.05, 0.1), n=1300)
        self.assertAlmostEqual(quality_values.lane_sway_sd(pooled), 0.036, delta=0.004)
        for w in (0, 1):
            self.assertAlmostEqual(quality_values.lane_sway_sd(blocks[w]), 0.036, delta=0.004)
        # Nothing is clean before 5.1 s. A message is evaluated by the first Sample 8.6 s after it: by 59.9 s
        # those up to 51.3 s. They are accepted from 5.1 s, and scored from 8.1 s: before that, over 20 % of
        # their window lies in the first 5.0 s of the drive, where nothing is valid.
        self.assertEqual((blocks[0]["eligibleS"], blocks[0]["validS"]), (46.3, 46.3))
        self.assertEqual((blocks[0]["n"], blocks[0]["scoredS"]), (433, 43.3))
        self.assertEqual((blocks[1]["n"], blocks[1]["scoredS"]), (600, 60.0))

    def test_a_low_line_probability_for_three_seconds(self):
        out, blocks, pooled = run(n=700, model=lambda t_s: dict(left_prob=0.85 if 20.0 <= t_s < 23.0 else 0.95))
        plain = run(n=700)[2]
        self.assertAlmostEqual(plain["validS"] - pooled["validS"], 3.0, delta=0.051)
        self.assertAlmostEqual(plain["eligibleS"], pooled["eligibleS"], delta=0.051)
        # Not scored: the 30 themselves and every message with more than 2.0 s of them in its window.
        self.assertEqual(plain["n"] - pooled["n"], 90)
        self.assertEqual(pooled["probMin"], 0.85)

    def test_a_lane_4_6_m_wide_for_three_seconds(self):
        wide = lambda t_s: dict(left_y=-2.3, right_y=2.3) if 20.0 <= t_s < 23.0 else None
        out, blocks, pooled = run(n=700, model=wide)
        plain = run(n=700)[2]
        self.assertAlmostEqual(plain["validS"] - pooled["validS"], 3.0, delta=0.051)
        self.assertEqual(plain["n"] - pooled["n"], 90)
        self.assertEqual((pooled["widthMin"], pooled["widthMax"]), (3.6, 4.6))

    def test_a_jump_of_0_4_m_between_two_messages(self):
        out, blocks, pooled = run(lambda t_s: 0.4 if t_s >= 25.0 else 0.0, n=700)
        plain = run(n=700)[2]
        # Every message from 1.0 s before the jump to 2.0 s after it is invalid: 24.0 s to 27.0 s, 31 of them.
        self.assertAlmostEqual(plain["validS"] - pooled["validS"], 3.1, delta=0.051)
        self.assertLess(quality_values.lane_sway_sd(pooled), 0.2)     # nobody is scored across the jump
        self.assertGreater(pooled["n"], 400)

    def test_windows_with_under_80_percent_valid_time_score_nothing(self):
        # Low line probability from 20 s to 40 s: a message is scored only with 8 of its 10 s valid.
        out, blocks, pooled = run(n=700, model=lambda t_s: dict(right_prob=0.5 if 20.0 <= t_s < 40.0 else 0.95))
        # Not scored: the messages from 17.0 s to 42.9 s; every one of them has over 2.0 s of the stretch in view.
        plain = run(n=700)[2]
        self.assertEqual(plain["n"] - pooled["n"], 260)
        self.assertAlmostEqual(plain["validS"] - pooled["validS"], 20.0, delta=0.051)

    def test_nothing_is_scored_outside_the_shared_gates(self):
        for name, more in (("slow", dict(v=14.9)),
                           ("steering off", dict(extra=lambda t_s: {"carControl": dict(lat=False)})),
                           ("lane change", dict(model=lambda t_s: dict(state="laneChangeStarting"))),
                           ("parked", dict(v=0.0, extra=lambda t_s: {"carState": dict(v=0.0, gear="park")}))):
            with self.subTest(case=name):
                out, blocks, pooled = run(sine(0.05, 0.1), n=700, **more)
                self.assertEqual(blocks, {})
                self.assertEqual(len(out), 2)

    def test_a_curve_is_valid_but_not_scored(self):
        out, blocks, pooled = run(sine(0.05, 0.1), n=700,
                                  extra=lambda t_s: {"controlsState": dict(desired=0.00025)})
        self.assertEqual((pooled["n"], pooled["scoredS"], pooled["sumSq"]), (0, 0.0, 0.0))
        self.assertIsNone(pooled["vMean"])
        self.assertGreater(pooled["validS"], 55.0)
        self.assertIsNone(quality_values.lane_sway_sd(pooled))

    def test_a_press_on_the_wheel_takes_out_the_messages_around_it(self):
        out, blocks, pooled = run(n=700, extra=lambda t_s: {"carState": dict(pressed=30.0 <= t_s < 30.3)})
        plain = run(n=700)[2]
        self.assertAlmostEqual(plain["eligibleS"] - pooled["eligibleS"], 7.2, delta=0.11)

    def test_the_sample_a_message_comes_with_must_be_within_half_a_second(self):
        # The model stream carries on through a 0.7 s stall of the agent's own loop; the message that waited
        # for it comes with a Sample 0.6 s younger than itself.
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(400, 100, head[-1] + 700 * MS)
        late_model = {}

        def batches_with_late_message():
            out = batches(times=times)
            stamp = head[-1] + 100 * MS                               # a message stamped 0.1 s after the last tick
            out[300]["drivingModelData"] = (stamp, synth.model())
            late_model["t"] = stamp
            return out
        p = Pipeline(PipelineConfig(measures=(LaneSway,)))
        out = synth.summaries(synth.run(p, batches_with_late_message()))
        pooled = quality_values.pool("lane", [s.get("lane") for s in out])
        self.assertEqual(sum(s["health"]["gapN"] for s in out), 1)
        self.assertGreater(pooled["n"], 300)                          # and nothing breaks

    def test_a_message_over_half_a_second_older_than_its_sample_is_not_accepted(self):
        # As in a decimated log, where a model message stays fresh for 1.25 s: every message is stamped well
        # before the carState it arrives with.
        def stale(by_ms):
            out = batches(n=700)
            for b in out:
                t_ns, msg = b["drivingModelData"]
                b["drivingModelData"] = (t_ns - by_ms * MS, msg)
            p = Pipeline(PipelineConfig.for_decimated_log(measures=(LaneSway,)))
            return quality_values.pool("lane", [s.get("lane") for s in synth.summaries(synth.run(p, out))])
        self.assertGreater(stale(500)["n"], 400)                      # exactly half a second: accepted
        self.assertIsNone(stale(501))

    def test_a_message_newer_than_its_car_state_is_scored_with_the_next_sample(self):
        events = []
        for i in range(700):
            t_ns = T0 + i * 100 * MS
            events.append((t_ns, "carState", synth.car_state()))
            events.append((t_ns + 5 * MS, "carControl", synth.car_control()))
            events.append((t_ns + 5 * MS, "controlsState", synth.controls_state()))
            events.append((t_ns + 5 * MS, "carOutput", synth.car_output()))
            events.append((t_ns + 30 * MS, "drivingModelData", synth.model()))
            events.append((t_ns, "onroadEvents", synth.alerts()))
        ticks = [T0 + 50 * MS + i * 100 * MS for i in range(700)]
        p = Pipeline(PipelineConfig(measures=(LaneSway,)))
        out = synth.summaries(synth.run(p, synth.tick_batches(events, ticks)))
        pooled = quality_values.pool("lane", [s.get("lane") for s in out])
        self.assertGreater(pooled["n"], 550)
        self.assertLess(pooled["sumSq"], 1e-12)

    def test_a_stream_of_two_messages_a_second(self):
        # As in a decimated log: a new model message with every fifth carState.
        def thin(batch_list):
            for i, b in enumerate(batch_list):
                if i % 5:
                    del b["drivingModelData"]
            return batch_list
        p = Pipeline(PipelineConfig.for_decimated_log(measures=(LaneSway,)))
        out = synth.summaries(synth.run(p, thin(batches(sine(0.05, 0.1), n=1300))))
        pooled = quality_values.pool("lane", [s.get("lane") for s in out])
        self.assertAlmostEqual(quality_values.lane_sway_sd(pooled), 0.036, delta=0.004)
        self.assertAlmostEqual(pooled["scoredS"], 130.0 - 8.0 - 5.0, delta=1.01)   # as at ten messages a second
        self.assertEqual(pooled["n"], round(pooled["scoredS"] / 0.5))
        # A jump between two messages half a second apart counts on a slow stream (it would not on a fast one):
        # the messages from 1.0 s before it to 2.0 s after it, seven of them, are invalid.
        p = Pipeline(PipelineConfig.for_decimated_log(measures=(LaneSway,)))
        out = synth.summaries(synth.run(p, thin(batches(lambda t_s: 0.4 if t_s >= 60.0 else 0.0, n=1300))))
        jumped = quality_values.pool("lane", [s.get("lane") for s in out])
        self.assertAlmostEqual(pooled["validS"] - jumped["validS"], 3.5, delta=0.051)

    def test_a_stall_releases_what_waits_and_starts_a_new_run(self):
        head = synth.ticks(580, 100, T0)
        times = head + synth.ticks(400, 100, head[-1] + 300 * MS)
        # The truck stands 0.25 m further over after the stall: no window may hold both places.
        out, blocks, pooled = run(lambda t_s: 0.0 if t_s < 58.0 else 0.25, times=times)
        self.assertEqual(sum(s["health"]["gapN"] for s in out), 1)
        self.assertLess(pooled["sumSq"], 1e-12)
        # Accepted: 5.1 s to 56.1 s (2.0 s before the first Sample after the stall, at 58.2 s) and 63.3 s to
        # 96.1 s (the drive ends at 98.1 s). The first lot is released by the stall, into the window it falls
        # in: without that, only the messages up to 49.6 s would have been evaluated by then.
        self.assertEqual(blocks[0]["eligibleS"], 51.1)
        self.assertEqual(blocks[1]["eligibleS"], 32.9)
        self.assertEqual(blocks[0]["scoredS"], 46.0)                  # 8.1 s to 54.0 s

    def test_a_stall_drops_the_messages_held(self):
        measure = LaneSway()
        p = Pipeline(PipelineConfig(measures=(lambda: measure,)))
        head = synth.ticks(300, 100, T0)
        for b in batches(times=head):
            p.ingest_tick(b)
        self.assertGreater(measure.buffered, 100)
        p.ingest_tick(batches(times=[head[-1] + 300 * MS])[0])
        self.assertEqual(measure.buffered, 1)                         # the message that came with the new run

    def test_the_drive_end_releases_what_waits(self):
        out, blocks, pooled = run(sine(0.05, 0.1), n=400)
        # Accepted from 5.1 s to 37.9 s. Scored from 8.1 s to 35.0 s: nearer the end, over 20 % of the window
        # lies in the last 2.0 s, where nothing is valid.
        self.assertEqual(pooled["eligibleS"], 32.9)
        self.assertEqual(pooled["scoredS"], 27.0)
        self.assertEqual(sorted(blocks), [0])

    def test_blocks_only_where_a_message_was_accepted(self):
        out, blocks, pooled = run(n=1300, extra=lambda t_s: {"carControl": dict(lat=t_s >= 70.0)})
        self.assertEqual(sorted(blocks), [1, 2])                      # evaluated from 75.1 + 8.6 s on

    def test_beside_another_measure(self):
        out, blocks, pooled = run(n=700, measures=(LaneSway, SteeringTime))
        self.assertEqual(out[0]["steeringS"], 60.0)
        self.assertIn("lane", out[0])


if __name__ == "__main__":
    unittest.main()
