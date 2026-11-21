"""Lane changes: counted by how they ended, with one record of how each one went."""
import unittest

import measure_set
import synth
from measure_api import Measure
from measure_lane_change import LaneChanges
from measure_steering import SteeringTime
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
NAN = float("nan")
COUNTS = ("lcStarts", "lcCompleted", "lcCancelled", "lcBlinkerOnly", "lcHandsOn", "lcTruncated")
RECORD = {"outcome", "dir", "handsOn", "pressedShare", "pushTorqueMax", "vStartMs", "crossed", "crossS", "startingS",
          "peakLatAccel", "settleState", "settleS", "overshootM"}
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"}
NONE = dict.fromkeys(COUNTS, 0)


def ms(seconds):
    return int(round(seconds * 1000))


class LC:
    """One scripted lane change. Times are seconds from the start of the drive.

    The model reports preLaneChange for `pre` seconds, laneChangeStarting from `start` for `starting` seconds and
    laneChangeFinishing for `finishing` seconds. The lane centre, seen from the truck and counted positive toward
    the new lane, moves out to half of `jump`, flips by `jump` at `cross` seconds after the start (the lines are
    then those of the new lane), comes back to zero over `recover` seconds and may then swing past by `overshoot`.
    `cross=None`: the truck drifts a seventh of `jump` toward the line and back without crossing it.
    """

    def __init__(self, start=20.0, pre=1.0, starting=3.5, finishing=1.0, direction="left", cross=3.0, jump=3.5,
                 recover=3.0, overshoot=0.0):
        self.t0 = ms(start)
        self.pre0 = self.t0 - ms(pre)
        self.fin0 = self.t0 + ms(starting)
        self.end = self.fin0 + ms(finishing)
        self.direction = direction
        self.cross = None if cross is None else ms(cross)
        self.jump, self.recover, self.overshoot = jump, ms(recover), overshoot

    def state(self, t):
        if self.pre0 <= t < self.t0:
            return "preLaneChange"
        if self.t0 <= t < self.fin0:
            return "laneChangeStarting"
        if self.fin0 <= t < self.end:
            return "laneChangeFinishing"
        return None

    def x(self, t):
        u = t - self.t0
        half = self.jump / 2
        if u < 0:
            return 0.0
        if self.cross is None:
            return self.jump / 7 * max(0, min(u, 2000 - u)) / 1000
        if u < self.cross:
            return half * min(1.0, u / max(self.cross - 500, 1))
        u -= self.cross
        if u < self.recover:
            return -half * (1 - u / self.recover)
        return self.overshoot * max(0.0, 1 - abs(u - self.recover - 1000) / 1000)


def model_of(changes, t):
    """The model message at `t` ms: state and direction of the lane change under way, lines of the latest one."""
    state, direction, centre = "off", "none", 0.0
    for c in changes:
        if c.state(t):
            state, direction = c.state(t), c.direction
        if c.t0 <= t:
            centre = c.x(t) * (1 if c.direction == "left" else -1)
    return dict(state=state, direction=direction, left_y=centre - 2.0, right_y=centre + 2.0)


class Run:
    """One synthetic drive through the pipeline: a straight at 25 m/s with steering on and a 10 Hz model stream.

    changes   the scripted lane changes
    car       car(t) -> carState values that differ at `t` ms
    lat       lat(t) -> steering switched on
    accel     accel(t) -> lateral acceleration (speed^2 x actual curvature), m/s^2
    model     model(t) -> model values that differ (state, direction, left_y, right_y, left_prob, right_prob)
    no_model  no_model(t) -> True where the model message is left out
    slow      a 2 Hz model stream with the limits of a decimated log
    model_early_ms  the model message of a tick is stamped this much before its carState
    """

    def __init__(self, changes=(), car=None, lat=None, accel=None, model=None, no_model=None, slow=False, n=450,
                 period_ms=100, measures=(LaneChanges,), model_early_ms=0):
        if isinstance(changes, LC):
            changes = (changes,)

        def change(i, t_ns):
            t = (t_ns - T0) // MS
            message = model_of(changes, t)
            message.update((model(t) if model else None) or {})
            out = {"drivingModelData": message, "carState": dict(v=25.0)}
            out["carState"].update((car(t) if car else None) or {})
            if (no_model and no_model(t)) or (slow and t % 500):
                out["skip"] = ("drivingModelData",)
            if lat:
                out["carControl"] = dict(lat=bool(lat(t)))
            if accel:
                out["controlsState"] = dict(actual=accel(t) / out["carState"]["v"] ** 2)
            return out

        config = PipelineConfig.for_decimated_log(measures=measures) if slow else PipelineConfig(measures=measures)
        self.pipeline = Pipeline(config)
        self.messages, self.sent_at = [], []
        for i, b in enumerate(synth.drive(synth.ticks(n, period_ms, T0), change)):
            if model_early_ms and "drivingModelData" in b:
                t_ns, message = b["drivingModelData"]
                b["drivingModelData"] = (t_ns - model_early_ms * MS, message)
            for message in self.pipeline.ingest_tick(b):
                self.messages.append(message)
                if message[0] == "lane_change":
                    self.sent_at.append(i)
        self.messages.extend(self.pipeline.end())
        self.summaries = synth.summaries(self.messages)
        self.records = [data for kind, data in self.messages if kind == "lane_change"]
        self.end = self.pipeline.drive_end_fields()
        self.counts = {key: sum(s[key] for s in self.summaries) for key in COUNTS}

    @property
    def record(self):
        assert len(self.records) == 1, self.records
        return self.records[0]


def counts(**given):
    return dict(NONE, **{"lc" + key[0].upper() + key[1:]: value for key, value in given.items()})


def between(a, b):
    """t -> True from `a` to just before `b` seconds."""
    return lambda t: ms(a) <= t < ms(b)


PUSH = lambda t: {"torque": 140.0} if ms(19.5) <= t < ms(20.1) else None


class CountTests(unittest.TestCase):
    def test_a_drive_without_lane_changes_sends_every_count_as_zero(self):
        r = Run()
        self.assertEqual(r.records, [])
        self.assertEqual(set(r.summaries[0]) - PIPELINE_FIELDS, set(COUNTS))
        self.assertEqual(r.counts, NONE)
        self.assertEqual(r.end["truncatedLaneChanges"], 0)

    def test_a_completed_lane_change(self):
        r = Run(LC(), car=PUSH, accel=lambda t: 0.2 + (0.8 if ms(21) <= t < ms(22) else 0.0))
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        self.assertEqual(set(r.record), RECORD)
        self.assertEqual(r.record, {
            "outcome": "completed", "dir": "L", "handsOn": False, "pressedShare": 0.0, "pushTorqueMax": 140,
            "vStartMs": 25.0, "crossed": True, "crossS": 3.0, "startingS": 3.5, "peakLatAccel": 0.8,
            "settleState": "settled", "settleS": 2.6, "overshootM": 0.0})

    def test_a_lane_change_to_the_right(self):
        r = Run(LC(direction="right"), car=lambda t: {"torque": -140.0} if ms(19.5) <= t < ms(20.1) else None)
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        self.assertEqual((r.record["dir"], r.record["crossed"], r.record["crossS"], r.record["pushTorqueMax"]),
                         ("R", True, 3.0, 140))
        self.assertEqual((r.record["settleState"], r.record["settleS"]), ("settled", 2.6))

    def test_blinker_without_a_lane_change(self):
        r = Run(LC(pre=2.0, starting=0.0, finishing=0.0))
        self.assertEqual(r.counts, counts(blinkerOnly=1))
        self.assertEqual(r.records, [])

    def test_a_start_that_ends_with_the_brake_is_cancelled(self):
        # The driver brakes 1.5 s in: steering goes off and the build puts the lane-change state back to off.
        r = Run(LC(starting=1.5, finishing=0.0, cross=None), car=lambda t: {"brake": t >= ms(21.5)},
                lat=lambda t: t < ms(21.5))
        self.assertEqual(r.counts, counts(starts=1, cancelled=1))
        self.assertEqual(r.record, {
            "outcome": "cancelled", "dir": "L", "handsOn": False, "pressedShare": 0.0, "pushTorqueMax": 0,
            "vStartMs": 25.0, "crossed": False, "crossS": None, "startingS": None, "peakLatAccel": None,
            "settleState": "no_crossing", "settleS": None, "overshootM": None})

    def test_a_start_that_falls_back_to_the_blinker_state_is_cancelled(self):
        states = lambda t: {"state": "preLaneChange", "direction": "left"} if ms(21.5) <= t < ms(23) else None
        r = Run(LC(starting=1.5, finishing=0.0, cross=None), model=states)
        self.assertEqual(r.counts, counts(starts=1, cancelled=1))      # and the blinker stretch after it is no episode
        self.assertEqual(r.sent_at, [246])                             # it ended at 21.5 s, not with the blinker

    def test_finishing_must_last_0_7_seconds_to_count_as_completed(self):
        for finishing, outcome in ((0.70, "completed"), (0.65, "cancelled")):
            with self.subTest(finishing=finishing):
                r = Run(LC(finishing=finishing), period_ms=50, n=900)
                self.assertEqual(r.record["outcome"], outcome)
                self.assertEqual(r.counts, counts(starts=1, **{outcome: 1}))
        self.assertEqual(r.record["startingS"], 3.5)                   # finishing was reached all the same

    def test_a_one_second_finishing_phase_seen_as_0_9_seconds_at_a_slow_loop_is_completed(self):
        # A 20 Hz model stream and 100 Hz car messages, seen through ticks 0.104 s apart at every phase.
        events, seen, c = [], [], LC()
        for t in range(0, 40_000, 10):
            t_ns = T0 + t * MS
            events.append((t_ns, "carState", synth.car_state()))
            events.append((t_ns + 5 * MS, "carControl", synth.car_control()))
            if t % 50 == 0:
                events.append((t_ns + 2 * MS, "drivingModelData", synth.model(**model_of((c,), t))))
        for phase_ms in range(0, 104, 4):
            ticks = [T0 + phase_ms * MS + k * 104 * MS for k in range(380)]
            p = Pipeline(PipelineConfig(measures=(LaneChanges,)))
            batches = synth.tick_batches(events, ticks)
            messages = synth.run(p, batches)
            models = [b["drivingModelData"] for b in batches if "drivingModelData" in b]
            finishing = [t for t, m in models if m.meta.laneChangeState == "laneChangeFinishing"]
            after = min(t for t, m in models if t > finishing[-1])
            seen.append(round((after - finishing[0]) / SEC, 2))
            with self.subTest(phase_ms=phase_ms):
                records = [data for kind, data in messages if kind == "lane_change"]
                self.assertEqual([r["outcome"] for r in records], ["completed"])
                self.assertEqual(sum(s["lcCompleted"] for s in synth.summaries(messages)), 1)
        self.assertEqual(min(seen), 0.9)                               # the set-up does what it says
        self.assertGreater(max(seen), 1.0)

    def test_a_double_lane_change_is_two_starts_and_two_completed(self):
        # Finishing is followed directly by starting: the blinker stayed on and the next push came at once.
        r = Run((LC(), LC(start=24.5, pre=0.0)), n=600)
        self.assertEqual(r.counts, counts(starts=2, completed=2))
        self.assertEqual([x["outcome"] for x in r.records], ["completed", "completed"])
        self.assertEqual([x["startingS"] for x in r.records], [3.5, 3.5])
        self.assertEqual([x["crossS"] for x in r.records], [3.0, 3.0])

    def test_the_blinker_still_on_after_a_lane_change_is_not_a_blinker_only(self):
        still_on = lambda t: {"state": "preLaneChange", "direction": "left"} if ms(24.5) <= t < ms(26) else None
        r = Run(LC(), model=still_on)
        self.assertEqual(r.counts, counts(starts=1, completed=1))

    def test_a_second_lane_change_from_the_blinker_still_on_is_a_new_attempt(self):
        r = Run((LC(), LC(start=26.0, pre=1.5)), n=600)                # blinker state from 24.5 s, right after finishing
        self.assertEqual(r.counts, counts(starts=2, completed=2))

    def test_a_start_straight_from_off_counts(self):
        r = Run(LC(pre=0.0))
        self.assertEqual(r.counts, counts(starts=1, completed=1))

    def test_a_lane_change_under_way_when_the_data_starts_is_not_an_attempt(self):
        r = Run(LC(start=-1.0))                                        # the first message already says starting
        self.assertEqual(r.counts, NONE)
        self.assertEqual(r.records, [])
        self.assertEqual(r.end["truncatedLaneChanges"], 0)

    def test_the_blinker_state_at_the_start_of_the_data_is_not_a_blinker_only(self):
        r = Run(LC(start=2.0, pre=5.0, starting=0.0, finishing=0.0))   # no off was seen before it
        self.assertEqual(r.counts, NONE)

    def test_after_the_start_of_the_data_the_next_lane_change_counts(self):
        r = Run((LC(start=-1.0), LC(start=20.0)))
        self.assertEqual(r.counts, counts(starts=1, completed=1))

    def test_the_direction_is_the_one_reported_most_while_starting(self):
        flicker = lambda t: {"direction": "right"} if ms(20) <= t < ms(21) else None      # 10 of 35 messages
        r = Run(LC(), model=flicker)
        self.assertEqual((r.record["dir"], r.record["crossed"]), ("L", True))
        mostly = lambda t: {"direction": "left"} if ms(20) <= t < ms(21) else None        # 25 of 35 say right
        r = Run(LC(direction="right"), model=mostly)
        self.assertEqual((r.record["dir"], r.record["crossed"]), ("R", True))

    def test_the_direction_is_taken_from_the_starting_phase_only(self):
        other = lambda t: {"direction": "right"} if t < ms(20) or t >= ms(23.5) else None
        self.assertEqual(Run(LC(pre=5.0, finishing=5.0), model=other).record["dir"], "L")

    def test_an_even_split_reads_as_left(self):
        half = lambda t: {"direction": "right"} if ms(20) <= t < ms(21.8) else None       # 18 of 36 messages
        self.assertEqual(Run(LC(starting=3.6), model=half).record["dir"], "L")

    def test_without_a_reported_direction_nothing_that_needs_it_is_given(self):
        r = Run(LC(), car=PUSH, model=lambda t: {"direction": "none"})
        self.assertEqual(r.counts, counts(starts=1, completed=1))      # the count needs no direction
        self.assertEqual((r.record["dir"], r.record["pushTorqueMax"], r.record["crossed"], r.record["crossS"]),
                         (None, None, False, None))
        self.assertEqual((r.record["settleState"], r.record["peakLatAccel"]), ("no_crossing", None))

    def test_the_speed_at_the_start(self):
        r = Run(LC(), car=lambda t: {"v": 20.0 + t / 8_000})
        self.assertEqual(r.record["vStartMs"], 22.5)


class PushTests(unittest.TestCase):
    def push(self, a, b, torque=140.0, **more):
        return Run(LC(**more), car=lambda t: {"torque": torque} if ms(a) <= t < ms(b) else None).record

    def test_the_push_is_looked_for_from_one_second_before_to_0_2_seconds_after_the_start(self):
        self.assertEqual(self.push(19.0, 19.1)["pushTorqueMax"], 140)
        self.assertEqual(self.push(20.2, 20.3)["pushTorqueMax"], 140)
        self.assertEqual(self.push(18.9, 19.0)["pushTorqueMax"], 0)
        self.assertEqual(self.push(20.3, 20.4)["pushTorqueMax"], 0)

    def test_torque_away_from_the_new_lane_is_no_push(self):
        self.assertEqual(self.push(19.0, 20.3, torque=-90.0)["pushTorqueMax"], -90)
        self.assertEqual(self.push(19.0, 20.3, torque=-90.0, direction="right")["pushTorqueMax"], 90)

    def test_it_is_a_whole_number(self):
        record = self.push(19.5, 20.0, torque=131.6)
        self.assertEqual(record["pushTorqueMax"], 132)
        self.assertIsInstance(record["pushTorqueMax"], int)


class GapTests(unittest.TestCase):
    """Two new model messages more than 0.35 s apart: what was open cannot be followed to the end."""

    def test_a_gap_while_starting_truncates_the_attempt(self):
        r = Run(LC(), no_model=between(21.0, 21.3))                    # messages at 20.9 s and 21.3 s
        self.assertEqual(r.counts, counts(truncated=1))                # and the rest of it is not a second attempt
        self.assertEqual(r.records, [])
        self.assertEqual(r.end["truncatedLaneChanges"], 1)
        self.assertEqual(sum(s["health"]["modelGapN"] for s in r.summaries), 1)

    def test_a_gap_while_finishing_truncates_the_attempt(self):
        r = Run(LC(), no_model=between(23.8, 24.1))
        self.assertEqual(r.counts, counts(truncated=1))
        self.assertEqual(r.records, [])

    def test_a_spacing_of_0_3_seconds_is_no_gap(self):
        r = Run(LC(), no_model=between(21.0, 21.2))
        self.assertEqual(r.counts, counts(starts=1, completed=1))

    def test_after_a_gap_no_attempt_opens_until_off_or_the_blinker_state_is_seen(self):
        # The gap is followed by finishing and then, directly, by a second starting phase.
        r = Run((LC(), LC(start=24.5, pre=0.0)), no_model=between(21.0, 21.3), n=600)
        self.assertEqual(r.counts, counts(truncated=1))
        self.assertEqual(r.records, [])

    def test_the_lane_change_after_a_truncated_one_counts_again(self):
        r = Run((LC(), LC(start=30.0)), no_model=between(21.0, 21.3), n=600)
        self.assertEqual(r.counts, counts(starts=1, completed=1, truncated=1))
        self.assertEqual(len(r.records), 1)

    def test_after_a_gap_the_blinker_state_is_enough_to_open_the_next_attempt(self):
        r = Run(LC(pre=1.0), no_model=between(18.7, 19.0))             # gap, blinker state from 19.0 s, start at 20.0 s
        self.assertEqual(r.counts, counts(starts=1, completed=1))

    def test_a_gap_makes_a_pending_blinker_state_forgotten(self):
        r = Run(LC(pre=2.0, starting=0.0, finishing=0.0), no_model=between(19.0, 19.3))
        self.assertEqual(r.counts, NONE)

    def test_a_gap_right_before_the_blinker_state_hides_where_it_came_from(self):
        r = Run(LC(pre=2.0, starting=0.0, finishing=0.0), no_model=between(17.7, 18.0))
        self.assertEqual(r.counts, NONE)                               # it may have followed a lane change

    def test_a_gap_outside_any_lane_change_changes_no_count(self):
        r = Run(LC(), no_model=between(10.0, 12.0))
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        self.assertEqual(r.record["settleState"], "settled")

    def test_a_message_without_a_readable_state_breaks_the_stream_like_a_gap(self):
        r = Run(LC(), model=lambda t: {"state": "somethingNew"} if t == ms(21) else None)
        self.assertEqual(r.counts, counts(truncated=1))
        self.assertEqual(r.records, [])

    def test_a_message_without_a_readable_state_spoils_a_settle_window_like_a_gap(self):
        r = Run(LC(), model=lambda t: {"state": "somethingNew"} if t == ms(27) else None)
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        self.assertEqual(r.record["settleState"], "not_measurable:data")

    def test_the_truncation_is_counted_where_the_gap_shows(self):
        r = Run(LC(start=57.0), no_model=between(59.5, 60.5), n=900)   # the next message comes in the second window
        self.assertEqual([s["lcTruncated"] for s in r.summaries], [0, 1])
        self.assertEqual(r.end["truncatedLaneChanges"], 1)

    def test_a_model_stream_that_stays_silent_truncates_the_attempt_without_waiting_for_the_end(self):
        r = Run(LC(start=50.0), no_model=lambda t: t >= ms(52), n=1900)
        self.assertEqual([s["lcTruncated"] for s in r.summaries], [1, 0, 0, 0])     # not held back to the drive end
        self.assertEqual(r.counts, counts(truncated=1))
        self.assertEqual(r.end["truncatedLaneChanges"], 1)

    def test_nothing_piles_up_while_the_model_stream_is_silent(self):
        p = Pipeline(PipelineConfig(measures=(LaneChanges,)))
        measure = p._measures[0]
        c = LC(start=20.0)
        for i, t_ns in enumerate(synth.ticks(3000, 100, T0)):
            t = (t_ns - T0) // MS
            skip = ("drivingModelData",) if t >= ms(22) else ()
            p.ingest_tick(synth.batch(t_ns, skip=skip, drivingModelData=model_of((c,), t)))
        self.assertLessEqual(measure.buffered, 80)                     # 3.5 s of Samples and messages, not 280 s

    def test_the_history_is_let_go_once_the_record_is_sent(self):
        p = Pipeline(PipelineConfig(measures=(LaneChanges,)))
        measure = p._measures[0]
        c = LC(start=20.0)
        most = 0
        for t_ns in synth.ticks(1000, 100, T0):
            p.ingest_tick(synth.batch(t_ns, drivingModelData=model_of((c,), (t_ns - T0) // MS)))
            most = max(most, measure.buffered)
        self.assertLessEqual(measure.buffered, 80)
        self.assertLessEqual(most, 2 * 160)                            # from 3.5 s before the start to the record


class HandsOnTests(unittest.TestCase):
    """Share of the Samples from 1.0 s after the start to the end with the wheel sensor reading pressed."""

    def run_pressed(self, a, b, **more):
        return Run(LC(**more), car=lambda t: {"pressed": ms(a) <= t < ms(b)})

    def test_a_pressed_share_of_a_fifth_is_hands_on(self):
        r = self.run_pressed(22.0, 22.8)                               # 8 of the 36 Samples from 21.0 to 24.5 s
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.22, True))
        self.assertEqual(r.counts, counts(starts=1, completed=1, handsOn=1))

    def test_the_share_must_be_over_a_tenth(self):
        # Starting for 3.9 s: 40 Samples from 21.0 to 24.9 s.
        r = self.run_pressed(22.0, 22.4, starting=3.9)
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.1, False))
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        r = self.run_pressed(22.0, 22.5, starting=3.9)
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.12, True))
        r = self.run_pressed(22.0, 22.4)                               # 4 of 36
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.11, True))

    def test_the_push_in_the_first_second_does_not_count(self):
        r = self.run_pressed(19.0, 21.0)
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.0, False))

    def test_pressed_after_the_end_does_not_count(self):
        r = self.run_pressed(24.6, 30.0)
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.0, False))

    def test_a_sample_newer_than_the_message_that_ends_the_attempt_does_not_count(self):
        # Each model message is stamped 30 ms before the carState it arrives with. The attempt ends with the
        # message stamped 24.47 s; the Sample at 24.5 s is already past its end.
        r = Run(LC(), car=lambda t: {"pressed": t >= ms(24.5)}, model_early_ms=30)
        self.assertEqual((r.record["outcome"], r.record["pressedShare"]), ("completed", 0.0))

    def test_the_first_and_the_last_sample_of_the_span_count(self):
        r = Run(LC(), car=lambda t: {"pressed": t in (ms(21.0), ms(24.5))})
        self.assertEqual(r.record["pressedShare"], 0.06)               # 2 of 36

    def test_a_cancelled_attempt_is_recorded_as_hands_on_but_not_counted_as_one(self):
        r = Run(LC(starting=3.0, finishing=0.0, cross=None), car=lambda t: {"pressed": t >= ms(21)})
        self.assertEqual((r.record["outcome"], r.record["handsOn"], r.record["pressedShare"]), ("cancelled", True, 1.0))
        self.assertEqual(r.counts, counts(starts=1, cancelled=1))

    def test_an_attempt_shorter_than_a_second_has_no_pressed_share(self):
        r = Run(LC(starting=0.8, finishing=0.0, cross=None), car=lambda t: {"pressed": True})
        self.assertEqual((r.record["pressedShare"], r.record["handsOn"]), (0.0, False))


class CrossingTests(unittest.TestCase):
    def test_a_jump_of_1_2_metres_is_a_crossing(self):
        for jump, crossed in ((1.25, True), (1.15, False)):
            with self.subTest(jump=jump):
                r = Run(LC(jump=jump))
                self.assertEqual((r.record["crossed"], r.record["crossS"]), (crossed, 3.0 if crossed else None))
                self.assertEqual(r.record["settleState"] == "no_crossing", not crossed)
                self.assertEqual(r.counts, counts(starts=1, completed=1))      # the count does not need the crossing

    def test_the_crossing_is_looked_for_until_two_seconds_after_the_end(self):
        self.assertEqual(Run(LC(cross=6.5)).record["crossS"], 6.5)     # the end is at 24.5 s
        self.assertEqual(Run(LC(cross=6.6)).record["crossed"], False)

    def test_a_slow_drift_is_no_crossing(self):
        # The centre moves 1.5 m in 1.5 s: never more than 1.0 m within one second.
        drift = lambda t: {"left_y": -2.0 - max(0.0, min(1.5, (t - ms(22)) / 1000)),
                           "right_y": 2.0 - max(0.0, min(1.5, (t - ms(22)) / 1000))}
        r = Run(LC(cross=None, jump=0.0), model=drift)
        self.assertEqual(r.record["crossed"], False)

    def test_the_jump_is_measured_against_the_highest_point_of_the_last_second(self):
        # The centre steps down by 0.7 m twice, 0.9 s apart: 1.4 m within one second.
        step = lambda t: {"left_y": -2.0 - 0.7 * ((t >= ms(22)) + (t >= ms(22.9))),
                          "right_y": 2.0 - 0.7 * ((t >= ms(22)) + (t >= ms(22.9)))}
        r = Run(LC(cross=None, jump=0.0), model=step)
        self.assertEqual((r.record["crossed"], r.record["crossS"]), (True, 2.9))
        apart = lambda t: {"left_y": -2.0 - 0.7 * ((t >= ms(22)) + (t >= ms(23.1))),
                           "right_y": 2.0 - 0.7 * ((t >= ms(22)) + (t >= ms(23.1)))}
        self.assertEqual(Run(LC(cross=None, jump=0.0), model=apart).record["crossed"], False)

    def test_the_position_before_the_start_counts_for_the_first_second(self):
        # The lines flip 0.3 s after the start; the high point lies before the start.
        r = Run(LC(cross=None, jump=0.0), model=lambda t: {"left_y": -1.0, "right_y": 3.0} if t < ms(20.3) else
                {"left_y": -3.0, "right_y": 1.0})
        self.assertEqual((r.record["crossed"], r.record["crossS"]), (True, 0.3))

    def test_a_jump_within_the_last_second_before_the_start_reads_as_a_crossing_at_the_start(self):
        # The rule as written: the first message of the attempt is measured against the second before it.
        flipped_at = lambda at: (lambda t: {"left_y": -1.0, "right_y": 3.0} if t < ms(at) else
                                 {"left_y": -3.0, "right_y": 1.0})
        r = Run(LC(cross=None, jump=0.0), model=flipped_at(19.1))
        self.assertEqual((r.record["crossed"], r.record["crossS"]), (True, 0.0))
        r = Run(LC(cross=None, jump=0.0), model=flipped_at(19.0))     # the high point is 1.1 s back
        self.assertEqual(r.record["crossed"], False)

    def test_every_message_counts_whatever_its_line_probability(self):
        r = Run(LC(), model=lambda t: {"left_prob": 0.1, "right_prob": 0.1} if ms(22) <= t < ms(24) else None)
        self.assertEqual((r.record["crossed"], r.record["crossS"]), (True, 3.0))

    def test_messages_without_line_positions_are_passed_over(self):
        r = Run(LC(), model=lambda t: {"left_y": NAN} if t in (ms(22.9), ms(23.0)) else None)
        self.assertEqual((r.record["crossed"], r.record["crossS"]), (True, 3.1))

    def test_the_first_of_two_equal_jumps_is_the_crossing(self):
        flips = lambda t: {"left_y": -2.0 - 2.0 * (ms(22) <= t < ms(24)), "right_y": 2.0 - 2.0 * (ms(22) <= t < ms(24))}
        again = lambda t: flips(t) if t < ms(25) else {"left_y": -4.0, "right_y": 0.0}
        r = Run(LC(cross=None, jump=0.0, starting=4.5), model=again)
        self.assertEqual(r.record["crossS"], 2.0)


class PeakTests(unittest.TestCase):
    """Largest change of lateral acceleration against its level before the lane change."""

    def peak(self, accel, changes=None, **more):
        return Run(changes or LC(), accel=accel, **more)

    def test_it_is_measured_against_the_level_before_the_start(self):
        r = self.peak(lambda t: 0.3 - (1.0 if ms(21) <= t < ms(22) else 0.0))
        self.assertEqual(r.record["peakLatAccel"], 1.0)                # -0.7 against +0.3

    def test_the_level_window_ends_are_three_and_one_second_before_the_start(self):
        # 11 Samples at 0.4 (the two at the ends among them) and 10 at 0.0 inside; 0.0 on either side of it.
        def accel(t):
            if ms(17) <= t <= ms(19):
                return 0.0 if ms(17) < t <= ms(18) else 0.4
            return 0.0 if t < ms(20) else 0.4
        self.assertEqual(self.peak(accel).record["peakLatAccel"], 0.0)  # one Sample more or less would make it 0.2

    def test_the_level_is_the_median_from_three_to_one_second_before_the_start(self):
        def accel(t):
            if ms(17) <= t <= ms(19):
                return 2.0 if t < ms(17.9) else 0.4                    # 9 Samples at 2.0, 12 at 0.4
            return 5.0 if t < ms(20) else 0.4 + (0.6 if ms(21) <= t < ms(22) else 0.0)
        self.assertEqual(self.peak(accel).record["peakLatAccel"], 0.6)

    def test_an_even_number_of_samples_gives_the_mean_of_the_two_middle_ones(self):
        def accel(t):
            if t == ms(17):
                return NAN                                             # leaves 20 Samples with a curvature
            if ms(17) < t <= ms(19):
                return 0.0 if t <= ms(18) else 0.4                     # 10 at 0.0 and 10 at 0.4: the level is 0.2
            return 0.2
        self.assertEqual(self.peak(accel).record["peakLatAccel"], 0.0)

    def test_it_is_looked_for_from_the_start_to_three_seconds_after_the_end(self):
        pulse = lambda a, b: (lambda t: 0.9 if ms(a) <= t < ms(b) else 0.0)
        self.assertEqual(self.peak(pulse(20.0, 20.1)).record["peakLatAccel"], 0.9)
        self.assertEqual(self.peak(pulse(27.5, 27.6)).record["peakLatAccel"], 0.9)
        self.assertEqual(self.peak(pulse(19.1, 20.0)).record["peakLatAccel"], 0.0)
        self.assertEqual(self.peak(pulse(27.6, 29.0)).record["peakLatAccel"], 0.0)

    def test_it_stops_at_the_start_of_the_next_attempt(self):
        pulse = lambda t: 0.9 if ms(26.1) <= t < ms(27) else (0.5 if t == ms(26) else 0.0)
        r = self.peak(pulse, changes=(LC(), LC(start=26.0, pre=1.0)), n=600)
        self.assertEqual([x["peakLatAccel"] for x in r.records], [0.5, 0.9])

    def test_it_stops_at_the_first_attempt_that_follows_not_at_a_later_one(self):
        short = dict(pre=0.5, starting=1.0, finishing=0.0, cross=None)
        r = self.peak(lambda t: 0.9 if ms(26) <= t < ms(27) else 0.0,
                      changes=(LC(), LC(start=25.5, **short), LC(start=29.0, **short)), n=600)
        first = [x for x in r.records if x["outcome"] == "completed"]
        self.assertEqual([x["peakLatAccel"] for x in first], [0.0])    # its window ended at 25.5 s

    def test_samples_without_curvature_are_passed_over(self):
        r = Run(LC(), accel=lambda t: NAN if ms(21) <= t < ms(22) else (0.7 if ms(22) <= t < ms(23) else 0.0))
        self.assertEqual(r.record["peakLatAccel"], 0.7)

    def test_without_a_level_before_the_start_there_is_no_peak(self):
        r = Run(LC(start=0.8, pre=0.5), accel=lambda t: 0.5)
        self.assertEqual((r.record["outcome"], r.record["crossed"]), ("completed", True))
        self.assertIsNone(r.record["peakLatAccel"])

    def test_only_a_completed_attempt_with_a_crossing_has_one(self):
        accel = lambda t: 0.9 if ms(21) <= t < ms(22) else 0.0
        self.assertIsNone(Run(LC(starting=3.5, finishing=0.0), accel=accel).record["peakLatAccel"])     # cancelled
        self.assertIsNone(Run(LC(jump=1.0), accel=accel).record["peakLatAccel"])                        # no crossing
        hands_on = Run(LC(), accel=accel, car=lambda t: {"pressed": t >= ms(21)})
        self.assertEqual(hands_on.record["peakLatAccel"], 0.9)         # hands on or not


class SettleTests(unittest.TestCase):
    """How the truck settled in the new lane, over the 8 s after the crossing (23.0 s to 31.0 s here)."""

    def settle(self, *changes, **more):
        r = Run(changes or LC(), **more)
        record = r.records[0]
        return record["settleState"], record["settleS"], record["overshootM"]

    def test_settled(self):
        self.assertEqual(self.settle(), ("settled", 2.6, 0.0))

    def test_a_truck_that_never_leaves_the_band_settles_at_once(self):
        self.assertEqual(self.settle(LC(recover=0.0)), ("settled", 0.0, 0.0))

    def test_overshoot_past_the_final_position(self):
        # Back at the centre 2.0 s after the crossing, then 0.4 m past it and back within two more seconds.
        self.assertEqual(self.settle(LC(recover=2.0, overshoot=0.4)), ("settled", 3.4, 0.4))

    def test_overshoot_inside_the_band_does_not_delay_the_settle_time(self):
        self.assertEqual(self.settle(LC(recover=2.0, overshoot=0.2)), ("settled", 1.8, 0.2))

    def test_still_outside_the_band_five_seconds_after_the_crossing_is_not_settled(self):
        self.assertEqual(self.settle(LC(recover=6.5)), ("not_settled", None, 0.0))
        self.assertEqual(self.settle(LC(recover=5.9)), ("not_settled", None, 0.0))     # last outside at 5.0 s exactly
        self.assertEqual(self.settle(LC(recover=5.8)), ("settled", 5.0, 0.0))          # last outside at 4.9 s

    def test_the_final_position_is_a_median_not_a_mean(self):
        # One confident message far off in the last three seconds: the final position stays where the rest are.
        wild = lambda t: {"left_y": 1.0, "right_y": 5.0} if t == ms(29) else None
        self.assertEqual(self.settle(model=wild), ("not_settled", None, 3.0))

    def test_the_final_position_is_the_median_of_the_last_three_seconds(self):
        # The lane centre ends 0.5 m off the truck's middle: the truck has settled there.
        shifted = lambda t: {"left_y": -1.5, "right_y": 2.5} if t >= ms(26) else None
        self.assertEqual(self.settle(model=shifted), ("settled", 3.0, 0.0))        # from the message at 26.0 s on

    def test_not_measurable_when_the_model_stream_ends_early(self):
        self.assertEqual(self.settle(no_model=lambda t: t > ms(30.3)), ("not_measurable:data", None, None))
        self.assertEqual(self.settle(no_model=lambda t: t > ms(30.4)), ("settled", 2.6, 0.0))      # 0.6 s short

    def test_not_measurable_with_a_gap_in_the_window(self):
        self.assertEqual(self.settle(no_model=between(27.0, 27.3)), ("not_measurable:data", None, None))
        self.assertEqual(self.settle(no_model=between(30.7, 31.0)), ("not_measurable:data", None, None))
        self.assertEqual(self.settle(no_model=between(27.0, 27.2)), ("settled", 2.6, 0.0))         # 0.3 s apart

    def test_a_gap_that_reaches_past_the_window_only_counts_as_an_early_end(self):
        self.assertEqual(self.settle(no_model=between(30.8, 31.1)), ("settled", 2.6, 0.0))
        self.assertEqual(self.settle(no_model=between(30.3, 31.1)), ("not_measurable:data", None, None))

    def test_not_measurable_when_the_next_lane_change_starts_in_the_window(self):
        self.assertEqual(self.settle(LC(), LC(start=30.9, pre=2.0), n=600),
                         ("not_measurable:next_lane_change", None, None))
        self.assertEqual(self.settle(LC(), LC(start=31.1, pre=2.0), n=600), ("settled", 2.6, 0.0))

    def test_not_measurable_with_steering_off_for_over_two_percent_of_the_samples(self):
        # 111 Samples from 20.0 s to 31.0 s.
        self.assertEqual(self.settle(lat=lambda t: not ms(27) <= t < ms(27.3)),
                         ("not_measurable:steering_off", None, None))
        self.assertEqual(self.settle(lat=lambda t: not ms(27) <= t < ms(27.2)), ("settled", 2.6, 0.0))

    def test_a_missing_control_stream_is_not_steering_on(self):
        p = Pipeline(PipelineConfig(measures=(LaneChanges,)))
        c = LC()
        batches = [synth.batch(t_ns, skip=("carControl",) if ms(26) <= (t_ns - T0) // MS < ms(27.5) else (),
                               drivingModelData=model_of((c,), (t_ns - T0) // MS))
                   for t_ns in synth.ticks(450, 100, T0)]
        records = [data for kind, data in synth.run(p, batches) if kind == "lane_change"]
        self.assertEqual(records[0]["settleState"], "not_measurable:steering_off")

    def test_not_measurable_when_the_lines_are_not_confident(self):
        low = lambda a, b, **probs: (lambda t: probs if ms(a) <= t < ms(b) else None)
        # 81 messages from 23.0 s to 31.0 s, 31 of them from 28.0 s on.
        self.assertEqual(self.settle(model=low(23.0, 27.1, left_prob=0.4)), ("not_measurable:lines", None, None))
        self.assertEqual(self.settle(model=low(23.0, 27.0, left_prob=0.4))[0], "settled")
        self.assertEqual(self.settle(model=low(28.0, 29.6, right_prob=0.4)), ("not_measurable:lines", None, None))
        self.assertEqual(self.settle(model=low(28.0, 29.5, right_prob=0.4))[0], "settled")
        self.assertEqual(self.settle(model=low(23.0, 27.1, left_prob=0.5, right_prob=0.5))[0], "settled")

    def test_exactly_half_of_the_messages_confident_is_enough(self):
        # Without the message at 29.0 s the last three seconds hold 30 messages; 15 of them are not confident.
        low = lambda t: {"left_prob": 0.4} if ms(28) <= t < ms(29.6) else None
        self.assertEqual(self.settle(model=low, no_model=lambda t: t == ms(29))[0], "settled")
        # And of the 80 left from the crossing on, 40 not confident.
        low = lambda t: {"left_prob": 0.4} if ms(23) <= t < ms(27) else None
        self.assertEqual(self.settle(model=low, no_model=lambda t: t == ms(27.5))[0], "settled")

    def test_only_confident_messages_are_used(self):
        # A wild line position while the lines are not confident changes nothing.
        wild = lambda t: {"left_y": -9.0, "left_prob": 0.2} if ms(27) <= t < ms(28) else None
        self.assertEqual(self.settle(model=wild), ("settled", 2.6, 0.0))

    def test_the_settle_time_ends_at_the_first_confident_message_back_inside(self):
        unsure = lambda t: {"right_prob": 0.3} if ms(25.6) <= t < ms(26) else None
        self.assertEqual(self.settle(model=unsure), ("settled", 3.0, 0.0))

    def test_the_first_reason_in_the_fixed_order_is_given(self):
        gap = between(27.0, 27.3)
        off = lambda t: not ms(26) <= t < ms(27)
        low = lambda t: {"left_prob": 0.1} if t >= ms(23) else None
        nxt = (LC(), LC(start=29.0, pre=2.0))
        self.assertEqual(self.settle(*nxt, no_model=gap, lat=off, model=low, n=600)[0], "not_measurable:data")
        self.assertEqual(self.settle(*nxt, lat=off, model=low, n=600)[0], "not_measurable:next_lane_change")
        self.assertEqual(self.settle(lat=off, model=low)[0], "not_measurable:steering_off")
        self.assertEqual(self.settle(model=low)[0], "not_measurable:lines")

    def test_an_attempt_that_was_not_completed_or_did_not_cross_has_no_settle_figures(self):
        self.assertEqual(self.settle(LC(finishing=0.0)), ("no_crossing", None, None))
        self.assertEqual(self.settle(LC(jump=1.0)), ("no_crossing", None, None))

    def test_a_hands_on_lane_change_is_measured_all_the_same(self):
        self.assertEqual(self.settle(car=lambda t: {"pressed": ms(21) <= t < ms(24)}), ("settled", 2.6, 0.0))

    def test_the_first_of_a_double_lane_change_cannot_settle(self):
        r = Run((LC(), LC(start=24.5, pre=0.0)), n=600)
        self.assertEqual([x["settleState"] for x in r.records], ["not_measurable:next_lane_change", "settled"])


class SlowStreamTests(unittest.TestCase):
    """A 2 Hz model stream, as a decimated log holds it."""

    def test_counts_and_record(self):
        r = Run(LC(), slow=True, car=PUSH)
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        self.assertEqual((r.record["outcome"], r.record["crossed"], r.record["crossS"], r.record["startingS"]),
                         ("completed", True, 3.0, 3.5))
        self.assertEqual((r.record["settleState"], r.record["settleS"], r.record["overshootM"]),
                         ("slow_stream", None, None))
        self.assertEqual(r.record["pushTorqueMax"], 140)
        self.assertEqual(r.record["peakLatAccel"], 0.0)

    def test_half_a_second_of_finishing_is_enough(self):
        self.assertEqual(Run(LC(finishing=0.5), slow=True).record["outcome"], "completed")
        self.assertEqual(Run(LC(finishing=0.5)).record["outcome"], "cancelled")      # not on a fast stream

    def test_one_missing_message_is_no_gap_and_two_are(self):
        r = Run(LC(), slow=True, no_model=lambda t: t == ms(21.5))
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        r = Run(LC(), slow=True, no_model=between(21.5, 22.5))
        self.assertEqual(r.counts, counts(truncated=1))

    def test_a_jump_of_one_metre_is_a_crossing(self):
        self.assertEqual(Run(LC(jump=1.0625), slow=True).record["crossed"], True)
        self.assertEqual(Run(LC(jump=0.9375), slow=True).record["crossed"], False)
        self.assertEqual(Run(LC(jump=1.0625)).record["crossed"], False)              # not on a fast stream

    def test_the_jump_is_measured_over_the_last_1_6_seconds(self):
        # The centre steps down by 0.6 m at 22.5 s and again at 23.5 s: 1.2 m below where it was at 22.0 s.
        step = lambda late: (lambda t: {"left_y": -2.0 - 0.6 * ((t >= ms(22.5)) + (t >= ms(late))),
                                        "right_y": 2.0 - 0.6 * ((t >= ms(22.5)) + (t >= ms(late)))})
        r = Run(LC(cross=None, jump=0.0, starting=4.5), slow=True, model=step(23.5))
        self.assertEqual((r.record["crossed"], r.record["crossS"]), (True, 3.5))
        r = Run(LC(cross=None, jump=0.0, starting=4.5), slow=True, model=step(24.0))
        self.assertEqual(r.record["crossed"], False)

    def test_no_settle_figures_even_for_a_cancelled_attempt_that_is_plain_no_crossing(self):
        r = Run(LC(finishing=0.0), slow=True)
        self.assertEqual(r.record["settleState"], "no_crossing")

    def test_blinker_only(self):
        r = Run(LC(pre=2.0, starting=0.0, finishing=0.0), slow=True)
        self.assertEqual(r.counts, counts(blinkerOnly=1))

    def test_a_double_lane_change(self):
        r = Run((LC(), LC(start=24.5, pre=0.0)), slow=True, n=600)
        self.assertEqual(r.counts, counts(starts=2, completed=2))

    def test_the_record_comes_three_seconds_after_the_end(self):
        r = Run(LC(), slow=True)
        self.assertEqual(r.sent_at, [276])                             # the Sample after the one at 27.5 s


class BookingTests(unittest.TestCase):
    def test_a_lane_change_across_a_window_boundary_counts_once_in_the_window_where_it_ends(self):
        r = Run(LC(start=57.0), n=900)
        self.assertEqual([s["lcStarts"] for s in r.summaries], [0, 1])
        self.assertEqual([s["lcCompleted"] for s in r.summaries], [0, 1])
        self.assertEqual(len(r.records), 1)
        self.assertEqual([kind for kind, _ in r.messages], ["summary", "lane_change", "summary"])

    def test_starts_are_completed_plus_cancelled_in_every_window(self):
        r = Run((LC(start=20.0), LC(start=40.0, finishing=0.0), LC(start=57.0), LC(start=100.0, finishing=0.2)), n=1300)
        self.assertEqual([(s["lcStarts"], s["lcCompleted"], s["lcCancelled"]) for s in r.summaries],
                         [(2, 1, 1), (2, 1, 1), (0, 0, 0)])

    def test_the_blinker_only_count_is_booked_when_the_state_returns_to_off(self):
        r = Run(LC(start=61.0, pre=3.0, starting=0.0, finishing=0.0), n=900)
        self.assertEqual([s["lcBlinkerOnly"] for s in r.summaries], [0, 1])

    def test_the_record_of_a_completed_lane_change_comes_eight_seconds_after_its_crossing(self):
        r = Run(LC())
        self.assertEqual(r.sent_at, [311])                             # the Sample after the one at 31.0 s

    def test_the_record_of_a_cancelled_attempt_comes_three_seconds_after_its_end(self):
        r = Run(LC(starting=1.5, finishing=0.0, cross=None))
        self.assertEqual(r.sent_at, [246])                             # the end is at 21.5 s

    def test_the_record_of_a_completed_lane_change_without_a_crossing_comes_three_seconds_after_its_end(self):
        r = Run(LC(jump=1.0))
        self.assertEqual(r.sent_at, [276])

    def test_an_early_crossing_still_waits_for_three_seconds_after_the_end(self):
        r = Run(LC(starting=9.0, cross=0.5), accel=lambda t: 0.9 if ms(32.9) <= t <= ms(33) else 0.0)
        self.assertEqual(r.sent_at, [331])                             # the end is at 30.0 s, the crossing at 20.5 s
        self.assertEqual(r.record["peakLatAccel"], 0.9)

    def test_records_come_in_the_order_they_are_complete(self):
        r = Run((LC(), LC(start=26.0, starting=1.0, finishing=0.0, cross=None)), n=600)
        self.assertEqual([x["outcome"] for x in r.records], ["cancelled", "completed"])
        self.assertEqual(r.sent_at, [301, 311])                        # 27.0 + 3 s, and 23.0 + 8 s

    def test_the_drive_ends_during_a_lane_change(self):
        r = Run(LC(), n=221)                                           # still starting
        self.assertEqual(r.counts, counts(truncated=1))
        self.assertEqual(r.records, [])
        self.assertEqual(r.end["truncatedLaneChanges"], 1)
        self.assertEqual(r.summaries[-1]["lcTruncated"], 1)
        self.assertEqual(r.summaries[-1]["windowS"], 22.1)
        r = Run(LC(), n=241)                                           # finishing
        self.assertEqual(r.counts, counts(truncated=1))

    def test_the_drive_ends_before_the_settle_window_is_over(self):
        r = Run(LC(), n=300)
        self.assertEqual(r.counts, counts(starts=1, completed=1))
        self.assertEqual((r.record["outcome"], r.record["crossed"], r.record["crossS"]), ("completed", True, 3.0))
        self.assertEqual((r.record["settleState"], r.record["settleS"], r.record["overshootM"]),
                         ("not_measurable:drive_end", None, None))
        self.assertEqual([kind for kind, _ in r.messages], ["lane_change", "summary"])     # before the last summary
        self.assertEqual(r.end["truncatedLaneChanges"], 0)

    def test_the_drive_ends_before_the_crossing_could_be_looked_for(self):
        r = Run(LC(cross=5.0), n=250)                                  # ends 0.4 s after the lane change; crossing later
        self.assertEqual((r.record["outcome"], r.record["crossed"], r.record["settleState"]),
                         ("completed", False, "not_measurable:drive_end"))

    def test_the_drive_ends_right_after_a_cancelled_attempt(self):
        r = Run(LC(starting=1.5, finishing=0.0, cross=None), n=225)
        self.assertEqual((r.record["outcome"], r.record["settleState"]), ("cancelled", "no_crossing"))
        self.assertEqual(r.counts, counts(starts=1, cancelled=1))

    def test_the_drive_ends_on_the_last_sample_of_the_settle_window(self):
        r = Run(LC(), n=311)                                           # the last Sample is at 31.0 s
        self.assertEqual((r.record["settleState"], r.record["settleS"]), ("settled", 2.6))

    def test_a_long_stall_leaves_a_waiting_record_without_settle_figures(self):
        p = Pipeline(PipelineConfig(measures=(LaneChanges,)))
        c = LC()
        times = synth.ticks(260, 100, T0)
        times += synth.ticks(100, 100, times[-1] + 70 * SEC)
        batches = [synth.batch(t_ns, drivingModelData=model_of((c,), (t_ns - T0) // MS)) for t_ns in times]
        records = [data for kind, data in synth.run(p, batches) if kind == "lane_change"]
        self.assertEqual([x["settleState"] for x in records], ["not_measurable:data"])


class RoundingTests(unittest.TestCase):
    def test_decimals(self):
        r = Run(LC(start=20.0, starting=3.55, finishing=1.05, cross=3.05, recover=2.05, overshoot=0.3337),
                period_ms=50, n=900, car=lambda t: {"v": 24.96, "pressed": ms(22) <= t < ms(22.85)},
                accel=lambda t: 0.123456 if t < ms(21) else 0.9)
        x = r.record
        self.assertEqual((x["startingS"], x["crossS"], x["vStartMs"]), (3.55, 3.05, 25.0))
        self.assertEqual(x["pressedShare"], 0.23)                      # 17 of the 73 Samples from 21.0 to 24.6 s
        self.assertEqual(x["peakLatAccel"], 0.78)
        self.assertEqual((x["settleState"], x["overshootM"]), ("settled", 0.33))
        self.assertEqual(x["settleS"], round(x["settleS"], 2))


class RegistrationTests(unittest.TestCase):
    def test_it_is_a_measure_of_this_build_and_runs_with_the_owner_facing_ones(self):
        self.assertIn(LaneChanges, measure_set.MEASURES)
        self.assertTrue(LaneChanges.owner_facing)
        self.assertEqual(LaneChanges.name, "laneChange")
        self.assertEqual(Pipeline().measure_names, ["steering", "limit", "brake", "laneChange"])

    def test_it_needs_nothing_but_the_measure_interface_and_the_shared_rules(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(__file__).with_name("measure_lane_change.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, {"__future__", "collections", "measure_api", "shared_rules"})

    def test_the_overlay_time_comes_from_the_steering_measure(self):
        r = Run(LC(), measures=(SteeringTime, LaneChanges))
        self.assertEqual(r.summaries[0]["laneChangeS"], 5.5)           # blinker state, starting and finishing
        self.assertEqual(r.counts, counts(starts=1, completed=1))


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
    def test_a_failing_log_only_measure_leaves_the_lane_change_numbers_alone(self):
        changes = (LC(), LC(start=57.0), LC(start=80.0, finishing=0.0))
        clean = Run(changes, n=1300, measures=(SteeringTime, LaneChanges))
        faulty = Run(changes, n=1300, measures=(Faulty, SteeringTime, LaneChanges))
        self.assertEqual(len(clean.records), 3)
        self.assertEqual(faulty.records, clean.records)
        self.assertEqual([{key: s[key] for key in COUNTS} for s in faulty.summaries],
                         [{key: s[key] for key in COUNTS} for s in clean.summaries])
        self.assertEqual(faulty.end["failed"], ["lane"])
        self.assertEqual(clean.end["failed"], [])


if __name__ == "__main__":
    unittest.main()
