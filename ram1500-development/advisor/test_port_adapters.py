"""Port-check adapters: the analysts' own gates and booking around the shipped cores (test support)."""
import ast
import math
import unittest
from pathlib import Path

import measure_set
import port_adapters as pa
import quality_values
import synth
from measure_curve import CurveCore, CurveSums, LagCore
from measure_lane_sway import LaneSway, LaneSwayCore
from measure_lat_rms import LateralRms, LateralRmsCore
from measure_swings import SwingCore, WheelSwings
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
V = 25.0


def sine(amplitude, hz):
    return lambda t_s: amplitude * math.sin(2 * math.pi * hz * t_s)


def drive(n=1200, period_ms=100.0, v=V, angle=None, demand=None, actual=None, centre=None, extra=None, times=None):
    """Batches of a straight at speed with, as asked for: a wheel angle, a cornering demand with the actual
    cornering (m/s^2), a lane centre, and whatever `extra(t_s)` changes."""
    times = times or synth.ticks(n, period_ms, T0)

    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        speed = v(t_s) if callable(v) else v
        x = demand(t_s) if demand else 0.0
        y = actual(t_s) if actual else x
        c = centre(t_s) if centre else 0.0
        out = {"carState": dict(v=speed, angle=angle(t_s) if angle else 0.0),
               "controlsState": dict(desired=x / speed ** 2, actual=y / speed ** 2),
               "drivingModelData": dict(left_y=c - 1.8, right_y=c + 1.8)}
        for stream, fields in ((extra(t_s) if extra else None) or {}).items():
            out[stream] = dict(out.get(stream) or {}, **fields)
        return out
    return synth.drive(times, change)


def run(batches, *measures):
    """Run ready-made measure objects over the batches; returns the summaries."""
    p = Pipeline(PipelineConfig(measures=tuple((lambda m=m: m) for m in measures)))
    out = synth.summaries(synth.run(p, batches))
    assert p.failed == [], p.failed
    return out


def window_of(t_ns):
    return (t_ns - T0) // (60 * SEC)


class TestTreeOnlyTests(unittest.TestCase):
    def test_no_adapter_is_a_measure_of_the_build(self):
        adapters = (pa.AnalystLaneSway, pa.AnalystSwings, pa.AnalystLateralRms, pa.AnalystCurve)
        for adapter in adapters:
            self.assertNotIn(adapter, measure_set.MEASURES)
            self.assertNotIn(adapter.name, [m.name for m in measure_set.MEASURES])
            self.assertFalse(adapter.owner_facing)

    def test_nothing_but_tests_imports_the_adapters(self):
        folder = Path(pa.__file__).parent
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("test_") or path.name == "port_adapters.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                with self.subTest(file=path.name):
                    self.assertNotIn("port_adapters", names)
            self.assertNotIn("port_adapters", path.read_text(encoding="utf-8"))

    def test_each_adapter_feeds_the_shipped_core(self):
        self.assertIs(type(pa.AnalystLaneSway().core), LaneSwayCore)
        self.assertIs(type(pa.AnalystSwings().core), SwingCore)
        self.assertIs(type(pa.AnalystLateralRms().core), LateralRmsCore)
        self.assertIs(type(pa.AnalystCurve().core), CurveCore)
        self.assertIs(type(pa.AnalystCurve().lag_core), LagCore)

    def test_the_adapters_send_nothing(self):
        adapters = (pa.AnalystLaneSway(), pa.AnalystSwings(), pa.AnalystLateralRms(), pa.AnalystCurve())
        out = run(drive(700, angle=sine(3.0, 0.25)), *adapters)
        self.assertEqual(set(out[0]), {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"})


class AnalystSwingsTests(unittest.TestCase):
    def swings(self, window_key=window_of, **more):
        adapter = pa.AnalystSwings(window_key=window_key)
        run(drive(angle=sine(3.0, 0.25), **more), adapter)
        return adapter.booked()

    def test_the_whole_drive_qualifies_from_the_first_sample_to_the_last(self):
        booked = self.swings(n=700)
        self.assertAlmostEqual(booked[0]["straightS"], 60.0, places=6)
        self.assertAlmostEqual(booked[1]["straightS"], 10.0, places=6)

    def test_it_books_by_the_time_of_the_sample_itself(self):
        # One left-right of the wheel at 56 s: in the first minute here, in the second for the shipped measure,
        # which books it when it is evaluated.
        angle = lambda t_s: 3.0 * math.sin(2 * math.pi * 0.5 * (t_s - 55.0)) if 55.0 <= t_s < 57.0 else 0.0
        adapter, shipped = pa.AnalystSwings(window_key=window_of), WheelSwings()
        out = run(drive(angle=angle), adapter, shipped)
        booked = adapter.booked()
        self.assertEqual((booked[0]["n"], booked[1]["n"]), (2, 0))
        self.assertEqual([s["swings"]["n"] for s in out], [0, 2])

    def test_a_single_pressed_sample_holds_off_for_more_than_two_seconds(self):
        pressed = lambda t_s: {"carState": dict(pressed=30.0 <= t_s < 30.1)}
        plain, held = self.swings(n=600), self.swings(n=600, extra=pressed)
        # Not qualified: the Sample at 30.0 s and those up to 32.0 s; the one at 32.1 s is 2.1 s after it.
        self.assertAlmostEqual(plain[0]["straightS"] - held[0]["straightS"], 2.1, places=6)

    def test_lane_change_and_steering_off_hold_off_the_same_way(self):
        plain = self.swings(n=600)
        for name, extra in (("lane change", lambda t_s: {"drivingModelData": dict(state="laneChangeStarting")
                                                         if 30.0 <= t_s < 31.0 else {}}),
                            ("steering off", lambda t_s: {"carControl": dict(lat=not 30.0 <= t_s < 31.0)})):
            with self.subTest(case=name):
                held = self.swings(n=600, extra=extra)
                self.assertAlmostEqual(plain[0]["straightS"] - held[0]["straightS"], 3.0, places=6)

    def test_the_loose_straight_gate_and_the_speed_floor(self):
        curve = lambda value: (lambda t_s: {"controlsState": dict(desired=value, actual=value)})
        self.assertAlmostEqual(self.swings(n=600, extra=curve(0.0019))[0]["straightS"], 60.0, places=6)
        self.assertEqual(self.swings(n=600, extra=curve(0.002)), {})
        self.assertEqual(self.swings(n=600, v=14.9), {})
        self.assertAlmostEqual(self.swings(n=600, v=15.0)[0]["straightS"], 60.0, places=6)

    def test_a_blinker_does_not_matter_to_this_analyst(self):
        blinker = lambda t_s: {"carState": dict(left=True)}
        self.assertAlmostEqual(self.swings(n=600, extra=blinker)[0]["straightS"], 60.0, places=6)

    def test_a_stall_starts_a_new_run_and_forgets_the_last_bad_sample(self):
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(300, 100, head[-1] + 300 * MS)
        pressed = lambda t_s: {"carState": dict(pressed=29.0 <= t_s < 29.95)}
        booked = self.swings(times=times, extra=pressed, window_key=lambda t_ns: 0)
        # Qualified again with the first Sample after the stall, which stands for 0.25 s.
        self.assertAlmostEqual(booked[0]["straightS"], 29.0 + 0.25 + 29.9, places=6)


class AnalystLateralRmsTests(unittest.TestCase):
    def rms(self, actual=sine(0.05, 0.2), **more):
        adapter = pa.AnalystLateralRms()
        run(drive(actual=actual, **more), adapter)
        return adapter.booked()

    def test_windows_of_sixty_seconds_from_the_first_sample_by_own_time(self):
        booked = self.rms(n=1300)
        self.assertEqual(sorted(booked), [0, 1, 2])
        self.assertAlmostEqual(booked[0]["s"], 55.0, places=6)        # nothing in the first 5.0 s
        self.assertAlmostEqual(booked[1]["s"], 60.0, places=6)
        self.assertAlmostEqual(booked[2]["s"], 10.0, places=6)        # up to the last Sample
        self.assertAlmostEqual(quality_values.lat_accel_rms(booked[1])[0], 0.035, delta=0.003)

    def test_twenty_metres_per_second(self):
        self.assertEqual(self.rms(n=700, v=19.9), {})
        self.assertAlmostEqual(self.rms(n=700, v=20.0)[0]["s"], 55.0, places=6)

    def test_a_bad_sample_drops_two_seconds_before_it_and_five_after_it(self):
        blinker = lambda t_s: {"carState": dict(left=30.0 <= t_s < 30.1)}
        booked = self.rms(n=600, extra=blinker)
        # Dropped: 28.0 s to 29.9 s (20 Samples), 30.0 s itself, and 30.1 s to 35.0 s (50 Samples).
        self.assertAlmostEqual(booked[0]["s"], 55.0 - 7.1, places=6)

    def test_the_two_seconds_before_are_only_dropped_inside_the_same_window(self):
        blinker = lambda t_s: {"carState": dict(left=60.5 <= t_s < 60.6)}
        booked = self.rms(n=1300, extra=blinker)
        self.assertAlmostEqual(booked[0]["s"], 55.0, places=6)        # 58.5 s to 59.9 s stay accepted
        self.assertAlmostEqual(booked[1]["s"], 60.0 - 0.5 - 0.1 - 5.0, places=6)

    def test_each_kind_of_bad_sample(self):
        for name, extra in (("pressed", lambda t_s: {"carState": dict(pressed=30.0 <= t_s < 30.1)}),
                            ("lane change", lambda t_s: {"drivingModelData": dict(state="preLaneChange")
                                                         if 30.0 <= t_s < 30.1 else {}}),
                            ("steering off", lambda t_s: {"carControl": dict(lat=not 30.0 <= t_s < 30.1)})):
            with self.subTest(case=name):
                self.assertAlmostEqual(self.rms(n=600, extra=extra)[0]["s"], 55.0 - 7.1, places=6)

    def test_a_steady_cornering_force_is_not_straight_road(self):
        self.assertEqual(self.rms(n=600, actual=lambda t_s: 0.31), {})
        self.assertAlmostEqual(self.rms(n=600, actual=lambda t_s: 0.29)[0]["s"], 55.0, places=6)

    def test_the_slow_cornering_force_is_smoothed_over_three_seconds(self):
        # A short pulse of 1.0 m/s^2 never lifts the smoothed value to 0.30: 0.4 s give 0.13.
        pulse = lambda t_s: 1.0 if 30.0 <= t_s < 30.4 else 0.0
        self.assertAlmostEqual(self.rms(n=600, actual=pulse)[0]["s"], 55.0, places=6)
        longer = lambda t_s: 1.0 if 30.0 <= t_s < 32.0 else 0.0
        self.assertLess(self.rms(n=600, actual=longer)[0]["s"], 55.0 - 7.0)

    def test_what_still_waits_is_handed_over_at_the_end_of_the_drive(self):
        booked = self.rms(n=100)
        self.assertEqual(booked[0]["n"], 50)                          # 5.0 s to 9.9 s, the last 2.0 s among them


class AnalystCurveTests(unittest.TestCase):
    def sums(self, **more):
        adapter = pa.AnalystCurve()
        run(drive(**more), adapter)
        curve, lag = adapter.booked(), adapter.lag_booked()
        return (curve[0] if curve else None), (lag[0] if lag else None)

    def test_usable_after_a_clean_run_of_more_than_two_seconds(self):
        curve, lag = self.sums(n=600, demand=lambda t_s: 1.0, actual=lambda t_s: 0.9)
        self.assertAlmostEqual(curve["s"], 60.0 - 2.0, places=6)      # from the 21st Sample to the last
        self.assertAlmostEqual(curve["belowSum"], 5.8, places=6)
        self.assertAlmostEqual(curve["s04"], 60.0 - 2.0 - 0.4, places=6)
        self.assertEqual(curve["events"], 1)
        self.assertAlmostEqual(lag["s"], 60.0 - 2.0 - 1.0, places=6)

    def test_a_single_pressed_sample_costs_two_seconds(self):
        pressed = lambda t_s: {"carState": dict(pressed=30.0 <= t_s < 30.1)}
        curve, lag = self.sums(n=600, demand=lambda t_s: 1.0, extra=pressed)
        self.assertAlmostEqual(curve["s"], 58.0 - 2.1, places=6)      # 30.0 s to 32.0 s are out
        self.assertAlmostEqual(lag["s"], 57.0 - 2.1 - 1.0, places=6)

    def test_lane_change_steering_off_and_missing_curvature_do_the_same(self):
        gone = synth.ns(desiredCurvature=None, curvature=None)
        for name, extra in (("lane change", lambda t_s: {"drivingModelData": dict(state="laneChangeFinishing")
                                                         if 30.0 <= t_s < 30.1 else {}}),
                            ("steering off", lambda t_s: {"carControl": dict(lat=not 30.0 <= t_s < 30.1)})):
            with self.subTest(case=name):
                curve, lag = self.sums(n=600, demand=lambda t_s: 1.0, extra=extra)
                self.assertAlmostEqual(curve["s"], 58.0 - 2.1, places=6)
        batches = drive(600, demand=lambda t_s: 1.0)
        batches[300]["controlsState"] = (batches[300]["controlsState"][0], gone)
        adapter = pa.AnalystCurve()
        run(batches, adapter)
        self.assertAlmostEqual(adapter.booked()[0]["s"], 58.0 - 2.1, places=6)

    def test_the_blinker_does_not_matter_to_this_analyst(self):
        curve, lag = self.sums(n=600, demand=lambda t_s: 1.0, extra=lambda t_s: {"carState": dict(left=True)})
        self.assertAlmostEqual(curve["s"], 58.0, places=6)

    def test_speed_floor_and_low_speed_line(self):
        curve, lag = self.sums(n=600, v=10.0, demand=lambda t_s: 1.0, actual=lambda t_s: 0.7)
        self.assertEqual((curve["s"], curve["s04"]), (0.0, 0.0))
        self.assertAlmostEqual(curve["lowS"], 58.0, places=6)
        self.assertAlmostEqual(curve["lowBelowSum"], 58.0 * 0.3, places=6)
        self.assertIsNone(lag)

    def test_a_stall_is_a_bad_sample_and_drops_the_history(self):
        head = synth.ticks(300, 100, T0)
        times = head + synth.ticks(300, 100, head[-1] + 300 * MS)
        curve, lag = self.sums(times=times, demand=lambda t_s: 1.0)
        # 2.0 s to 29.9 s, then from 2.1 s after the first Sample behind the stall (30.2 s) to the end (60.1 s).
        self.assertAlmostEqual(curve["s"], 28.0 + 27.9, places=6)
        self.assertEqual(curve["events"], 1)                          # 2.4 s without a curve Sample: the same curve
        self.assertAlmostEqual(curve["s04"], 28.0 - 0.4 + 27.9 - 0.4, places=6)
        # The earlier demands are dropped at the stall: both cores hold the first Sample behind it and no more.
        adapter = pa.AnalystCurve()
        p = Pipeline(PipelineConfig(measures=(lambda: adapter,)))
        for b in drive(times=head + [head[-1] + 300 * MS], demand=lambda t_s: 1.0):
            p.ingest_tick(b)
        self.assertEqual((adapter.core.held, adapter.lag_core.held), (1, 1))

    def test_against_the_shipped_gates_on_the_same_drive(self):
        adapter, shipped = pa.AnalystCurve(), CurveSums()
        out = run(drive(600, demand=lambda t_s: 1.0, actual=lambda t_s: 0.9), adapter, shipped)
        pooled = quality_values.pool("curve", [s.get("curve") for s in out])
        self.assertAlmostEqual(pooled["s"], 60.0 - 5.1 - 2.0, places=6)           # shared gates: 5.1 s to 57.9 s
        self.assertAlmostEqual(adapter.booked()[0]["s"], 58.0, places=6)
        self.assertAlmostEqual(quality_values.curve_tracking(pooled)["below"], 0.10, delta=0.0005)


class AnalystLaneSwayTests(unittest.TestCase):
    def lane(self, window_key=lambda t_ns: 0, **more):
        adapter = pa.AnalystLaneSway(window_key=window_key)
        run(drive(centre=sine(0.05, 0.1), **more), adapter)
        return adapter.booked()

    def test_accepted_from_the_first_message(self):
        block = self.lane(n=700)[0]
        self.assertAlmostEqual(block["eligibleS"], 69.9, places=6)    # the first message stands for no time
        self.assertAlmostEqual(block["validS"], 69.9, places=6)
        # Scored: all but the first and the last 3.0 s, where the road is not known to be straight.
        self.assertEqual(block["n"], 700 - 30 - 30)
        self.assertAlmostEqual(quality_values.lane_sway_sd(block), 0.036, delta=0.004)

    def test_it_books_by_the_minute_of_the_message_itself(self):
        booked = self.lane(n=1300, window_key=pa.minute_of)
        # The drive starts 1,000 s after boot: 20 s into minute 16.
        self.assertEqual(sorted(booked), [16, 17, 18])
        self.assertAlmostEqual(booked[16]["eligibleS"], 19.9, places=6)
        self.assertAlmostEqual(booked[17]["eligibleS"], 60.0, places=6)
        self.assertEqual(booked[17]["n"], 600)
        shipped = LaneSway()
        out = run(drive(1300, centre=sine(0.05, 0.1)), shipped)
        self.assertEqual(out[0]["lane"]["n"], 433)                    # the shipped measure books 8.6 s late

    def test_five_seconds_after_a_lane_change(self):
        lane_change = lambda t_s: {"drivingModelData": dict(state="laneChangeStarting") if 30.0 <= t_s < 31.0 else {}}
        plain, held = self.lane(n=700)[0], self.lane(n=700, extra=lane_change)[0]
        # Not accepted: the ten messages of the lane change and every message up to 5.0 s after the last of
        # them (30.9 s): 30.0 s to 35.9 s.
        self.assertAlmostEqual(plain["eligibleS"] - held["eligibleS"], 6.0, places=6)

    def test_three_seconds_after_a_single_pressed_sample_or_a_blinker(self):
        plain = self.lane(n=700)[0]
        for name, extra in (("pressed", lambda t_s: {"carState": dict(pressed=30.0 <= t_s < 30.1)}),
                            ("blinker", lambda t_s: {"carState": dict(right=30.0 <= t_s < 30.1)})):
            with self.subTest(case=name):
                held = self.lane(n=700, extra=extra)[0]
                self.assertAlmostEqual(plain["eligibleS"] - held["eligibleS"], 3.1, places=6)     # 30.0 s to 33.0 s

    def test_steering_on_and_fifteen_metres_per_second(self):
        self.assertEqual(self.lane(n=700, v=14.9), {})
        self.assertEqual(self.lane(n=700, extra=lambda t_s: {"carControl": dict(lat=False)}), {})
        self.assertAlmostEqual(self.lane(n=700, v=15.0)[0]["eligibleS"], 69.9, places=6)

    def test_only_messages_on_straight_road_are_scored(self):
        block = self.lane(n=700, extra=lambda t_s: {"controlsState": dict(desired=0.00025, actual=0.00025)})[0]
        self.assertEqual(block["n"], 0)
        self.assertAlmostEqual(block["validS"], 69.9, places=6)


if __name__ == "__main__":
    unittest.main()
