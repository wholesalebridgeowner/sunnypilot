"""GateTracker: the shared flags of every Sample, and when each becomes known."""
import random
import unittest

from gates import GateTracker
from samples import Sample

MS = 1_000_000
SEC = 1_000_000_000
T0 = 2_000 * SEC


def stream(times_ns, change=None, **common):
    """Samples of a straight at 25 m/s with steering on; `change(i, t_ns)` returns the fields that differ."""
    out, prev = [], None
    for i, t_ns in enumerate(times_ns):
        raw = 0.10 if prev is None else (t_ns - prev) / 1e9
        fields = dict(t=t_ns / 1e9, dt=min(raw, 0.25), gap=raw > 0.25, raw_dt=raw, v_ego=25.0, gear_drive=True,
                      lat_active=True, actuators_torque=0.0, desired_curvature=0.0, actual_curvature=0.0,
                      torque_output=0.0, lane_change_state=0, alerts=frozenset())
        fields.update(common)
        if change is not None:
            fields.update(change(i, t_ns) or {})
        out.append(Sample(**fields))
        prev = t_ns
    return out


def grid(n, period_ms=100, start_ns=T0):
    return [start_ns + i * period_ms * MS for i in range(n)]


def run(samples):
    """Every Sample's flags, final (the drive has ended), plus the order of release."""
    tracker = GateTracker()
    flags, released = [], []
    for s in samples:
        g, out = tracker.add(s)
        flags.append(g)
        released.extend(out)
    released.extend(tracker.end())
    return flags, released


def between(lo_ns, hi_ns):
    return lambda i, t_ns: lo_ns <= t_ns < hi_ns


class SteeringOnTests(unittest.TestCase):
    def test_lat_on_needs_a_present_true_lat_active(self):
        flags, _ = run(stream(grid(3), lambda i, t: {"lat_active": (True, False, None)[i]}))
        self.assertEqual([g.lat_on for g in flags], [True, False, False])
        self.assertEqual([g.steering for g in flags], [True, False, False])

    def test_alert_marks_forced_zero(self):
        flags, _ = run(stream(grid(2), lambda i, t: {"alerts": frozenset({"belowSteerSpeed"})} if i else {}))
        self.assertEqual([(g.lat_on, g.forced_zero, g.steering) for g in flags],
                         [(True, False, True), (True, True, False)])

    def test_alert_without_lat_active_is_not_forced_zero(self):
        flags, _ = run(stream(grid(1), lat_active=False, alerts=frozenset({"belowSteerSpeed"})))
        self.assertFalse(flags[0].forced_zero)

    def test_mid_drive_start_without_an_alert_list_is_not_forced_zero(self):
        # The agent's first Sample comes mid-drive: what the truck did before is unknown.
        flags, _ = run(stream(grid(20), alerts=None))
        self.assertFalse(any(g.forced_zero for g in flags))
        self.assertTrue(all(g.steering for g in flags))

    def test_unknown_latch_stays_unknown_at_low_speed_in_drive(self):
        flags, _ = run(stream(grid(20), alerts=None, v_ego=10.0))
        self.assertFalse(any(g.forced_zero for g in flags))

    def test_fallback_latch_after_park_until_the_start_speed_is_passed(self):
        def change(i, t):
            if i < 10:
                return {"gear_drive": False, "v_ego": 0.0, "lat_active": False}
            return {"v_ego": 10.0 if i < 20 else (14.5 if i < 30 else 9.0)}
        flags, _ = run(stream(grid(40), change, alerts=None))
        self.assertTrue(all(g.forced_zero for g in flags[10:20]))       # in Drive, never yet at 14.5 m/s
        self.assertFalse(any(g.forced_zero for g in flags[20:]))        # passed once: steered at every speed after
        self.assertTrue(all(g.steering for g in flags[20:]))

    def test_latch_falls_back_when_the_gear_leaves_drive(self):
        def change(i, t):
            if i < 5 or 20 <= i < 25:
                return {"gear_drive": False, "v_ego": 0.0, "lat_active": False}       # Park
            return {"v_ego": 20.0 if i < 15 else 10.0}                                # Drive
        flags, _ = run(stream(grid(35), change, alerts=None))
        self.assertFalse(any(g.forced_zero for g in flags[5:20]))       # at speed once: steered at 10 m/s too
        self.assertTrue(all(g.forced_zero for g in flags[25:]))         # after Park the truck has to pass it again
        self.assertFalse(any(g.steering for g in flags[25:]))

    def test_alert_list_wins_over_the_latch(self):
        def change(i, t):
            if i < 5:
                return {"gear_drive": False, "v_ego": 0.0, "alerts": None}
            return {"v_ego": 10.0, "alerts": frozenset() if i >= 10 else None}
        flags, _ = run(stream(grid(15), change))
        self.assertTrue(all(g.forced_zero for g in flags[5:10]))
        self.assertFalse(any(g.forced_zero for g in flags[10:]))

    def test_latch_forgets_at_a_hole_longer_than_five_seconds(self):
        parked = grid(600)                                              # 60 s in Park
        for hole_s, expect_forced in ((600, False), (4, True)):
            with self.subTest(hole_s=hole_s):
                driving = grid(100, start_ns=parked[-1] + hole_s * SEC)
                def change(i, t):
                    if t <= parked[-1]:
                        return {"gear_drive": False, "v_ego": 0.0, "lat_active": False}
                    return {"v_ego": 10.0}
                flags, _ = run(stream(parked + driving, change, alerts=None))
                self.assertEqual(any(g.forced_zero for g in flags), expect_forced)
                if expect_forced:
                    self.assertTrue(all(g.forced_zero for g in flags[600:]))

    def test_at_limit_needs_lat_on_and_a_known_command(self):
        values = [(True, 343.0), (True, -350.0), (True, 342.9), (True, None), (False, 350.0), (None, 350.0)]
        flags, _ = run(stream(grid(len(values)),
                              lambda i, t: {"lat_active": values[i][0], "torque_output": values[i][1]}))
        self.assertEqual([g.at_limit for g in flags], [True, True, False, False, False, False])


class DriverTests(unittest.TestCase):
    def driver(self, pressed, period_ms=100, times=None):
        times = times or grid(len(pressed), period_ms)
        flags, _ = run(stream(times, lambda i, t: {"steering_pressed": pressed[i]}))
        return [g.driver for g in flags]

    def test_single_pressed_samples_never_count(self):
        pressed = [i % 10 == 3 for i in range(100)]
        self.assertFalse(any(self.driver(pressed)))

    def test_second_pressed_sample_in_a_row_counts_until_released(self):
        pressed = [False, True, True, True, False, True, False]
        self.assertEqual(self.driver(pressed), [False, False, True, True, False, False, False])

    def test_second_in_a_row_at_the_shortest_and_longest_loop_period(self):
        for period_ms in (88, 130):
            with self.subTest(period_ms=period_ms):
                self.assertEqual(self.driver([True, True, True], period_ms), [False, True, True])

    def test_span_of_exactly_the_minimum_counts(self):
        at = lambda *offsets_ms: [T0 + ms * MS for ms in offsets_ms]
        self.assertEqual(self.driver([True, True, True], times=at(0, 80, 160)), [False, True, True])
        self.assertEqual(self.driver([True, True, True], times=at(0, 79, 160)), [False, False, True])

    def test_span_is_by_time_stamp_not_by_count(self):
        self.assertEqual(self.driver([True, True, True, True], period_ms=30), [False, False, False, True])

    def test_gap_sample_ends_a_run(self):
        times = [T0, T0 + 100 * MS, T0 + 400 * MS, T0 + 500 * MS]
        self.assertEqual(self.driver([True, True, True, True], times=times), [False, True, False, True])

    def test_recent_lasts_two_seconds_and_half_a_sample(self):
        pressed = [False, True, True] + [False] * 30
        flags, _ = run(stream(grid(len(pressed)), lambda i, t: {"steering_pressed": pressed[i]}))
        recent = [g.driver_recent for g in flags]
        self.assertEqual(recent[:3], [False, False, True])
        self.assertTrue(all(recent[3:23]))           # the 20th Sample after the last driver Sample is inside
        self.assertFalse(any(recent[23:]))


class CleanTests(unittest.TestCase):
    """Nothing is clean from 2.0 s before a disturbed Sample to 5.0 s after it (closed, by time stamp)."""
    N = 300                                          # 30 s; disturbance in the middle, clear of both ends

    def check(self, change, first_i, last_i, period_ms=100, **common):
        times = grid(self.N, period_ms)
        flags, _ = run(stream(times, change, **common))
        lo, hi = times[first_i] - 2 * SEC, times[last_i] + 5 * SEC
        start_until, end_from = times[0] + 5 * SEC, times[-1] - 2 * SEC
        for g, t_ns in zip(flags, times):
            expected = not (lo <= t_ns <= hi) and t_ns > start_until and t_ns <= end_from
            self.assertEqual(g.clean, expected, msg=f"at {(t_ns - T0) / 1e9:.3f} s")
        self.assertTrue(all(g.disturbed for g in flags[first_i:last_i + 1]))
        return flags

    def one_sample(self, **fields):
        return self.check(lambda i, t: fields if i == 150 else None, 150, 150)

    def test_undisturbed_straight_is_clean_except_at_both_ends(self):
        flags, _ = run(stream(grid(self.N)))
        clean = [g.clean for g in flags]
        self.assertEqual(clean.index(True), 51)      # the first Sample disturbs up to and including 5.0 s
        self.assertEqual(clean.count(True), self.N - 51 - 20)
        self.assertTrue(clean[self.N - 21])          # exactly 2.0 s before the last Sample
        self.assertFalse(any(clean[self.N - 20:]))   # the last 2.0 s cannot be clean
        self.assertEqual([g.disturbed for g in flags].count(True), 1)

    def test_steering_off(self):
        self.one_sample(lat_active=False)

    def test_car_control_missing(self):
        self.one_sample(lat_active=None)

    def test_forced_zero(self):
        self.one_sample(alerts=frozenset({"belowSteerSpeed"}))

    def test_lane_change_state(self):
        for state in (1, 2, 3):
            with self.subTest(state=state):
                self.one_sample(lane_change_state=state)

    def test_model_stream_missing(self):
        self.one_sample(lane_change_state=None)

    def test_blinker(self):
        self.one_sample(blinker=True)

    def test_desired_curvature_unknown(self):
        self.one_sample(desired_curvature=None)

    def test_actual_curvature_unknown(self):
        self.one_sample(actual_curvature=None)

    def test_press_of_a_third_of_a_second(self):
        # Pressed on Samples 150..152: `driver` is true from the second one.
        flags = self.check(lambda i, t: {"steering_pressed": True} if 150 <= i <= 152 else None, 151, 152)
        self.assertFalse(flags[150].disturbed)

    def test_short_blips_disturb_nothing(self):
        times = grid(self.N)
        flags, _ = run(stream(times, lambda i, t: {"steering_pressed": i % 10 == 0}))
        self.assertFalse(any(g.driver for g in flags))
        self.assertTrue(all(g.clean for g in flags[51:self.N - 20]))

    def test_stall_of_three_tenths_of_a_second(self):
        times = grid(150) + grid(150, start_ns=T0 + 149 * 100 * MS + 300 * MS)
        flags, _ = run(stream(times))
        self.assertTrue(flags[150].sample.gap)
        self.assertEqual([g.disturbed for g in flags].count(True), 2)       # the first Sample and the gap Sample
        gap_ns = times[150]
        for g, t_ns in zip(flags, times):
            near = gap_ns - 2 * SEC <= t_ns <= gap_ns + 5 * SEC
            if times[0] + 5 * SEC < t_ns <= times[-1] - 2 * SEC:
                self.assertEqual(g.clean, not near, msg=f"at {(t_ns - T0) / 1e9:.3f} s")

    def test_interval_ends_are_closed_at_an_uneven_loop_period_too(self):
        self.check(lambda i, t: {"blinker": True} if i == 150 else None, 150, 150, period_ms=103)

    def test_eligible_needs_fifteen_metres_per_second(self):
        slow, _ = run(stream(grid(self.N), v_ego=14.9))
        fast, _ = run(stream(grid(self.N), v_ego=15.0))
        self.assertTrue(any(g.clean for g in slow))
        self.assertFalse(any(g.eligible for g in slow))
        self.assertFalse(any(g.straight_eligible for g in slow))
        self.assertEqual([g.eligible for g in fast], [g.clean for g in fast])


class StraightTests(unittest.TestCase):
    N = 300

    def straight(self, change=None, **common):
        flags, _ = run(stream(grid(self.N), change, **common))
        return flags

    def test_small_mean_curvature_is_straight(self):
        flags = self.straight(desired_curvature=0.00015)
        self.assertTrue(all(g.straight for g in flags[40:self.N - 40]))

    def test_gentle_curve_is_not_straight(self):
        flags = self.straight(desired_curvature=0.00025)
        self.assertFalse(any(g.straight for g in flags))
        self.assertFalse(any(g.straight_eligible for g in flags))
        self.assertTrue(any(g.eligible for g in flags))

    def test_it_is_the_mean_over_ten_seconds_not_the_sample(self):
        # 2 s of a clear bend inside a straight: the samples around it share its mean.
        flags = self.straight(lambda i, t: {"desired_curvature": 0.002} if 140 <= i < 160 else None)
        self.assertFalse(any(g.straight for g in flags[110:190]))       # 20 of 101 samples: mean 0.0004
        self.assertTrue(all(g.straight for g in flags[40:89]))
        self.assertTrue(all(g.straight for g in flags[211:self.N - 40]))

    def test_sample_five_seconds_away_is_inside_the_window_whatever_the_jitter(self):
        # Time stamps jitter by about a millisecond. One sharp Sample makes the mean of every
        # window that holds it too large, so it shows exactly which Samples have it in their window.
        rng = random.Random(5)
        for trial in range(20):
            times = [T0 + i * 100 * MS + rng.randrange(-MS, MS + 1) for i in range(self.N)]
            flags, _ = run(stream(times, lambda i, t: {"desired_curvature": 0.05} if i == 150 else None))
            straight = [g.straight for g in flags]
            with self.subTest(trial=trial):
                self.assertFalse(any(straight[100:201]))                # up to 5.0 s away, both sides: inside
                self.assertTrue(all(straight[40:100]))                  # 5.1 s away: outside
                self.assertTrue(all(straight[201:self.N - 40]))

    def test_left_and_right_cancel(self):
        flags = self.straight(lambda i, t: {"desired_curvature": 0.001 if i < 150 else -0.001})
        self.assertTrue(flags[150].straight)                            # 50 before, 51 from here on
        self.assertFalse(flags[120].straight)

    def test_needs_eight_seconds_of_known_curvature(self):
        flags = self.straight()
        self.assertFalse(any(g.straight for g in flags[:28]))           # start of data: under 8.0 s in the window
        self.assertTrue(all(g.straight for g in flags[31:self.N - 31]))
        self.assertFalse(any(g.straight for g in flags[self.N - 28:]))  # end of data

    def test_unknown_curvature_does_not_count_as_cover(self):
        flags = self.straight(lambda i, t: {"desired_curvature": None} if 135 <= i < 165 else None)
        self.assertFalse(any(g.straight for g in flags[125:175]))       # 3 s unknown: 7.1 s of cover at most
        self.assertTrue(all(g.straight for g in flags[40:105]))

    def test_straight_eligible_is_both(self):
        flags = self.straight()
        for g in flags:
            self.assertEqual(g.straight_eligible, g.eligible and g.straight)
        self.assertTrue(any(g.straight_eligible for g in flags))

    def test_before_a_gap_only_what_arrived_before_it_counts(self):
        times = grid(150) + grid(150, start_ns=T0 + 149 * 100 * MS + 300 * MS)
        flags, _ = run(stream(times))
        self.assertTrue(flags[149 - 31].straight)                       # 5.05 s back and 3.1 s forward exist
        self.assertFalse(flags[149 - 27].straight)                      # only 2.7 s forward: under 8.0 s
        self.assertTrue(flags[149 - 27].clean)                          # more than 2.0 s before the gap Sample


    def test_after_a_gap_only_what_arrived_after_it_counts(self):
        times = grid(150) + grid(150, start_ns=T0 + 149 * 100 * MS + 300 * MS)
        flags, _ = run(stream(times))
        self.assertTrue(flags[150].sample.gap)
        self.assertFalse(any(g.straight for g in flags[150:150 + 28]))  # the window starts again at the gap Sample
        self.assertTrue(flags[150 + 28].straight)                       # 0.25 s + 7.8 s of the new run


class LatenessTests(unittest.TestCase):
    def test_each_flag_is_known_as_soon_as_its_data_has_arrived(self):
        tracker = GateTracker()
        samples = stream(grid(200))
        first, _ = tracker.add(samples[0])
        self.assertIs(first.disturbed, True)
        self.assertIsNone(first.clean)
        watched = None
        for i, s in enumerate(samples[1:120], start=1):
            g, _ = tracker.add(s)
            if i == 60:
                watched = g
        # the newest Sample is 119: Sample 60 is 5.9 s old
        self.assertIs(watched.clean, True)
        self.assertIs(watched.straight, True)
        tracker2 = GateTracker()
        flags = [tracker2.add(s)[0] for s in samples[:81]]
        self.assertIsNone(flags[61].clean)                              # 1.9 s old
        self.assertIsNotNone(flags[60].clean)                           # 2.0 s old: known
        self.assertIsNotNone(flags[60].eligible)
        self.assertIsNone(flags[30].straight)                           # 5.0 s old
        self.assertIsNone(flags[30].straight_eligible)
        tracker2.add(samples[81])                                       # 5.1 s old now
        self.assertIsNotNone(flags[30].straight)
        self.assertIsNotNone(flags[30].straight_eligible)

    def test_release_six_seconds_late_in_order_once(self):
        tracker = GateTracker()
        samples = stream(grid(100))
        got = []
        for i, s in enumerate(samples):
            g, released = tracker.add(s)
            for r in released:
                got.append((i, r))
                self.assertIsNotNone(r.clean)
                self.assertIsNotNone(r.straight)
        self.assertEqual(got[0][0], 60)                                 # the first Sample at or past t + 6.0 s
        self.assertIs(got[0][1].sample, samples[0])
        self.assertEqual([r.sample.t for _, r in got], [s.t for s in samples[:40]])
        self.assertTrue(all(i - samples.index(r.sample) == 60 for i, r in got))
        rest = tracker.end()
        self.assertEqual([r.sample.t for r in rest], [s.t for s in samples[40:]])
        self.assertEqual(tracker.end(), [])

    def test_release_at_an_uneven_loop_period(self):
        tracker = GateTracker()
        samples = stream(grid(200, period_ms=103))
        for s in samples:
            _, released = tracker.add(s)
            for r in released:
                age = s.t - r.sample.t
                self.assertGreaterEqual(age, 6.0 - 1e-9)
                self.assertLess(age, 6.0 + 0.103 + 1e-9)

    def test_gap_sample_releases_everything_waiting_with_final_flags(self):
        times = grid(150) + grid(150, start_ns=T0 + 149 * 100 * MS + 300 * MS)
        samples = stream(times)
        tracker = GateTracker()
        for s in samples[:150]:
            tracker.add(s)
        g, released = tracker.add(samples[150])
        self.assertEqual([r.sample.t for r in released], [s.t for s in samples[90:150]])
        self.assertTrue(all(r.clean is not None and r.straight is not None for r in released))
        clean = [r.clean for r in released]
        self.assertEqual(clean, [True] * 42 + [False] * 18)             # only the 2.0 s before the gap Sample lose it
        self.assertIs(g.disturbed, True)
        self.assertEqual(tracker.waiting, 1)                            # the gap Sample starts the new run

    def test_end_releases_everything_and_the_last_two_seconds_are_not_clean(self):
        tracker = GateTracker()
        samples = stream(grid(150))
        for s in samples:
            tracker.add(s)
        released = tracker.end()
        self.assertEqual(len(released), 60)
        self.assertEqual([r.clean for r in released], [True] * 40 + [False] * 20)
        self.assertEqual(tracker.waiting, 0)

    def test_memory_stays_bounded(self):
        tracker = GateTracker()
        for s in stream(grid(3000), lambda i, t: {"lat_active": i % 7 != 0}):
            tracker.add(s)
        self.assertLessEqual(tracker.waiting, 62)
        self.assertLessEqual(tracker.buffered, 250)


if __name__ == "__main__":
    unittest.main()
