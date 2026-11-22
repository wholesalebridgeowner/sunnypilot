"""The measures of this build together: what one summary holds, and episodes at the edges of a window and a drive."""
import json
import math
import unittest

import measure_set
import synth
from measure_api import Measure
from measure_swings import WheelSwings
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS"}
# Owner-facing measures send flat fields in every summary.
FLAT_FIELDS = {
    "steering": {"latOnS", "steeringS", "forcedZeroS", "fzFallbackS", "pressedS", "driverS", "laneChangeS", "movingS",
                 "cruiseS", "vMean", "vMeanSteering"},
    "limit": {"limitS", "limitDriverS", "limitLaneChangeS", "limitNoneS", "limitUnexpectedS", "askS", "askNoneS",
              "warnS", "warnEvents", "demandMaxNone"},
    "brake": {"brakeEpisodes"},
    "laneChange": {"lcStarts", "lcCompleted", "lcCancelled", "lcBlinkerOnly", "lcHandsOn", "lcTruncated"},
}
# The learned delay is owner-facing too. It sends one block, from the learner's first message of the drive on.
FLAT_FIELDS["cruiseCutoffs"] = {"cruiseCutoffs"}  # window-level count map on observed carState pairs
OWNER_BLOCKS = {"delay": {"valueS", "minS", "maxS", "status", "blocks"}}
# Owner-facing measures that send event records only, never summary fields or a block (2026-11-21, r2).
EVENT_ONLY = ("steeringLoss",)
# Log-only measures send one block each, named like the measure, and only when it has something to say.
BLOCKS = {
    "lane": {"n", "sumSq", "scoredS", "validS", "eligibleS", "vMean", "widthMin", "widthMax", "probMin"},
    "swings": {"n", "straightS", "vMean"},
    "ay": {"n", "s", "sumSq", "planSumSq", "vMean"},
    "curve": {"s", "events", "belowSum", "aboveSum", "s04", "below04Sum", "above04Sum", "lowS", "lowLimitS",
              "lowBelowSum", "lowAboveSum"},
    "lag": {"s", "n", "syy", "sxy", "sxx"},
}
RECORD_TYPES = {"summary", "brake", "lane_change", "limit_event"}
ALL_FLAT = set().union(*FLAT_FIELDS.values())


def lane_state(t_ms, start_ms):
    """Model state of a lane change that starts at `start_ms`: 1 s of blinker, 3.5 s starting, 1 s finishing."""
    u = t_ms - start_ms
    if -1000 <= u < 0:
        return "preLaneChange"
    if 0 <= u < 3500:
        return "laneChangeStarting"
    if 3500 <= u < 4500:
        return "laneChangeFinishing"
    return "off"


def drive(n, stop_at_s=None, lane_change_at_s=None, limit_at_s=None, loss_at_s=None):
    """Batches of a drive at 25 m/s with, each from its given time: a stop of 3 m/s^2 for 4 s, a lane change,
    4 s with the steering command at its limit and the warning up, and a brake press at speed that switches
    steering off for the rest of the drive (a steering loss)."""
    def change(i, t_ns):
        t = (t_ns - T0) // MS
        out = {"carState": dict(v=25.0)}
        if stop_at_s is not None:
            out["carState"]["v"] = 25.0 - 3.0 * min(max(t / 1000 - stop_at_s, 0.0), 4.0)
            out["carState"]["brake"] = 0 <= t / 1000 - stop_at_s < 4.0
        if lane_change_at_s is not None:
            state = lane_state(t, round(lane_change_at_s * 1000))
            out["drivingModelData"] = dict(state=state, direction="none" if state == "off" else "left")
        if limit_at_s is not None and 0 <= t / 1000 - limit_at_s < 4.0:
            out["carOutput"] = dict(torque=350.0)
            out["carControl"] = dict(torque=1.0)
            out["controlsState"] = dict(desired=0.004)
            out["onroadEvents"] = ["steerSaturated"]
        if loss_at_s is not None and t / 1000 >= loss_at_s:
            out["carControl"] = dict(out.get("carControl") or {}, lat=False)
            out["carState"]["brake"] = t / 1000 - loss_at_s < 0.5
        return out
    return synth.drive(synth.ticks(n, 100, T0), change)


def road(n, stop_at_s=None, period_ms=100.0, live_delay=False):
    """Batches of a drive that gives every log-only measure something to say: a straight with a small wave of
    the wheel, of the truck in its lane and of its cornering, and a curve of 1.2 m/s^2 in the second half of
    every minute, followed 0.4 s late. Optionally a stop, and the delay learner's messages (4 a second)."""
    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        v = 25.0 if stop_at_s is None else 25.0 - 3.0 * min(max(t_s - stop_at_s, 0.0), 4.0)
        demand = lambda u: 1.2 + 0.3 * math.sin(2 * math.pi * 0.3 * u) if u % 60.0 >= 30.0 else 0.0
        wobble = 0.04 * math.sin(2 * math.pi * 0.2 * t_s)
        centre = 0.05 * math.sin(2 * math.pi * 0.1 * t_s)
        out = {"carState": dict(v=v, angle=3.0 * math.sin(2 * math.pi * 0.25 * t_s), brake=v < 25.0),
               "controlsState": dict(desired=demand(t_s) / 625.0, actual=(0.95 * demand(t_s - 0.4) + wobble) / 625.0),
               "drivingModelData": dict(left_y=centre - 1.8, right_y=centre + 1.8)}
        if live_delay and round(t_s * 1000) % 250 < 100:
            out["liveDelay"] = dict(delay=0.3457 + 0.0004 * math.sin(2 * math.pi * t_s / 480.0), blocks=30)
        return out
    return synth.drive(synth.ticks(n, period_ms, T0), change)


def envelope(kind, data) -> str:
    """The message as it goes out: compact JSON, sorted keys."""
    return json.dumps({"v": 2, "type": kind, "driveId": "0123456789abcdef0123456789abcdef",
                       "id": "0123456789ab-123456", "data": data}, sort_keys=True, separators=(",", ":"))


class SummaryTests(unittest.TestCase):
    def test_the_measures_of_this_build_in_run_order(self):
        names = [m.name for m in measure_set.MEASURES]
        self.assertEqual(names, list(FLAT_FIELDS) + list(OWNER_BLOCKS) + list(EVENT_ONLY) + list(BLOCKS))
        self.assertEqual([m.owner_facing for m in measure_set.MEASURES], [True] * 7 + [False] * 5)
        self.assertEqual(Pipeline().measure_names, names)             # the owner-facing ones run first
        self.assertTrue(all(issubclass(m, Measure) for m in measure_set.MEASURES))

    def test_a_summary_holds_every_flat_field_once(self):
        out = synth.summaries(synth.run(Pipeline(), drive(300)))
        self.assertEqual(len(ALL_FLAT), sum(len(fields) for fields in FLAT_FIELDS.values()))  # no name used twice
        self.assertFalse(ALL_FLAT & (set(BLOCKS) | set(OWNER_BLOCKS)))
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS - {"health"} - set(BLOCKS), ALL_FLAT)

    def test_each_measure_sends_its_own_fields_and_no_other(self):
        for measure in measure_set.MEASURES:
            with self.subTest(measure=measure.name):
                p = Pipeline(PipelineConfig(measures=(measure,)))
                out = synth.summaries(synth.run(p, road(1300, live_delay=True)))
                sent = set().union(*(set(s) for s in out)) - PIPELINE_FIELDS - {"health"}
                if measure.name in EVENT_ONLY:
                    self.assertTrue(measure.owner_facing)
                    self.assertEqual(sent, set())
                    continue
                if measure.name in FLAT_FIELDS:
                    self.assertTrue(measure.owner_facing)
                    self.assertEqual(sent, FLAT_FIELDS[measure.name])
                    self.assertTrue(all(set(s) - PIPELINE_FIELDS - {"health"} == sent for s in out))
                else:
                    blocks = OWNER_BLOCKS if measure.owner_facing else BLOCKS
                    self.assertEqual(sent, {measure.name})
                    for s in out:
                        if measure.name in s:
                            self.assertEqual(set(s[measure.name]), blocks[measure.name])

    def test_a_summary_with_every_block(self):
        out = synth.summaries(synth.run(Pipeline(), road(1300, live_delay=True)))
        self.assertEqual(set(out[1]) - PIPELINE_FIELDS - {"health"}, ALL_FLAT | set(OWNER_BLOCKS) | set(BLOCKS))
        for name, fields in list(BLOCKS.items()) + list(OWNER_BLOCKS.items()):
            self.assertEqual(set(out[1][name]), fields)
        self.assertNotIn("failed", out[1]["health"])
        # Blocks in the order the size cap takes them out last: lane, swings, ay, curve, lag.
        self.assertEqual([m.name for m in measure_set.MEASURES][7:], ["lane", "swings", "ay", "curve", "lag"])

    def test_without_the_delay_learner_there_is_no_delay_block(self):
        out = synth.summaries(synth.run(Pipeline(), road(1300)))
        self.assertEqual(set(out[1]) - PIPELINE_FIELDS - {"health"}, ALL_FLAT | set(BLOCKS))

    def test_a_full_summary_stays_well_under_the_size_cap(self):
        out = synth.summaries(synth.run(Pipeline(), road(1300, live_delay=True)))
        full = [s for s in out if set(BLOCKS) | set(OWNER_BLOCKS) <= set(s)]
        self.assertTrue(full)
        for s in full:
            self.assertLess(len(envelope("summary", s)), 1600)        # the cap is 2,048 bytes

    def test_a_parked_window_sends_every_flat_field_and_no_block(self):
        parked = [synth.batch(t, carState=dict(v=0.0, gear="park"), carControl=dict(lat=False))
                  for t in synth.ticks(700, 100, T0)]
        out = synth.summaries(synth.run(Pipeline(), parked))
        for s in out:
            self.assertEqual(set(s) - PIPELINE_FIELDS - {"health"}, ALL_FLAT)
        self.assertEqual((out[0]["brakeEpisodes"], out[0]["lcStarts"], out[0]["lcTruncated"]), (0, 0, 0))

    def test_the_drive_end_fields(self):
        p = Pipeline()
        synth.run(p, drive(300))
        end = p.drive_end_fields()
        self.assertEqual(set(end), {"windows", "totals", "failed", "truncatedLaneChanges", "delay"})
        self.assertEqual(set(end["totals"]), {"windowS", "unobservedS", "steeringS", "movingS"})
        self.assertEqual((end["failed"], end["truncatedLaneChanges"]), ([], 0))

    def test_only_the_known_message_types_are_sent(self):
        messages = synth.run(Pipeline(), drive(900, stop_at_s=20.0, lane_change_at_s=30.0, limit_at_s=50.0))
        self.assertEqual({kind for kind, _ in messages}, RECORD_TYPES)
        self.assertEqual({kind for kind, _ in synth.run(Pipeline(), road(1300, live_delay=True))}, {"summary"})

    def test_every_number_sent_is_finite_and_no_string_is(self):
        def walk(value):
            if isinstance(value, dict):
                for inner in value.values():
                    yield from walk(inner)
            elif isinstance(value, list):
                for inner in value:
                    yield from walk(inner)
            else:
                yield value
        for kind, data in synth.run(Pipeline(), road(1300, stop_at_s=70.0)):
            for value in walk({key: inner for key, inner in data.items() if key in BLOCKS}):
                self.assertTrue(value is None or (isinstance(value, (int, float)) and math.isfinite(value)), value)
                self.assertFalse(isinstance(value, bool))
            json.dumps(data, allow_nan=False)


class EdgeTests(unittest.TestCase):
    def test_episodes_across_a_window_boundary_count_once(self):
        # A stop, a lane change, a warning and a limit event, each begun before the first minute is over.
        messages = synth.run(Pipeline(), drive(900, stop_at_s=58.0, lane_change_at_s=57.0, limit_at_s=58.5))
        out = synth.summaries(messages)
        self.assertEqual(len(out), 2)
        self.assertEqual([s["brakeEpisodes"] for s in out], [0, 1])
        self.assertEqual([(s["lcStarts"], s["lcCompleted"]) for s in out], [(0, 0), (1, 1)])
        self.assertEqual([s["warnEvents"] for s in out], [1, 0])       # booked where the warning starts
        self.assertEqual(round(sum(s["limitS"] for s in out), 1), 4.0)
        for kind in ("brake", "lane_change", "limit_event"):
            self.assertEqual(sum(1 for k, _ in messages if k == kind), 1)

    def test_the_drive_ends_during_a_stop_and_a_lane_change(self):
        p = Pipeline()
        messages = synth.run(p, drive(215, stop_at_s=19.0, lane_change_at_s=20.0))
        out = synth.summaries(messages)
        self.assertEqual([kind for kind, _ in messages], ["brake", "summary"])     # the stop is sent, the attempt is not
        self.assertEqual(out[-1]["brakeEpisodes"], 1)
        self.assertEqual((out[-1]["lcStarts"], out[-1]["lcCompleted"], out[-1]["lcCancelled"]), (0, 0, 0))
        self.assertEqual(out[-1]["lcTruncated"], 1)
        self.assertEqual(out[-1]["windowS"], 21.5)                     # the partial window with its true length
        self.assertEqual(p.drive_end_fields()["truncatedLaneChanges"], 1)

    def test_the_drive_ends_with_samples_still_waiting(self):
        # 33 s of straight road at speed: the Samples of the last 6 s have not been evaluated when the drive
        # ends. They are evaluated then, into the last window, and nothing from the last 2.0 s is scored
        # (nor from the last 3.0 s where the road must be known to be straight).
        out = synth.summaries(synth.run(Pipeline(), synth.drive(synth.ticks(330, 100, T0))))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["windowS"], 33.0)
        self.assertEqual(out[0]["swings"]["straightS"], 24.9)          # 5.1 s to 29.9 s
        self.assertEqual(out[0]["ay"]["s"], 24.9)
        self.assertEqual(out[0]["lane"]["eligibleS"], 25.9)            # 5.1 s to 30.9 s
        self.assertEqual(out[0]["lane"]["scoredS"], 20.0)              # 8.1 s to 28.0 s, 4.9 s before the end
        self.assertNotIn("curve", out[0])
        self.assertNotIn("lag", out[0])

    def test_every_third_sample_missing_changes_no_count(self):
        full = drive(900, stop_at_s=20.0, lane_change_at_s=40.0)
        thinned = [b for i, b in enumerate(full) if i % 3 != 2]
        for batches in (full, thinned):
            messages = synth.run(Pipeline(), batches)
            out = synth.summaries(messages)
            self.assertEqual(sum(s["brakeEpisodes"] for s in out), 1)
            self.assertEqual((sum(s["lcStarts"] for s in out), sum(s["lcCompleted"] for s in out),
                              sum(s["lcTruncated"] for s in out)), (1, 1, 0))


class IsolationTests(unittest.TestCase):
    """One measure must not take the others down."""

    def run_with(self, broken=None, fail_at=100):
        measures = list(measure_set.MEASURES)
        if broken is not None:
            class Broken(broken):
                calls = 0

                def on_sample(self, ctx, g):
                    type(self).calls += 1
                    if type(self).calls >= fail_at:
                        raise ZeroDivisionError("a repeatable fault in one core")
                    super().on_sample(ctx, g)
            measures[measures.index(broken)] = Broken
        p = Pipeline(PipelineConfig(measures=tuple(measures)))
        return synth.run(p, road(1300, stop_at_s=70.0)), p.drive_end_fields()

    def test_a_log_only_measure_that_fails_on_its_100th_sample(self):
        clean, clean_end = self.run_with()
        faulty, faulty_end = self.run_with(WheelSwings)
        self.assertEqual([kind for kind, _ in clean], ["summary", "brake", "summary", "summary"])
        self.assertEqual(clean_end["failed"], [])
        self.assertEqual(faulty_end["failed"], ["swings"])
        self.assertEqual(faulty_end["totals"], clean_end["totals"])    # steering-on time among them

        def without(data, *keys):
            return {key: value for key, value in data.items() if key not in keys}
        # Everything but the failed block and the health line is the same: the flat fields, the brake record,
        # and every other block.
        self.assertEqual([(kind, without(data, "swings", "health")) for kind, data in faulty],
                         [(kind, without(data, "swings", "health")) for kind, data in clean])
        for s in synth.summaries(faulty):
            self.assertNotIn("swings", s)                              # it failed in the first window
            self.assertEqual(s["health"]["failed"], ["swings"])
        # (The last window of the drive is slow, after the stop: no block there either way.)
        self.assertEqual(["swings" in s for s in synth.summaries(clean)], [True, True, False])
        for s in synth.summaries(clean):
            self.assertNotIn("failed", s["health"])

    def test_the_block_is_sent_until_the_measure_fails(self):
        faulty, end = self.run_with(WheelSwings, fail_at=900)
        blocks = ["swings" in s for s in synth.summaries(faulty)]
        self.assertEqual(blocks, [True, False, False])
        self.assertEqual([s["health"].get("failed") for s in synth.summaries(faulty)], [None, ["swings"], ["swings"]])


if __name__ == "__main__":
    unittest.main()
