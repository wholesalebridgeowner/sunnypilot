"""Steering-on time: how long the truck was driven, how long it was steered, how long it was held at zero."""
import random
import unittest

import measure_set
import synth
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
FIELDS = {"latOnS", "steeringS", "forcedZeroS", "fzFallbackS", "pressedS", "driverS", "laneChangeS", "movingS",
          "cruiseS", "vMean", "vMeanSteering"}
PARKED = {"carState": dict(v=0.0, gear="park"), "carControl": dict(lat=False)}


def pipeline(**config):
    return Pipeline(PipelineConfig(measures=(SteeringTime,), **config))


def run(change=None, n=300, period_ms=100.0, times=None):
    """Summaries and drive-end fields of one synthetic drive: a straight at 25 m/s with steering on,
    except where `change(i, t_ns)` says otherwise."""
    p = pipeline()
    out = synth.summaries(synth.run(p, synth.drive(times or synth.ticks(n, period_ms, T0), change)))
    return out, p.drive_end_fields()


class SumsTests(unittest.TestCase):
    def test_a_steered_straight(self):
        out, end = run()
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(FIELDS - set(s), set())
        self.assertEqual((s["latOnS"], s["steeringS"], s["movingS"]), (30.0, 30.0, 30.0))
        self.assertEqual((s["forcedZeroS"], s["fzFallbackS"], s["pressedS"], s["driverS"], s["laneChangeS"],
                          s["cruiseS"]), (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual((s["vMean"], s["vMeanSteering"]), (25.0, 25.0))
        self.assertEqual(end["totals"]["steeringS"], 30.0)
        self.assertEqual(end["totals"]["movingS"], 30.0)

    def test_steering_off_is_not_steering_time(self):
        out, _ = run(lambda i, t: {"carControl": dict(lat=False)} if i >= 100 else None)
        self.assertEqual((out[0]["latOnS"], out[0]["steeringS"]), (10.0, 10.0))
        self.assertEqual(out[0]["movingS"], 30.0)

    def test_held_at_zero_is_switched_on_but_not_steering(self):
        def change(i, t_ns):                                           # the alert is up for the first 12 s
            return {"carState": dict(v=10.0), "onroadEvents": ["belowSteerSpeed"]} if i < 120 else None
        out, end = run(change)
        s = out[0]
        self.assertEqual((s["latOnS"], s["forcedZeroS"], s["steeringS"]), (30.0, 12.0, 18.0))
        self.assertEqual(s["fzFallbackS"], 0.0)                        # decided by the alert list, not by the fallback
        self.assertEqual(end["totals"]["steeringS"], 18.0)

    def test_alert_without_steering_switched_on_counts_nothing(self):
        out, _ = run(lambda i, t: {"carControl": dict(lat=False), "onroadEvents": ["belowSteerSpeed"]})
        self.assertEqual((out[0]["latOnS"], out[0]["forcedZeroS"], out[0]["steeringS"]), (0.0, 0.0, 0.0))

    def test_missing_control_stream_is_not_steering(self):
        out, _ = run(lambda i, t: {"skip": ("carControl",)} if 100 <= i < 200 else None)
        self.assertAlmostEqual(out[0]["latOnS"], 20.5, delta=0.11)     # the last message stays fresh for 0.5 s
        self.assertEqual(out[0]["steeringS"], out[0]["latOnS"])


class FallbackTests(unittest.TestCase):
    def test_mid_drive_start_before_the_first_alert_list(self):
        out, _ = run(lambda i, t: {"skip": ("onroadEvents",)} if i < 20 else None)
        self.assertEqual(out[0]["forcedZeroS"], 0.0)
        self.assertAlmostEqual(out[0]["fzFallbackS"], 2.0, delta=0.2)
        self.assertEqual(out[0]["steeringS"], 30.0)

    def test_park_then_a_long_hole_then_slow_driving_without_alerts(self):
        parked = synth.ticks(600, 100, T0)
        times = parked + synth.ticks(300, 100, parked[-1] + 600 * SEC)
        def change(i, t_ns):
            if i < 600:
                return dict(PARKED, skip=("onroadEvents",))
            return {"carState": dict(v=10.0), "skip": ("onroadEvents",)}
        out, end = run(change, times=times)
        self.assertEqual([s["forcedZeroS"] for s in out], [0.0] * len(out))
        self.assertAlmostEqual(sum(s["fzFallbackS"] for s in out), 30.0, delta=0.3)
        self.assertAlmostEqual(end["totals"]["steeringS"], 30.0, delta=0.3)

    def test_slow_after_park_without_alerts_is_held_at_zero_by_the_fallback(self):
        def change(i, t_ns):
            if i < 50:
                return dict(PARKED, skip=("onroadEvents",))
            return {"carState": dict(v=10.0 if i < 200 else 20.0), "skip": ("onroadEvents",)}
        out, _ = run(change)
        s = out[0]
        self.assertEqual((s["latOnS"], s["forcedZeroS"], s["steeringS"]), (25.0, 15.0, 10.0))
        self.assertEqual(s["fzFallbackS"], 25.0)

    def test_fallback_time_only_counts_while_steering_is_switched_on(self):
        out, _ = run(lambda i, t: {"carControl": dict(lat=False), "skip": ("onroadEvents",)})
        self.assertEqual(out[0]["fzFallbackS"], 0.0)

    def test_alert_list_goes_missing_after_five_seconds(self):
        out, _ = run(lambda i, t: {"skip": ("onroadEvents",)} if 100 <= i < 160 else None)
        self.assertAlmostEqual(out[0]["fzFallbackS"], 1.0, delta=0.15)     # 6 s silent, trusted for 5 s


class OverlayTests(unittest.TestCase):
    def test_short_blips_are_pressed_time_but_not_driver_time(self):
        out, _ = run(lambda i, t: {"carState": dict(pressed=True)} if i % 10 == 5 else None, n=600)
        self.assertEqual(sum(s["pressedS"] for s in out), 6.0)
        self.assertEqual(sum(s["driverS"] for s in out), 0.0)
        self.assertEqual(sum(s["steeringS"] for s in out), 60.0)       # an overlay is never taken off steering time

    def test_a_real_press_is_driver_time_from_its_second_sample(self):
        out, _ = run(lambda i, t: {"carState": dict(pressed=True)} if 100 <= i < 104 else None)
        self.assertAlmostEqual(out[0]["pressedS"], 0.4, places=6)
        self.assertAlmostEqual(out[0]["driverS"], 0.3, places=6)
        self.assertEqual(out[0]["steeringS"], 30.0)

    def test_pressed_with_steering_off_is_no_overlay(self):
        out, _ = run(lambda i, t: {"carState": dict(pressed=True), "carControl": dict(lat=False)})
        self.assertEqual((out[0]["pressedS"], out[0]["driverS"]), (0.0, 0.0))

    def test_lane_change_time(self):
        states = {10: "preLaneChange", 11: "laneChangeStarting", 12: "laneChangeFinishing"}
        def change(i, t_ns):
            state = states.get(i // 10)
            return {"drivingModelData": dict(state=state)} if state else None
        out, _ = run(change)
        self.assertEqual(out[0]["laneChangeS"], 3.0)
        self.assertEqual(out[0]["steeringS"], 30.0)

    def test_lane_change_with_steering_off_or_without_the_model_stream_is_no_overlay(self):
        def off(i, t_ns):
            return {"drivingModelData": dict(state="laneChangeStarting"), "carControl": dict(lat=False)}
        self.assertEqual(run(off)[0][0]["laneChangeS"], 0.0)
        out, _ = run(lambda i, t: {"skip": ("drivingModelData",)})
        self.assertEqual(out[0]["laneChangeS"], 0.0)


class MovingTests(unittest.TestCase):
    def test_moving_starts_above_half_a_metre_per_second(self):
        speeds = {0: 0.0, 1: 0.5, 2: 0.51}
        def change(i, t_ns):
            return {"carState": dict(v=speeds[i // 100], cruise=True), "carControl": dict(lat=False)}
        out, end = run(change)
        self.assertEqual(out[0]["movingS"], 10.0)
        self.assertEqual(out[0]["cruiseS"], 10.0)                      # cruise only counts while moving
        self.assertEqual(end["totals"]["movingS"], 10.0)

    def test_cruise_time_needs_the_cruise_on(self):
        out, _ = run(lambda i, t: {"carState": dict(cruise=i < 120)})
        self.assertEqual((out[0]["movingS"], out[0]["cruiseS"]), (30.0, 12.0))

    def test_parked_window_sends_every_field(self):
        out, end = run(lambda i, t: PARKED)
        s = out[0]
        self.assertEqual(FIELDS - set(s), set())
        self.assertEqual({key: s[key] for key in FIELDS - {"vMean", "vMeanSteering"}},
                         dict.fromkeys(FIELDS - {"vMean", "vMeanSteering"}, 0.0))
        self.assertEqual(s["vMean"], 0.0)
        self.assertIsNone(s["vMeanSteering"])                          # no steering time: no mean, never a zero
        self.assertEqual(end["totals"]["steeringS"], 0.0)
        self.assertEqual(end["totals"]["movingS"], 0.0)

    def test_speed_means_are_weighted_by_time(self):
        def change(i, t_ns):                                           # 10 s at 10 m/s steering off, 20 s at 30 m/s
            return {"carState": dict(v=10.0), "carControl": dict(lat=False)} if i < 100 else {"carState": dict(v=30.0)}
        out, _ = run(change)
        self.assertEqual(out[0]["vMean"], 23.3)
        self.assertEqual(out[0]["vMeanSteering"], 30.0)

    def test_speed_mean_leaves_out_time_held_at_zero(self):
        def change(i, t_ns):
            return {"carState": dict(v=10.0), "onroadEvents": ["belowSteerSpeed"]} if i < 150 else None
        out, _ = run(change)
        self.assertEqual(out[0]["vMean"], 17.5)
        self.assertEqual(out[0]["vMeanSteering"], 25.0)


class TimeBaseTests(unittest.TestCase):
    def test_fully_engaged_minutes_at_a_slow_loop(self):
        out, end = run(n=6000, period_ms=103)
        self.assertEqual(len(out), 11)
        for s in out[:10]:
            with self.subTest(w=s["w"]):
                self.assertIn(s["n"], (582, 583))
                self.assertAlmostEqual(s["latOnS"], 60.0, delta=0.11)
                self.assertAlmostEqual(s["steeringS"], 60.0, delta=0.11)
                self.assertAlmostEqual(s["movingS"], 60.0, delta=0.11)
                self.assertEqual(s["vMean"], 25.0)
        self.assertAlmostEqual(end["totals"]["steeringS"], 6000 * 0.103, delta=0.11)

    def test_time_is_the_same_at_every_loop_period(self):
        for period_ms in (100, 104, 113, 125):
            with self.subTest(period_ms=period_ms):
                n = int(50_000 / period_ms)
                out, _ = run(lambda i, t: {"carControl": dict(lat=T0 + 10 * SEC <= t < T0 + 40 * SEC)},
                             n=n, period_ms=period_ms)
                self.assertAlmostEqual(out[0]["latOnS"], 30.0, delta=0.13)
                self.assertAlmostEqual(out[0]["movingS"], n * period_ms / 1000, delta=0.11)

    def test_a_stall_counts_as_a_quarter_second_not_as_its_length(self):
        times = synth.ticks(100, 100, T0)
        times += synth.ticks(100, 100, times[-1] + 3 * SEC)
        out, end = run(times=times)
        self.assertAlmostEqual(out[0]["latOnS"], 19.9 + 0.25, delta=0.051)     # 199 ordinary Samples and the gap Sample
        self.assertAlmostEqual(out[0]["movingS"], 19.9 + 0.25, delta=0.051)
        self.assertEqual(out[0]["unobservedS"], 2.75)
        self.assertAlmostEqual(end["totals"]["movingS"], 19.9 + 0.25, delta=0.051)

    def test_control_message_five_ms_after_car_state_at_every_phase(self):
        events = []
        for k in range(6000):                                          # 60 s at 100 Hz, steering on for 40 s of it
            t = T0 + k * 10 * MS
            events.append((t, "carState", synth.car_state()))
            events.append((t + 5 * MS, "carControl", synth.car_control(lat=1000 <= k < 5000)))
            if k % 100 == 0:
                events.append((t + 2 * MS, "onroadEvents", synth.alerts()))
        rng = random.Random(7)
        tick_times, t = [], T0 + 20 * MS
        while t < T0 + 60 * SEC:
            tick_times.append(t)
            t += 100 * MS + int(rng.uniform(0.5, 3.0) * MS)            # the tick walks through the 10 ms cycle
        p = pipeline()
        out = synth.summaries(synth.run(p, synth.tick_batches(events, tick_times)))
        self.assertGreater(len({(t // (1 * MS)) % 10 for t in tick_times}), 8)
        steering = sum(s["steeringS"] for s in out)
        self.assertGreaterEqual(steering, 0.99 * 40.0)
        self.assertLessEqual(steering, 1.01 * 40.0)
        self.assertAlmostEqual(p.drive_end_fields()["totals"]["steeringS"], steering, delta=0.11)


class WindowTests(unittest.TestCase):
    def test_each_window_has_its_own_sums_and_the_drive_has_the_total(self):
        def change(i, t_ns):                                           # steering from 50 s to 80 s
            return {"carControl": dict(lat=500 <= i < 800), "carState": dict(v=20.0 if i < 600 else 30.0)}
        out, end = run(change, n=1500)
        self.assertEqual([s["w"] for s in out], [0, 1, 2])
        self.assertEqual([s["latOnS"] for s in out], [10.0, 20.0, 0.0])
        self.assertEqual([s["steeringS"] for s in out], [10.0, 20.0, 0.0])
        self.assertEqual([s["movingS"] for s in out], [60.0, 60.0, 30.0])
        self.assertEqual([s["vMean"] for s in out], [20.0, 30.0, 30.0])
        self.assertEqual([s["vMeanSteering"] for s in out], [20.0, 30.0, None])
        self.assertEqual(end["totals"]["steeringS"], 30.0)
        self.assertEqual(end["totals"]["movingS"], 150.0)

    def test_drive_totals_are_not_sums_of_rounded_windows(self):
        # 0.14 s of steering in each of five windows: every window rounds to 0.1, the drive holds 0.7.
        times = []
        for w in range(5):
            times += [T0 + w * 60 * SEC + k * 70 * MS for k in range(3)]
        def change(i, t_ns):
            return {"carControl": dict(lat=i % 3 != 0)}
        out, end = run(change, times=times)
        self.assertEqual([s["steeringS"] for s in out], [0.1] * 5)
        self.assertEqual(end["totals"]["steeringS"], 0.7)

    def test_only_the_fields_of_this_measure(self):
        out, end = run()
        own = set(out[0]) - {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}
        self.assertEqual(own, FIELDS)
        self.assertEqual(set(end["totals"]), {"windowS", "unobservedS", "steeringS", "movingS"})
        self.assertEqual(end["failed"], [])


class RegistrationTests(unittest.TestCase):
    def test_it_is_a_measure_of_this_build_and_runs_with_the_owner_facing_ones(self):
        self.assertIn(SteeringTime, measure_set.MEASURES)
        self.assertTrue(SteeringTime.owner_facing)
        self.assertEqual(SteeringTime.name, "steering")
        self.assertIn("steering", Pipeline().measure_names)

    def test_it_needs_nothing_but_the_measure_interface_and_the_shared_rules(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(__file__).with_name("measure_steering.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, {"__future__", "measure_api", "shared_rules"})


if __name__ == "__main__":
    unittest.main()
