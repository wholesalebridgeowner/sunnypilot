"""Braking episodes: each brake application counted once, graded by the speed lost in one second."""
import math
import unittest

import measure_set
import synth
from measure_api import Measure
from measure_brake import BrakeDetector, Braking
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
NAN = float("nan")
RECORD = {"peakDrop1s", "peakDrop2s", "who", "tookOverFromCruise", "speedMs", "leadGapS", "leadDistanceM"}
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}


def speed(v0, *legs):
    """Speed over time (seconds from T0): `v0`, changed by each leg (from_s, to_s, acceleration)."""
    def at(t_s):
        v = v0
        for start, end, accel in legs:
            v += accel * max(0.0, min(t_s, end) - start)
        return max(v, 0.0)
    return at


def braking(start_s=10.0, seconds=3.0, decel=3.0, v0=25.0):
    return speed(v0, (start_s, start_s + seconds, -decel))


def batches(v=None, extra=None, n=300, period_ms=100.0, times=None, without=()):
    """One batch per tick. `v(t_s)` is the speed; `extra(t_s)` gives what else differs, as keywords of
    synth.batch (its carState values are added to the speed). `without` lists (from_s, to_s) spans to leave out."""
    times = times or synth.ticks(n, period_ms, T0)
    for start, end in without:
        times = [t for t in times if not T0 + round(start * SEC) <= t < T0 + round(end * SEC)]

    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        out = dict(extra(t_s) or {}) if extra else {}
        out["carState"] = dict(out.get("carState") or {}, v=v(t_s) if v else 25.0)
        return out
    return synth.drive(times, change)


def run(v=None, extra=None, measures=(Braking,), **more):
    """(summaries, brake records, every message) of one synthetic drive."""
    p = Pipeline(PipelineConfig(measures=measures))
    messages = synth.run(p, batches(v, extra, **more))
    return synth.summaries(messages), [data for kind, data in messages if kind == "brake"], messages


def pedal(from_s, to_s=1e9, **more):
    """The brake pedal is down from `from_s` to `to_s`."""
    return lambda t_s: {"carState": dict(more, brake=from_s <= t_s < to_s)}


def detector_run(rows):
    """Records of a detector fed (t_s, v, brake, cruise, lead distance) rows; the last one from the flush."""
    det = BrakeDetector()
    out = []
    for t_s, v, *rest in rows:
        brake, cruise, lead = (list(rest) + [False, False, None])[:3]
        out.append(det.add(round(t_s * SEC), v, brake, cruise, lead))
    out.append(det.flush())
    return [r for r in out if r is not None]


class DetectionTests(unittest.TestCase):
    def test_a_steady_drive_has_no_episode_and_still_sends_the_count(self):
        out, records, _ = run()
        self.assertEqual(records, [])
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS, {"brakeEpisodes"})
        self.assertEqual(out[0]["brakeEpisodes"], 0)

    def test_one_stop_is_one_record(self):
        out, records, _ = run(braking(decel=2.5), pedal(10.0, 13.0))
        self.assertEqual(len(records), 1)
        self.assertEqual(set(records[0]), RECORD)
        self.assertEqual(records[0], {"peakDrop1s": 2.5, "peakDrop2s": 5.0, "who": "driver", "tookOverFromCruise": False,
                                      "speedMs": 25.0, "leadGapS": None, "leadDistanceM": None})
        self.assertEqual(out[0]["brakeEpisodes"], 1)

    def test_it_takes_two_metres_per_second_lost_in_one_second(self):
        for decel, n in ((1.9, 0), (2.1, 1)):
            with self.subTest(decel=decel):
                out, records, _ = run(braking(decel=decel))
                self.assertEqual(len(records), n)
                self.assertEqual(out[0]["brakeEpisodes"], n)
        self.assertEqual(records[0]["peakDrop1s"], 2.1)

    def test_the_episode_closes_when_the_one_second_drop_falls_below_one(self):
        # Braking of 3 m/s^2 ends at 13.0 s; the drop over the last second is under 1.0 from 13.67 s on.
        p = Pipeline(PipelineConfig(measures=(Braking,)))
        sent_at = [i for i, b in enumerate(batches(braking()))
                   if any(kind == "brake" for kind, _ in p.ingest_tick(b))]
        self.assertEqual(sent_at, [137])

    def test_gentle_slowing_after_a_stop_keeps_the_episode_open(self):
        # 3 m/s^2, then 1.5 m/s^2: the drop never falls below 1.0 in between, so it is one episode.
        out, records, _ = run(speed(30.0, (10.0, 12.0, -3.0), (12.0, 15.0, -1.5), (15.0, 17.0, -3.0)))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["peakDrop1s"], 3.0)

    def test_a_wobbling_stop_counts_once(self):
        # 2.4 m/s^2 for 6 s with a wobble of 1.2 m/s^2 at 0.4 Hz: the one-second drop swings between about
        # 1.5 and 3.3 m/s, so it crosses 2.0 several times and never falls below 1.0.
        def v(t_s):
            tau = min(max(t_s - 10.0, 0.0), 6.0)
            w = 2 * math.pi * 0.4
            return 25.0 - 2.4 * tau - 1.2 * (1 - math.cos(w * tau)) / w
        drops = [v(t / 10 - 1.0) - v(t / 10) for t in range(110, 161)]
        self.assertLess(min(drops), 1.6)                               # the set-up does what it says
        self.assertGreater(sum(1 for a, b in zip(drops, drops[1:]) if (a < 2.0) != (b < 2.0)), 2)
        out, records, _ = run(v)
        self.assertEqual(len(records), 1)
        self.assertGreaterEqual(records[0]["peakDrop1s"], 3.2)
        self.assertLessEqual(records[0]["peakDrop1s"], 3.4)
        self.assertEqual(out[0]["brakeEpisodes"], 1)

    def test_two_stops_are_two_records(self):
        v = speed(25.0, (5.0, 8.0, -3.0), (8.0, 14.0, 1.5), (15.0, 18.0, -2.5))
        out, records, _ = run(v)
        self.assertEqual([r["peakDrop1s"] for r in records], [3.0, 2.5])
        self.assertEqual([r["speedMs"] for r in records], [25.0, 25.0])
        self.assertEqual(out[0]["brakeEpisodes"], 2)

    def test_speeding_up_is_never_an_episode(self):
        _, records, _ = run(speed(5.0, (5.0, 10.0, 4.0)))
        self.assertEqual(records, [])

    def test_the_peak_is_the_same_at_every_loop_period(self):
        for period_ms in (10, 100, 105, 113, 125, 250):
            with self.subTest(period_ms=period_ms):
                _, records, _ = run(braking(decel=3.5, seconds=4.0), n=int(30_000 / period_ms), period_ms=period_ms)
                self.assertEqual(len(records), 1)
                self.assertAlmostEqual(records[0]["peakDrop1s"], 3.5, delta=0.01)
                self.assertAlmostEqual(records[0]["peakDrop2s"], 7.0, delta=0.01)
                self.assertEqual(records[0]["speedMs"], 25.0)

    def test_the_two_second_peak_is_unknown_without_two_seconds_of_history(self):
        # The drive starts in a short stop: the episode opens at 1.0 s and closes at 1.5 s.
        _, records, _ = run(braking(start_s=0.0, seconds=0.8, decel=3.0))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["peakDrop1s"], 2.4)
        self.assertIsNone(records[0]["peakDrop2s"])

    def test_the_two_second_peak_starts_as_soon_as_two_seconds_are_known(self):
        _, records, _ = run(braking(start_s=0.0, seconds=1.5, decel=3.0))  # open from 1.0 s to 2.2 s
        self.assertEqual((records[0]["peakDrop1s"], records[0]["peakDrop2s"]), (3.0, 4.5))

    def test_values_are_rounded_for_the_record(self):
        _, records, _ = run(speed(25.123, (10.0, 13.0, -2.3456)))
        self.assertEqual((records[0]["peakDrop1s"], records[0]["peakDrop2s"], records[0]["speedMs"]),
                         (2.35, 4.69, 25.1))


class GlitchAndHoleTests(unittest.TestCase):
    def test_a_start_up_speed_step_counts_nothing(self):
        _, up, _ = run(lambda t_s: 0.0 if t_s < 5.0 else 22.0)
        _, down, _ = run(lambda t_s: 22.0 if t_s < 5.0 else 0.0)
        self.assertEqual((up, down), ([], []))

    def test_a_speed_step_discards_the_open_episode(self):
        v = braking(seconds=1.5, decel=2.5)                            # open from 10.8 s on
        _, without_step, _ = run(v)
        _, records, _ = run(lambda t_s: v(t_s) + (3.0 if t_s >= 11.2 else 0.0))
        self.assertEqual(len(without_step), 1)
        self.assertEqual(records, [])

    def test_a_step_just_over_two_metres_per_second_is_a_glitch(self):
        _, records, _ = run(lambda t_s: 25.0 if t_s < 10.0 else 22.75)
        self.assertEqual(records, [])

    def test_a_step_between_samples_0_3_seconds_apart_is_a_glitch(self):
        step = lambda t_s: 25.0 if t_s < 10.05 else 22.0
        _, records, _ = run(step, n=100, period_ms=300)                # Samples at 9.9 s and 10.2 s
        self.assertEqual(records, [])
        _, records, _ = run(step, without=((10.0, 10.25),))            # Samples at 9.9 s and 10.3 s: braking
        self.assertEqual(len(records), 1)

    def test_a_step_of_two_metres_per_second_is_still_real(self):
        _, records, _ = run(lambda t_s: 25.0 if t_s < 10.0 else 23.0)  # 2.0 m/s in one Sample: not over 2.0
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["peakDrop1s"], 2.0)

    def test_a_hard_stop_with_a_hole_still_counts(self):
        v = braking(decel=3.5)
        for hole in ((11.2, 11.8), (10.3, 10.9), (10.0, 11.4)):
            with self.subTest(hole=hole):
                out, records, _ = run(v, without=(hole,))
                self.assertEqual(len(records), 1)
                self.assertGreaterEqual(records[0]["peakDrop1s"], 3.0)
                self.assertAlmostEqual(records[0]["peakDrop1s"], 3.5, delta=0.01)
                self.assertEqual(out[0]["health"]["gapN"], 1)          # the detector lives through a gap Sample

    def test_a_slow_step_across_a_hole_is_not_a_glitch(self):
        # 2.4 m/s lost between two Samples 0.4 s apart: braking seen through a hole, not a sensor step.
        _, records, _ = run(braking(decel=6.0, seconds=0.4), without=((10.05, 10.35),))
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["peakDrop1s"], 2.4, delta=0.01)

    def test_a_long_hole_closes_the_episode_and_forgets_the_speeds_before_it(self):
        v = speed(30.0, (10.0, 14.0, -3.0))                            # 12 m/s lost, the last 6 of them unseen
        p = Pipeline(PipelineConfig(measures=(Braking,)))
        sent = [(i, data) for i, b in enumerate(batches(v, without=((12.05, 13.95),)))
                for kind, data in p.ingest_tick(b) if kind == "brake"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 121)                              # with the first Sample after the hole
        self.assertEqual(sent[0][1]["peakDrop1s"], 3.0)
        self.assertEqual(p.end()[-1][1]["brakeEpisodes"], 1)

    def test_a_hole_of_one_and_a_half_seconds_is_bridged(self):
        _, records, _ = run(braking(decel=2.5, seconds=5.0), without=((11.05, 12.45),))
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["peakDrop1s"], 2.5, delta=0.01)

    def test_every_third_sample_missing_gives_the_same_records(self):
        v = speed(28.0, (10.0, 13.0, -3.5), (15.0, 21.0, 1.5), (30.0, 33.0, -2.5), (35.0, 40.0, 1.5),
                  (45.0, 47.5, -4.0))
        full_times = synth.ticks(600, 100, T0)
        _, full, _ = run(v, pedal(29.0, 34.0), times=full_times)
        _, thinned, _ = run(v, pedal(29.0, 34.0), times=[t for i, t in enumerate(full_times) if i % 3 != 2])
        self.assertEqual(len(full), 3)
        self.assertEqual(len(thinned), 3)
        for a, b in zip(full, thinned):
            self.assertEqual({k: a[k] for k in ("who", "tookOverFromCruise", "speedMs", "leadGapS", "leadDistanceM")},
                             {k: b[k] for k in ("who", "tookOverFromCruise", "speedMs", "leadGapS", "leadDistanceM")})
            self.assertAlmostEqual(a["peakDrop1s"], b["peakDrop1s"], delta=0.011)
            self.assertAlmostEqual(a["peakDrop2s"], b["peakDrop2s"], delta=0.011)
        self.assertEqual([r["who"] for r in full], ["other", "driver", "other"])

    def test_a_car_state_without_speed_is_skipped_and_the_episode_survives(self):
        v = braking(decel=3.0)
        _, whole, _ = run(v)
        out, records, _ = run(lambda t_s: NAN if 11.15 <= t_s < 11.35 else v(t_s))
        self.assertEqual(records, whole)
        self.assertEqual(out[0]["n"], 298)                             # the two messages gave no Sample


class DetectorTests(unittest.TestCase):
    """The detector on its own, with speeds and times that are exact in binary."""

    def test_opens_at_exactly_two_and_not_just_under(self):
        self.assertEqual(len(detector_run([(0.0, 20.0), (1.0, 18.0), (2.0, 18.0)])), 1)
        self.assertEqual(detector_run([(0.0, 20.0), (1.0, 18.125), (2.0, 18.125)]), [])

    def test_stays_open_at_exactly_one_and_closes_just_under(self):
        rows = [(0.0, 20.0), (1.0, 17.0), (2.0, 16.0), (3.0, 13.0), (4.0, 13.0)]
        self.assertEqual([r["peakDrop1s"] for r in detector_run(rows)], [3.0])             # one episode
        rows[2] = (2.0, 16.125)
        self.assertEqual([r["peakDrop1s"] for r in detector_run(rows)], [3.0, 3.12])       # closed in between

    def test_speed_one_second_back_is_interpolated(self):
        records = detector_run([(0.0, 20.0), (0.5, 20.0), (1.0, 19.0), (1.25, 17.5), (2.5, 17.5)])
        self.assertEqual(records[0]["peakDrop1s"], 2.5)                # 20.0 at 0.25 s, half-way between two Samples
        self.assertEqual(records[0]["speedMs"], 20.0)

    def test_the_oldest_sample_stands_in_for_a_time_up_to_20_ms_before_it(self):
        self.assertEqual(len(detector_run([(0.0, 20.0), (0.98, 17.9), (1.5, 17.9)])), 1)
        self.assertEqual(detector_run([(0.0, 20.0), (0.97, 17.9), (1.5, 17.9)]), [])

    def test_the_buffer_holds_the_last_4_2_seconds(self):
        det = BrakeDetector()
        for i in range(100):
            det.add(i * 100 * MS, 25.0, False, False, None)
        self.assertEqual(det.buffered, 43)

    def test_time_running_backwards_closes_the_episode_and_starts_afresh(self):
        det = BrakeDetector()
        for i in range(30):
            self.assertIsNone(det.add(i * 100 * MS, 25.0 - 0.3 * max(0, i - 10), False, False, None))
        record = det.add(1_000 * MS, 19.0, False, False, None)        # an older time stamp
        self.assertEqual(record["peakDrop1s"], 3.0)
        self.assertEqual(det.buffered, 1)
        self.assertIsNone(det.flush())

    def test_the_opening_sample_is_counted_once(self):
        # Opens at 2.0 s with two Samples in the last second, then one more: three Samples in all.
        rows = [(0.0, 20.0), (1.0, 20.0, False, True), (2.0, 17.0, False, True), (3.0, 15.0), (4.0, 15.0)]
        self.assertEqual(detector_run(rows)[0]["who"], "cruise")       # 2 of 3
        rows[1] = (1.0, 20.0)
        self.assertEqual(detector_run(rows)[0]["who"], "other")        # 1 of 3

    def test_flush_without_an_open_episode_gives_nothing(self):
        self.assertIsNone(BrakeDetector().flush())


class WhoTests(unittest.TestCase):
    def who(self, extra, v=None):
        _, records, _ = run(v or braking(), extra)
        self.assertEqual(len(records), 1)
        return records[0]["who"], records[0]["tookOverFromCruise"]

    def test_with_the_pedal(self):
        self.assertEqual(self.who(pedal(10.0, 13.0)), ("driver", False))

    def test_with_the_pedal_while_the_cruise_still_reads_on_it_is_the_drivers(self):
        both = lambda t_s: {"carState": dict(brake=10.0 <= t_s < 13.0, cruise=True)}
        self.assertEqual(self.who(both), ("driver", True))

    def test_with_the_pedal_one_second_after_the_cruise_was_on(self):
        took_over = lambda t_s: {"carState": dict(brake=10.0 <= t_s < 13.0, cruise=t_s < 9.0)}
        self.assertEqual(self.who(took_over), ("driver", True))

    def test_the_cruise_must_have_been_on_within_three_seconds_before_the_pedal(self):
        for off_at_s, took_over in ((7.05, True), (6.95, False)):      # the last cruise Sample is 3.0 or 3.1 s before
            with self.subTest(off_at_s=off_at_s):
                extra = lambda t_s: {"carState": dict(brake=10.0 <= t_s < 13.0, cruise=t_s < off_at_s)}
                self.assertEqual(self.who(extra), ("driver", took_over))

    def test_by_the_cruise_alone(self):
        self.assertEqual(self.who(lambda t_s: {"carState": dict(cruise=True)}), ("cruise", False))

    def test_with_neither_pedal_nor_cruise(self):
        self.assertEqual(self.who(None), ("other", False))

    def test_the_cruise_needs_half_of_the_episodes_samples(self):
        # The episode opens at 10.7 s with the 11 Samples since 9.7 s and runs through 13.6 s: 40 Samples.
        for on_until_s, who in ((11.65, "cruise"), (11.55, "other")):  # 20 or 19 of them with the cruise on
            with self.subTest(on_until_s=on_until_s):
                extra = lambda t_s: {"carState": dict(cruise=9.65 <= t_s < on_until_s)}
                self.assertEqual(self.who(extra), (who, False))

    def test_a_pedal_late_in_the_episode_makes_it_the_drivers(self):
        late = lambda t_s: {"carState": dict(brake=12.0 <= t_s < 13.0, cruise=t_s < 11.0)}
        self.assertEqual(self.who(late), ("driver", True))             # cruise on within 3.0 s before the pedal
        late_no_cruise = lambda t_s: {"carState": dict(brake=12.0 <= t_s < 13.0, cruise=t_s < 8.0)}
        self.assertEqual(self.who(late_no_cruise), ("driver", False))

    def test_took_over_goes_by_the_first_pedal_sample_of_the_opening_second(self):
        # Pedal from 10.0 s; the episode opens at 10.7 s. Cruise last on at 7.0 s: within 3.0 s of the first
        # pedal Sample, though more than 3.0 s before the episode opens.
        extra = lambda t_s: {"carState": dict(brake=10.0 <= t_s < 13.0, cruise=t_s < 7.05)}
        self.assertEqual(self.who(extra), ("driver", True))

    def test_a_pedal_that_was_released_before_the_opening_second_does_not_count(self):
        extra = lambda t_s: {"carState": dict(brake=t_s < 9.0, cruise=True)}
        self.assertEqual(self.who(extra), ("cruise", False))


class LeadTests(unittest.TestCase):
    """Lead context: the Samples from 3.0 s to 1.5 s before the episode opens (at 10.7 s: 7.7 s to 9.2 s)."""

    def lead(self, with_lead, distance=lambda t_s: 30.0, v=None, **more):
        extra = lambda t_s: {"radarState": dict(lead=with_lead(t_s), distance=distance(t_s))}
        _, records, _ = run(v or braking(), extra, **more)
        self.assertEqual(len(records), 1)
        return records[0]["leadDistanceM"], records[0]["leadGapS"]

    def test_lead_ahead_gives_distance_and_time_gap(self):
        self.assertEqual(self.lead(lambda t_s: True), (30.0, 1.2))     # 30 m at 25 m/s

    def test_no_lead_gives_nothing(self):
        self.assertEqual(self.lead(lambda t_s: False), (None, None))

    def test_lead_for_ninety_percent_of_the_look_back_counts_and_seventy_does_not(self):
        # 16 Samples from 7.7 s to 9.2 s.
        self.assertEqual(self.lead(lambda t_s: not 7.65 <= t_s < 7.75), (30.0, 1.2))           # 15 of 16
        self.assertEqual(self.lead(lambda t_s: not 7.65 <= t_s < 7.95), (30.0, 1.2))           # 13 of 16: 81%
        self.assertEqual(self.lead(lambda t_s: not 7.65 <= t_s < 8.05), (None, None))          # 12 of 16: 75%
        self.assertEqual(self.lead(lambda t_s: not 7.65 <= t_s < 8.15), (None, None))          # 11 of 16: 69%

    def test_exactly_eighty_percent_counts(self):
        # Without the Sample at 8.5 s the look-back holds 15 Samples.
        hole = ((8.45, 8.55),)
        self.assertEqual(self.lead(lambda t_s: not 7.65 <= t_s < 7.95, without=hole), (30.0, 1.2))     # 12 of 15
        self.assertEqual(self.lead(lambda t_s: not 7.65 <= t_s < 8.05, without=hole), (None, None))    # 11 of 15

    def test_the_distance_is_the_upper_middle_value(self):
        ramp = lambda t_s: 20.0 + round((t_s - 7.7) * 10)             # 20, 21, ... 35 over the 16 Samples
        self.assertEqual(self.lead(lambda t_s: True, ramp), (28.0, 1.1))       # not 27.5, and not 27

    def test_a_lead_without_a_distance_is_no_lead(self):
        self.assertEqual(self.lead(lambda t_s: True, lambda t_s: NAN), (None, None))

    def test_only_the_look_back_window_counts(self):
        only_there = lambda t_s: 7.65 <= t_s < 9.25
        self.assertEqual(self.lead(only_there), (30.0, 1.2))
        self.assertEqual(self.lead(lambda t_s: not only_there(t_s)), (None, None))
        # 13 of its 16 Samples: one Sample more at either end without a lead would make it 13 of 17.
        self.assertEqual(self.lead(lambda t_s: 7.95 <= t_s < 9.25), (30.0, 1.2))
        self.assertEqual(self.lead(lambda t_s: 7.65 <= t_s < 8.95), (30.0, 1.2))

    def test_the_gap_uses_the_speed_two_seconds_before_the_episode_opened(self):
        # Slowing gently before the stop: 23.0 m/s at 8.7 s, two seconds before the opening Sample.
        v = speed(26.0, (5.7, 9.7, -1.0), (10.0, 13.0, -3.0))
        distance, gap = self.lead(lambda t_s: True, v=v)
        self.assertEqual((distance, gap), (30.0, 1.3))

    def test_no_gap_from_a_crawl(self):
        # Crawling at 0.9 m/s, pulling away to 5.9 m/s and braking at once: the episode opens at 10.8 s,
        # and two seconds before that the truck was still crawling. A time gap would mean nothing.
        crawl = lambda v0: speed(v0, (9.0, 10.0, 5.0), (10.0, 11.4, -4.0))
        self.assertEqual(self.lead(lambda t_s: True, v=crawl(0.9)), (None, None))
        self.assertEqual(self.lead(lambda t_s: True, v=crawl(1.1)), (30.0, 27.3))

    def test_distance_and_gap_have_one_decimal(self):
        self.assertEqual(self.lead(lambda t_s: True, lambda t_s: 31.26), (31.3, 1.3))

    def test_no_lead_context_without_two_seconds_of_history(self):
        # The episode opens at 1.7 s: the look-back holds the first three Samples, but no speed for -0.3 s.
        _, records, _ = run(braking(start_s=1.0), lambda t_s: {"radarState": dict(lead=True, distance=30.0)})
        self.assertEqual((records[0]["leadDistanceM"], records[0]["leadGapS"]), (None, None))

    def test_no_lead_context_when_the_look_back_has_no_sample(self):
        # A hole that long also clears the buffer, so no speed is known for two seconds before either.
        self.assertEqual(self.lead(lambda t_s: True, without=((7.65, 9.25),)), (None, None))

    def test_missing_lead_stream_is_no_lead(self):
        extra = lambda t_s: {"skip": ("radarState",)} if t_s >= 5.0 else {"radarState": dict(lead=True, distance=30.0)}
        _, records, _ = run(braking(), extra)
        self.assertEqual((records[0]["leadDistanceM"], records[0]["leadGapS"]), (None, None))


class BookingTests(unittest.TestCase):
    def test_an_episode_is_counted_in_the_window_where_it_closes(self):
        out, records, messages = run(braking(start_s=58.0, seconds=4.0), n=900)
        self.assertEqual(len(records), 1)
        self.assertEqual([s["brakeEpisodes"] for s in out], [0, 1])    # across the boundary: once
        kinds = [kind for kind, _ in messages]
        self.assertEqual(kinds, ["summary", "brake", "summary"])

    def test_a_stop_cut_by_the_end_of_the_drive_is_still_sent(self):
        out, records, messages = run(braking(start_s=28.0, seconds=4.0))
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["peakDrop1s"], 3.0, delta=0.01)
        self.assertEqual([kind for kind, _ in messages], ["brake", "summary"])     # before the last summary
        self.assertEqual(out[-1]["brakeEpisodes"], 1)
        self.assertEqual(out[-1]["windowS"], 30.0)

    def test_the_drive_end_sends_nothing_without_an_open_episode(self):
        _, records, messages = run(braking(start_s=5.0))
        self.assertEqual(len(records), 1)
        self.assertEqual([kind for kind, _ in messages], ["brake", "summary"])

    def test_the_count_starts_again_in_every_window(self):
        v = speed(25.0, (5.0, 8.0, -3.0), (8.0, 14.0, 1.5), (65.0, 68.0, -2.5), (68.0, 73.0, 1.5),
                  (130.0, 133.0, -2.5))
        out, records, _ = run(v, n=1900)
        self.assertEqual([s["brakeEpisodes"] for s in out], [1, 1, 1, 0])
        self.assertEqual(len(records), 3)

    def test_it_runs_with_steering_off_and_at_any_speed(self):
        extra = lambda t_s: {"carControl": dict(lat=False), "carState": dict(gear="neutral")}
        _, records, _ = run(braking(v0=8.0, seconds=2.0), extra)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["speedMs"], 8.0)


class RegistrationTests(unittest.TestCase):
    def test_it_is_a_measure_of_this_build_and_runs_with_the_owner_facing_ones(self):
        self.assertIn(Braking, measure_set.MEASURES)
        self.assertTrue(Braking.owner_facing)
        self.assertEqual(Braking.name, "brake")
        self.assertIn("brake", Pipeline().measure_names)

    def test_it_needs_nothing_but_the_measure_interface(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(__file__).with_name("measure_brake.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, {"__future__", "collections", "measure_api", "shared_rules"})

    def test_driving_time_comes_from_the_steering_measure(self):
        out, _, _ = run(braking(), measures=(SteeringTime, Braking))
        self.assertEqual(out[0]["movingS"], 30.0)                      # one denominator, summed once
        self.assertNotIn("movingS", run(braking())[0][0])


class Faulty(Measure):
    """A log-only block that breaks on its 100th Sample."""
    name = "lane"

    def __init__(self):
        self.n = 0

    def on_sample(self, ctx, g):
        self.n += 1
        if self.n >= 100:
            raise RuntimeError("broken measure")

    def window_fields(self):
        return {"lane": {"n": self.n}}


class IsolationTests(unittest.TestCase):
    def test_a_failing_log_only_measure_leaves_the_braking_records_alone(self):
        v = speed(25.0, (5.0, 8.0, -3.0), (8.0, 14.0, 1.5), (58.0, 62.0, -2.5), (62.0, 70.0, 1.2),
                  (122.0, 125.0, -4.0))
        clean_out, clean, _ = run(v, pedal(57.0, 63.0), n=1300, measures=(SteeringTime, Braking))
        out, records, messages = run(v, pedal(57.0, 63.0), n=1300, measures=(Faulty, SteeringTime, Braking))
        self.assertEqual(len(clean), 3)
        self.assertEqual(records, clean)
        self.assertEqual([s["steeringS"] for s in out], [s["steeringS"] for s in clean_out])
        self.assertEqual([s["brakeEpisodes"] for s in out], [1, 1, 1])
        for s in out:
            self.assertEqual(s["health"]["failed"], ["lane"])
            self.assertNotIn("lane", s)


if __name__ == "__main__":
    unittest.main()
