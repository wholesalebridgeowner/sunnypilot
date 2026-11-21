"""Steering warnings and command-at-limit time, with one record per limit event."""
import math
import unittest

import measure_set
import synth
from measure_api import Measure
from measure_limit import SteeringLimit
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
NAN = float("nan")
SECONDS = {"limitS", "limitDriverS", "limitLaneChangeS", "limitNoneS", "limitUnexpectedS", "askS", "askNoneS",
           "warnS"}
FIELDS = SECONDS | {"warnEvents", "demandMaxNone"}
RECORD = {"limitS", "vMeanMs", "vAtMaxMs", "demandMax", "achievedAtMax", "achievedMean", "driverTorqueMean",
          "epsOverCmd", "warned", "label"}
WARNING = ["steerSaturated"]


def at_limit(v=25.0, desired=0.0, actual=None, cmd=350.0, ask=1.0, driver_torque=0.0, eps=0.0, **more):
    """What differs from the plain straight while the steering command sits at its limit."""
    out = {"carState": dict(v=v, torque=driver_torque, eps=eps), "carControl": dict(torque=ask),
           "carOutput": dict(torque=cmd), "controlsState": dict(desired=desired, actual=actual)}
    out.update(more)
    return out


def run(change=None, n=300, period_ms=100.0, times=None):
    """(summaries, limit-event records, every message) of one synthetic drive."""
    p = Pipeline(PipelineConfig(measures=(SteeringLimit,)))
    messages = synth.run(p, synth.drive(times or synth.ticks(n, period_ms, T0), change))
    return synth.summaries(messages), [data for kind, data in messages if kind == "limit_event"], messages


def total(summaries, key):
    return round(sum(s[key] for s in summaries), 1)


class LimitTimeTests(unittest.TestCase):
    def test_a_plain_straight_has_no_limit_time_and_sends_every_field(self):
        out, records, _ = run()
        self.assertEqual(records, [])
        own = set(out[0]) - {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}
        self.assertEqual(own, FIELDS)
        self.assertEqual({key: out[0][key] for key in SECONDS}, dict.fromkeys(SECONDS, 0.0))
        self.assertEqual(out[0]["warnEvents"], 0)
        self.assertEqual(out[0]["demandMaxNone"], 0.0)

    def test_the_limit_is_98_percent_of_the_full_command_in_either_direction(self):
        def change(i, t_ns):
            if 100 <= i < 120:
                return at_limit(cmd=343.0)
            if 150 <= i < 170:
                return at_limit(cmd=-343.0)
            if 200 <= i < 220:
                return at_limit(cmd=342.0)
        out, _, _ = run(change)
        self.assertEqual(out[0]["limitS"], 4.0)
        self.assertEqual(out[0]["limitNoneS"], 4.0)

    def test_steering_off_counts_nothing(self):
        out, records, _ = run(lambda i, t: at_limit(carControl=dict(lat=False, torque=1.0)) if i >= 100 else None)
        self.assertEqual((out[0]["limitS"], out[0]["askS"], out[0]["askNoneS"]), (0.0, 0.0, 0.0))
        self.assertEqual(records, [])

    def test_unknown_command_adds_nothing_to_limit_time(self):
        def change(i, t_ns):
            if 100 <= i < 150:
                return at_limit(carOutput=dict(torque=NAN))
        out, records, _ = run(change)
        self.assertEqual(out[0]["limitS"], 0.0)
        self.assertEqual(out[0]["askS"], 5.0)                          # asked for is known without it
        self.assertEqual(records, [])

    def test_asking_for_the_limit_without_reaching_it_counts_alone(self):
        def change(i, t_ns):
            if 100 <= i < 120:
                return at_limit(cmd=200.0, ask=0.98)
            if 150 <= i < 170:
                return at_limit(cmd=-200.0, ask=-1.0)
            if 200 <= i < 220:
                return at_limit(cmd=200.0, ask=0.97)
            if 250 <= i < 270:
                return at_limit(cmd=200.0, ask=NAN)
        out, records, _ = run(change)
        self.assertEqual((out[0]["askS"], out[0]["askNoneS"]), (4.0, 4.0))
        self.assertEqual(out[0]["limitS"], 0.0)
        self.assertEqual(records, [])

    def test_limit_time_is_a_sum_of_time_at_every_loop_period(self):
        for period_ms in (100, 104, 113, 125):
            with self.subTest(period_ms=period_ms):
                def change(i, t_ns):
                    return at_limit() if T0 + 10 * SEC <= t_ns < T0 + 16 * SEC else None
                out, records, _ = run(change, n=int(30_000 / period_ms), period_ms=period_ms)
                self.assertAlmostEqual(out[0]["limitS"], 6.0, delta=0.13)
                self.assertAlmostEqual(out[0]["askS"], 6.0, delta=0.13)
                self.assertAlmostEqual(records[0]["limitS"], 6.0, delta=0.13)


class SplitTests(unittest.TestCase):
    def test_limit_time_after_a_press_is_driver_time_for_two_seconds(self):
        for period_ms in (100, 104):
            with self.subTest(period_ms=period_ms):
                times = synth.ticks(400, period_ms, T0)
                pressed = [t for t in times if T0 + 10 * SEC <= t <= T0 + 10 * SEC + 300 * MS]
                start = pressed[-1] + 1_050 * MS                       # 1.05 s after the last driver Sample
                flicker = [min(t for t in times if t >= start + k * 800 * MS) for k in range(5)]
                def change(i, t_ns):
                    if t_ns in pressed:
                        return {"carState": dict(pressed=True)}
                    if start <= t_ns < start + 3 * SEC:
                        return at_limit(onroadEvents=WARNING if t_ns in flicker else [])
                    return {"onroadEvents": WARNING if t_ns in flicker else []}
                out, records, _ = run(change, times=times)
                self.assertAlmostEqual(out[0]["limitS"], 3.0, delta=0.11)      # 0.1, and the field has one decimal
                self.assertAlmostEqual(out[0]["limitDriverS"], 1.0, delta=0.11)
                self.assertAlmostEqual(out[0]["limitNoneS"], 2.0, delta=0.11)
                self.assertEqual(out[0]["limitLaneChangeS"], 0.0)
                self.assertEqual(out[0]["warnEvents"], 1)
                self.assertEqual(len(records), 1)
                self.assertAlmostEqual(records[0]["limitS"], 2.0, delta=0.11)  # only the time without the driver

    def test_a_lane_change_comes_before_the_driver(self):
        def change(i, t_ns):
            if 100 <= i < 130:                                         # pressed and in a lane change
                return at_limit(carState=dict(pressed=True), drivingModelData=dict(state="laneChangeStarting"))
            if 130 <= i < 140:                                         # just after the press, no lane change
                return at_limit()
        out, records, _ = run(change)
        self.assertEqual((out[0]["limitS"], out[0]["limitLaneChangeS"], out[0]["limitDriverS"], out[0]["limitNoneS"]),
                         (4.0, 3.0, 1.0, 0.0))
        self.assertEqual((out[0]["askS"], out[0]["askNoneS"]), (4.0, 0.0))
        self.assertEqual(records, [])

    def test_every_lane_change_state_but_off_is_a_lane_change(self):
        states = {10: "preLaneChange", 11: "laneChangeStarting", 12: "laneChangeFinishing", 13: "off"}
        def change(i, t_ns):
            state = states.get(i // 10)
            return at_limit(drivingModelData=dict(state=state)) if state else None
        out, _, _ = run(change)
        self.assertEqual((out[0]["limitLaneChangeS"], out[0]["limitNoneS"]), (3.0, 1.0))

    def test_without_the_model_stream_it_is_not_a_lane_change(self):
        def change(i, t_ns):
            return at_limit(skip=("drivingModelData",)) if i >= 100 else {"skip": ("drivingModelData",)}
        out, _, _ = run(change)
        self.assertEqual((out[0]["limitS"], out[0]["limitLaneChangeS"], out[0]["limitNoneS"]), (20.0, 0.0, 20.0))

    def test_short_blips_do_not_make_it_driver_time(self):
        def change(i, t_ns):
            if i >= 100:
                return at_limit(carState=dict(pressed=i % 10 == 5))
        out, _, _ = run(change)
        self.assertEqual((out[0]["limitDriverS"], out[0]["limitNoneS"]), (0.0, 20.0))

    def test_the_parts_add_up(self):
        def change(i, t_ns):
            if 50 <= i < 250:
                return at_limit(carState=dict(pressed=100 <= i < 110),
                                drivingModelData=dict(state="laneChangeStarting" if 200 <= i < 215 else "off"))
        out, _, _ = run(change)
        s = out[0]
        self.assertEqual(s["limitS"], 20.0)
        self.assertEqual(s["limitLaneChangeS"], 1.5)
        self.assertEqual(s["limitDriverS"], 2.9)                       # 0.9 s of driver Samples and 2.0 s after them
        self.assertEqual(s["limitNoneS"], 15.6)
        self.assertEqual((s["askS"], s["askNoneS"]), (20.0, 15.6))


class WarningTests(unittest.TestCase):
    def test_a_flickering_alert_is_one_warning(self):
        out, _, _ = run(lambda i, t: {"onroadEvents": WARNING} if i in (100, 108, 116, 124, 132) else None)
        self.assertEqual(out[0]["warnEvents"], 1)
        self.assertEqual(out[0]["warnS"], 0.5)

    def test_warnings_merge_up_to_ten_seconds_apart(self):
        for later, events in ((100, 1), (101, 2)):                     # 10.0 s and 10.1 s after the first
            with self.subTest(later=later):
                out, _, _ = run(lambda i, t: {"onroadEvents": WARNING} if i in (50, 50 + later) else None)
                self.assertEqual(out[0]["warnEvents"], events)
                self.assertEqual(out[0]["warnS"], 0.2)

    def test_a_long_alert_is_one_warning(self):
        out, _, _ = run(lambda i, t: {"onroadEvents": WARNING} if 50 <= i < 200 else None)
        self.assertEqual(out[0]["warnEvents"], 1)
        self.assertEqual(out[0]["warnS"], 15.0)

    def test_a_warning_counts_whatever_the_steering_state(self):
        out, _, _ = run(lambda i, t: {"carControl": dict(lat=False), "onroadEvents": WARNING if i == 100 else []})
        self.assertEqual((out[0]["warnEvents"], out[0]["warnS"]), (1, 0.1))

    def test_other_alerts_are_not_warnings(self):
        out, _, _ = run(lambda i, t: {"onroadEvents": ["belowSteerSpeed", "steerTempUnavailable"]})
        self.assertEqual((out[0]["warnEvents"], out[0]["warnS"]), (0, 0.0))

    def test_no_warning_is_counted_while_the_alert_list_is_missing(self):
        def change(i, t_ns):                                           # the last list before 6 s of silence has the alert
            if i == 99:
                return {"onroadEvents": WARNING}
            return {"skip": ("onroadEvents",)} if 100 <= i < 160 else None
        out, _, _ = run(change)
        self.assertAlmostEqual(out[0]["warnS"], 5.1, delta=0.11)       # trusted for 5.0 s, then not seen
        self.assertEqual(out[0]["warnEvents"], 1)
        self.assertAlmostEqual(out[0]["health"]["staleS"]["ev"], 1.0, delta=0.11)

    def test_a_warning_across_a_window_boundary_counts_once(self):
        out, _, _ = run(lambda i, t: {"onroadEvents": WARNING} if 590 <= i < 610 else None, n=900)
        self.assertEqual([s["warnEvents"] for s in out], [1, 0])
        self.assertEqual([s["warnS"] for s in out], [1.0, 1.0])

    def test_the_last_warning_is_remembered_across_windows(self):
        out, _, _ = run(lambda i, t: {"onroadEvents": WARNING} if i in (595, 650, 760) else None, n=900)
        self.assertEqual([s["warnEvents"] for s in out], [1, 1])       # 5.5 s later: the same; 11 s after that: new
        self.assertEqual([s["warnS"] for s in out], [0.1, 0.2])


class LimitEventTests(unittest.TestCase):
    def test_an_event_under_0_4_seconds_gives_no_record_but_keeps_its_seconds(self):
        for samples, sent in ((3, 0), (4, 1), (5, 1)):
            with self.subTest(samples=samples):
                out, records, _ = run(lambda i, t: at_limit() if 100 <= i < 100 + samples else None)
                self.assertEqual(len(records), sent)
                self.assertEqual(out[0]["limitNoneS"], round(0.1 * samples, 1))
                if sent:
                    self.assertEqual(records[0]["limitS"], round(0.1 * samples, 1))

    def test_float_rounding_does_not_drop_an_event_of_0_4_seconds(self):
        added = 0.0
        for _ in range(8):
            added += 0.05
        self.assertLess(added, 0.4)                                    # eight Samples of 0.05 s add up just short
        _, records, _ = run(lambda i, t: at_limit() if 200 <= i < 208 else None, n=500, period_ms=50)
        self.assertEqual([r["limitS"] for r in records], [0.4])

    def test_record_of_a_steady_event(self):
        def change(i, t_ns):
            if 100 <= i < 120:
                return at_limit(v=20.0, desired=0.004, actual=0.003, driver_torque=-50.0, eps=300.0)
        _, records, _ = run(change)
        self.assertEqual(records, [{"limitS": 2.0, "vMeanMs": 20.0, "vAtMaxMs": 20.0, "demandMax": 1.6,
                                    "achievedAtMax": 1.2, "achievedMean": 1.2, "driverTorqueMean": -50,
                                    "epsOverCmd": 0.86, "warned": False, "label": "sharp"}])
        self.assertEqual(set(records[0]), RECORD)
        self.assertIs(type(records[0]["driverTorqueMean"]), int)

    def test_torques_are_signed_to_the_command(self):
        cases = ((350.0, 60.0, 280.0, 60, 0.8),                        # driver pushes with the command
                 (-350.0, 60.0, -280.0, -60, 0.8),                     # the same push against a command the other way
                 (-350.0, -60.0, -350.0, 60, 1.0))
        for cmd, driver_torque, eps, driver_mean, eps_over_cmd in cases:
            with self.subTest(cmd=cmd, driver_torque=driver_torque):
                _, records, _ = run(lambda i, t: at_limit(cmd=cmd, ask=cmd / 350.0, driver_torque=driver_torque,
                                                          eps=eps) if 100 <= i < 110 else None)
                self.assertEqual(records[0]["driverTorqueMean"], driver_mean)
                self.assertEqual(records[0]["epsOverCmd"], eps_over_cmd)

    def test_achieved_is_signed_to_the_asked_for_direction(self):
        for desired, actual, achieved in ((0.004, 0.003, 1.2), (-0.004, -0.003, 1.2), (-0.004, 0.001, -0.4)):
            with self.subTest(desired=desired, actual=actual):
                _, records, _ = run(lambda i, t: at_limit(v=20.0, desired=desired, actual=actual)
                                    if 100 <= i < 110 else None)
                self.assertEqual(records[0]["demandMax"], 1.6)
                self.assertEqual(records[0]["achievedAtMax"], achieved)
                self.assertEqual(records[0]["achievedMean"], achieved)

    def test_a_zero_is_never_sent_as_minus_zero(self):
        _, records, _ = run(lambda i, t: at_limit(v=20.0, desired=-0.004, actual=0.0, cmd=-350.0, ask=-1.0)
                            if 100 <= i < 110 else None)
        for key in ("achievedAtMax", "achievedMean", "epsOverCmd"):
            with self.subTest(key=key):
                self.assertEqual(records[0][key], 0.0)
                self.assertEqual(math.copysign(1.0, records[0][key]), 1.0)

    def test_speed_and_delivery_at_the_largest_demand_come_from_that_sample(self):
        def change(i, t_ns):
            if 100 <= i < 110:
                return at_limit(v=12.0, desired=0.005, actual=0.004)   # demand 0.72, achieved 0.576
            if i == 110:
                return at_limit(v=18.0, desired=0.008, actual=0.006)   # demand 2.592, achieved 1.944
            if 111 <= i < 115:
                return at_limit(v=18.0, desired=0.008, actual=0.007)   # the same demand again: the first one stands
        _, records, _ = run(change)
        r = records[0]
        self.assertEqual(r["limitS"], 1.5)
        self.assertEqual(r["vMeanMs"], 14.0)
        self.assertEqual(r["vAtMaxMs"], 18.0)
        self.assertEqual(r["demandMax"], 2.59)
        self.assertEqual(r["achievedAtMax"], 1.94)
        self.assertEqual(r["achievedMean"], round((0.576 * 1.0 + 1.944 * 0.1 + 2.268 * 0.4) / 1.5, 2))
        self.assertEqual(r["label"], "sharp")

    def test_samples_with_unknown_curvature_add_to_the_time_and_the_torques_only(self):
        def change(i, t_ns):
            if 100 <= i < 105:
                return at_limit(v=20.0, desired=0.002, actual=0.002, driver_torque=-40.0, eps=280.0)
            if 105 <= i < 110:
                return at_limit(v=10.0, desired=NAN, actual=NAN, driver_torque=-80.0, eps=350.0)
        out, records, _ = run(change)
        self.assertEqual(records, [{"limitS": 1.0, "vMeanMs": 20.0, "vAtMaxMs": 20.0, "demandMax": 0.8,
                                    "achievedAtMax": 0.8, "achievedMean": 0.8, "driverTorqueMean": -60,
                                    "epsOverCmd": 0.9, "warned": False, "label": "unexpected"}])
        self.assertEqual(out[0]["limitNoneS"], 1.0)
        self.assertEqual(out[0]["limitUnexpectedS"], 0.5)              # unknown demand is never "unexpected"

    def test_an_event_without_any_known_curvature_has_no_demand_and_no_label(self):
        def change(i, t_ns):
            if 100 <= i < 110:
                return at_limit(v=20.0, desired=NAN, actual=NAN, driver_torque=30.0, eps=350.0)
        out, records, _ = run(change)
        self.assertEqual(records, [{"limitS": 1.0, "vMeanMs": None, "vAtMaxMs": None, "demandMax": None,
                                    "achievedAtMax": None, "achievedMean": None, "driverTorqueMean": 30,
                                    "epsOverCmd": 1.0, "warned": False, "label": None}])
        self.assertEqual(out[0]["limitUnexpectedS"], 0.0)

    def test_means_are_weighted_by_time(self):
        times = [T0 + ms * MS for ms in list(range(0, 5000, 100)) + [5000, 5100, 5300, 5400]]
        speeds = {5000: 10.0, 5100: 10.0, 5300: 20.0}
        def change(i, t_ns):
            v = speeds.get((t_ns - T0) // MS)
            return at_limit(v=v, desired=0.001, driver_torque=-v, eps=17.5 * v) if v else None
        _, records, _ = run(change, times=times)
        self.assertEqual(records[0]["limitS"], 0.4)                    # 0.1 + 0.1 + 0.2
        self.assertEqual(records[0]["vMeanMs"], 15.0)
        self.assertEqual(records[0]["driverTorqueMean"], -15)
        self.assertEqual(records[0]["epsOverCmd"], 0.75)
        self.assertEqual(records[0]["achievedMean"], round((0.1 * 0.2 + 0.4 * 0.2) / 0.4, 2))

    def test_warned_needs_the_alert_between_the_first_and_the_last_sample(self):
        cases = ((95, False), (100, True), (105, True), (119, True), (120, False), (150, False))
        for alert_at, warned in cases:
            with self.subTest(alert_at=alert_at):
                def change(i, t_ns):
                    alerts = WARNING if i == alert_at else []
                    return at_limit(onroadEvents=alerts) if 100 <= i < 120 else {"onroadEvents": alerts}
                out, records, _ = run(change)
                self.assertEqual(len(records), 1)
                self.assertIs(records[0]["warned"], warned)
                self.assertEqual(out[0]["warnEvents"], 1)

    def test_an_alert_between_two_bursts_of_one_event_counts_as_inside(self):
        def change(i, t_ns):
            if 100 <= i < 105 or 150 <= i < 155:
                return at_limit()
            return {"onroadEvents": WARNING} if i == 130 else None
        _, records, _ = run(change)
        self.assertEqual(len(records), 1)
        self.assertIs(records[0]["warned"], True)

    def test_an_alert_after_one_event_does_not_mark_the_next(self):
        def change(i, t_ns):
            if 50 <= i < 60 or 200 <= i < 210:
                return at_limit()
            return {"onroadEvents": WARNING} if i == 70 else None
        _, records, _ = run(change)
        self.assertEqual([r["warned"] for r in records], [False, False])


class LabelTests(unittest.TestCase):
    def test_two_seconds_at_speed_with_a_modest_demand_are_unexpected(self):
        out, records, _ = run(lambda i, t: at_limit(v=20.0, desired=0.0025) if 100 <= i < 120 else None)
        self.assertEqual(out[0]["limitUnexpectedS"], 2.0)
        self.assertEqual((records[0]["demandMax"], records[0]["label"]), (1.0, "unexpected"))

    def test_slow_event(self):
        out, records, _ = run(lambda i, t: at_limit(v=10.0, desired=0.01) if 100 <= i < 120 else None)
        self.assertEqual(out[0]["limitUnexpectedS"], 0.0)
        self.assertEqual((records[0]["demandMax"], records[0]["label"]), (1.0, "slow"))

    def test_start_up_speed_is_the_line_between_slow_and_unexpected(self):
        for v, label, unexpected in ((14.5, "unexpected", 2.0), (14.4, "slow", 0.0)):
            with self.subTest(v=v):
                out, records, _ = run(lambda i, t: at_limit(v=v, desired=0.004) if 100 <= i < 120 else None)
                self.assertEqual(records[0]["label"], label)
                self.assertEqual(out[0]["limitUnexpectedS"], unexpected)

    def test_a_demand_of_exactly_1_5_is_sharp(self):
        def change(i, t_ns):                                           # 16^2 x 1.5 / 256 is 1.5 without rounding
            return at_limit(v=16.0, desired=1.5 / 256) if 100 <= i < 120 else None
        out, records, _ = run(change)
        self.assertEqual((records[0]["demandMax"], records[0]["label"]), (1.5, "sharp"))
        self.assertEqual(out[0]["limitUnexpectedS"], 0.0)

    def test_slow_goes_by_the_mean_speed_not_by_the_speed_at_the_largest_demand(self):
        def change(i, t_ns):
            if 100 <= i < 110:
                return at_limit(v=10.0, desired=0.005)                 # demand 0.5
            if 110 <= i < 112:
                return at_limit(v=16.0, desired=0.005)                 # demand 1.28, at a speed above the line
        _, records, _ = run(change)
        self.assertEqual((records[0]["vMeanMs"], records[0]["vAtMaxMs"]), (11.0, 16.0))
        self.assertEqual(records[0]["label"], "slow")

    def test_a_sharp_event_at_low_speed_is_sharp(self):
        _, records, _ = run(lambda i, t: at_limit(v=10.0, desired=0.02) if 100 <= i < 120 else None)
        self.assertEqual(records[0]["label"], "sharp")

    def test_unexpected_seconds_are_counted_per_sample_inside_a_sharp_event(self):
        def change(i, t_ns):
            if 100 <= i < 110:
                return at_limit(v=20.0, desired=0.0025)                # demand 1.0
            if 110 <= i < 120:
                return at_limit(v=20.0, desired=0.005)                 # demand 2.0
        out, records, _ = run(change)
        self.assertEqual(records[0]["label"], "sharp")
        self.assertEqual(out[0]["limitUnexpectedS"], 1.0)

    def test_unexpected_seconds_need_no_driver_and_no_lane_change(self):
        def change(i, t_ns):
            if 100 <= i < 120:
                return at_limit(v=20.0, desired=0.0025, carState=dict(v=20.0, pressed=True))
            if 200 <= i < 220:
                return at_limit(v=20.0, desired=0.0025, drivingModelData=dict(state="laneChangeFinishing"))
        out, records, _ = run(change)
        self.assertEqual(out[0]["limitS"], 4.0)
        self.assertAlmostEqual(out[0]["limitUnexpectedS"], 0.1, places=6)   # only the Sample before the press counts
        self.assertEqual(records, [])


class EventTimingTests(unittest.TestCase):
    def test_bursts_up_to_ten_seconds_apart_are_one_event(self):
        for later, limit_s in ((100, [1.0]), (101, [0.5, 0.5])):       # 10.0 s and 10.1 s after the last Sample
            with self.subTest(later=later):
                def change(i, t_ns):
                    if 50 <= i < 55 or 54 + later <= i < 59 + later:
                        return at_limit()
                _, records, _ = run(change)
                self.assertEqual([r["limitS"] for r in records], limit_s)

    def test_the_record_is_sent_once_ten_seconds_have_passed(self):
        p = Pipeline(PipelineConfig(measures=(SteeringLimit,)))
        batches = synth.drive(synth.ticks(300, 100, T0), lambda i, t: at_limit() if 100 <= i < 110 else None)
        sent_at = [i for i, b in enumerate(batches) if any(kind == "limit_event" for kind, _ in p.ingest_tick(b))]
        self.assertEqual(sent_at, [210])                               # the first Sample over 10.0 s after the last
        self.assertEqual([kind for kind, _ in p.end()], ["summary"])

    def test_an_open_event_is_sent_when_the_drive_ends(self):
        p = Pipeline(PipelineConfig(measures=(SteeringLimit,)))
        batches = synth.drive(synth.ticks(130, 100, T0), lambda i, t: at_limit() if 100 <= i < 110 else None)
        for b in batches:
            self.assertEqual(p.ingest_tick(b), [])
        out = p.end()
        self.assertEqual([kind for kind, _ in out], ["limit_event", "summary"])
        self.assertEqual(out[0][1]["limitS"], 1.0)

    def test_an_open_short_event_is_dropped_when_the_drive_ends(self):
        out, records, _ = run(lambda i, t: at_limit() if 100 <= i < 103 else None, n=130)
        self.assertEqual(records, [])
        self.assertEqual(out[0]["limitNoneS"], 0.3)

    def test_an_event_across_a_window_boundary_is_one_record(self):
        out, records, messages = run(lambda i, t: at_limit() if 580 <= i < 620 else None, n=900)
        self.assertEqual([s["limitNoneS"] for s in out], [2.0, 2.0])
        self.assertEqual([r["limitS"] for r in records], [4.0])
        self.assertEqual([kind for kind, _ in messages], ["summary", "limit_event", "summary"])

    def test_driver_time_inside_the_span_is_not_part_of_the_event(self):
        def change(i, t_ns):
            if 100 <= i < 160:                                         # 6 s at the limit with a press in the middle
                return at_limit(v=20.0, desired=0.004, driver_torque=200.0 if 120 <= i < 124 else -30.0,
                                carState=dict(v=20.0, torque=200.0 if 120 <= i < 124 else -30.0,
                                              pressed=120 <= i < 124))
        out, records, _ = run(change)
        s = out[0]
        self.assertEqual(s["limitS"], 6.0)
        self.assertEqual(s["limitDriverS"], 2.3)                       # 0.3 s of driver Samples and 2.0 s after them
        self.assertEqual(s["limitNoneS"], 3.7)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["limitS"], 3.7)
        self.assertLess(records[0]["driverTorqueMean"], 0)             # the 200-unit press is not in the event's mean

    def test_a_stall_inside_an_event_does_not_split_it(self):
        times = synth.ticks(110, 100, T0)
        times += synth.ticks(100, 100, times[-1] + 3 * SEC)
        def change(i, t_ns):
            return at_limit() if 100 <= i < 120 else None
        out, records, _ = run(change, times=times)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["limitS"], 1.0 + 0.25 + 0.9, delta=0.051)
        self.assertEqual(out[0]["health"]["gapN"], 1)


class DemandTests(unittest.TestCase):
    def test_largest_demand_without_driver_or_lane_change(self):
        def change(i, t_ns):
            return {"controlsState": dict(desired=0.002 if 100 <= i < 110 else -0.001)}
        out, _, _ = run(change)
        self.assertEqual(out[0]["demandMaxNone"], 1.25)                # 25^2 x 0.002

    def test_demand_during_a_lane_change_or_near_driver_steering_is_left_out(self):
        def change(i, t_ns):
            if 100 <= i < 110:
                return {"controlsState": dict(desired=0.004), "drivingModelData": dict(state="laneChangeStarting")}
            if 150 <= i < 154:                                         # a press: driver steering from its second Sample
                return {"controlsState": dict(desired=0.003 if i > 150 else 0.001), "carState": dict(pressed=True)}
            if 154 <= i < 174:                                         # up to 2.0 s after the last driver Sample
                return {"controlsState": dict(desired=0.003)}
            if i == 174:                                               # 2.1 s after it
                return {"controlsState": dict(desired=0.0012)}
            return {"controlsState": dict(desired=0.001)}
        out, _, _ = run(change)
        self.assertEqual(out[0]["demandMaxNone"], 0.75)

    def test_no_demand_is_known_with_steering_off_or_without_curvature(self):
        out, _, _ = run(lambda i, t: {"carControl": dict(lat=False), "controlsState": dict(desired=0.002)})
        self.assertIsNone(out[0]["demandMaxNone"])
        out, _, _ = run(lambda i, t: {"controlsState": dict(desired=NAN, actual=NAN)})
        self.assertIsNone(out[0]["demandMaxNone"])

    def test_each_window_has_its_own_largest_demand(self):
        def change(i, t_ns):
            return {"controlsState": dict(desired=0.002 if i == 300 else 0.0012),
                    "carControl": dict(lat=i < 1200)}
        out, _, _ = run(change, n=1500)
        self.assertEqual([s["demandMaxNone"] for s in out], [1.25, 0.75, None])


class RegistrationTests(unittest.TestCase):
    def test_it_is_a_measure_of_this_build_and_runs_with_the_owner_facing_ones(self):
        self.assertIn(SteeringLimit, measure_set.MEASURES)
        self.assertTrue(SteeringLimit.owner_facing)
        self.assertEqual(SteeringLimit.name, "limit")
        self.assertEqual(Pipeline().measure_names[:2], ["steering", "limit"])

    def test_it_needs_nothing_but_the_measure_interface_and_the_shared_rules(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(__file__).with_name("measure_limit.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, {"__future__", "measure_api", "shared_rules"})

    def test_both_measures_fill_one_summary_without_sharing_a_field(self):
        p = Pipeline()
        out = synth.summaries(synth.run(p, synth.drive(synth.ticks(300, 100, T0),
                                                        lambda i, t: at_limit() if 100 <= i < 120 else None)))
        self.assertEqual((out[0]["steeringS"], out[0]["limitS"], out[0]["limitNoneS"]), (30.0, 2.0, 2.0))
        self.assertEqual(p.drive_end_fields()["failed"], [])
        own = set(out[0]) - {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}
        self.assertEqual(len(own), 11 + len(FIELDS))                   # no field is sent by both


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
    def drive(self, *measures):
        def change(i, t_ns):
            if 80 <= i < 120 or 590 <= i < 620:
                return at_limit(v=20.0, desired=0.004, onroadEvents=WARNING if i in (90, 600) else [])
            return {"carControl": dict(lat=not 800 <= i < 900)}
        p = Pipeline(PipelineConfig(measures=measures))
        messages = synth.run(p, synth.drive(synth.ticks(1500, 100, T0), change))
        return messages, p.drive_end_fields()

    def test_a_failing_log_only_measure_leaves_the_owner_facing_numbers_alone(self):
        clean, clean_end = self.drive(SteeringTime, SteeringLimit)
        faulty, faulty_end = self.drive(Faulty, SteeringTime, SteeringLimit)
        strip = lambda data: {key: value for key, value in data.items() if key not in ("lane", "health")}
        self.assertEqual([(kind, strip(data)) for kind, data in faulty], [(kind, strip(data)) for kind, data in clean])
        self.assertEqual([kind for kind, _ in clean], ["limit_event", "summary", "limit_event", "summary", "summary"])
        self.assertEqual(clean_end["failed"], [])
        self.assertEqual(faulty_end["failed"], ["lane"])
        self.assertEqual(faulty_end["totals"], clean_end["totals"])
        for s in synth.summaries(faulty):
            self.assertNotIn("lane", s)                                # it broke in the first window
            self.assertEqual(s["health"]["failed"], ["lane"])
        for s in synth.summaries(clean):
            self.assertNotIn("failed", s["health"])


if __name__ == "__main__":
    unittest.main()
