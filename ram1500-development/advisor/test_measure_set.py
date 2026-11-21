"""The measures of this build together: what one summary holds, and episodes at the edges of a window and a drive."""
import unittest

import measure_set
import synth
from pipeline import Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC
PIPELINE_FIELDS = {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS"}
FLAT_FIELDS = {
    "steering": {"latOnS", "steeringS", "forcedZeroS", "fzFallbackS", "pressedS", "driverS", "laneChangeS", "movingS",
                 "cruiseS", "vMean", "vMeanSteering"},
    "limit": {"limitS", "limitDriverS", "limitLaneChangeS", "limitNoneS", "limitUnexpectedS", "askS", "askNoneS",
              "warnS", "warnEvents", "demandMaxNone"},
    "brake": {"brakeEpisodes"},
    "laneChange": {"lcStarts", "lcCompleted", "lcCancelled", "lcBlinkerOnly", "lcHandsOn", "lcTruncated"},
}
RECORD_TYPES = {"summary", "brake", "lane_change", "limit_event"}


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


def drive(n, stop_at_s=None, lane_change_at_s=None, limit_at_s=None):
    """Batches of a drive at 25 m/s with, each from its given time: a stop of 3 m/s^2 for 4 s, a lane change,
    and 4 s with the steering command at its limit and the warning up."""
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
        return out
    return synth.drive(synth.ticks(n, 100, T0), change)


class SummaryTests(unittest.TestCase):
    def test_the_measures_of_this_build_in_run_order(self):
        self.assertEqual([m.name for m in measure_set.MEASURES], list(FLAT_FIELDS))
        self.assertTrue(all(m.owner_facing for m in measure_set.MEASURES))
        self.assertEqual(Pipeline().measure_names, list(FLAT_FIELDS))

    def test_a_summary_holds_every_flat_field_once(self):
        out = synth.summaries(synth.run(Pipeline(), drive(300)))
        want = set().union(*FLAT_FIELDS.values())
        self.assertEqual(len(want), sum(len(fields) for fields in FLAT_FIELDS.values()))      # no name used twice
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS - {"health"}, want)

    def test_each_measure_sends_its_own_fields_and_no_other(self):
        for measure in measure_set.MEASURES:
            with self.subTest(measure=measure.name):
                p = Pipeline(PipelineConfig(measures=(measure,)))
                out = synth.summaries(synth.run(p, drive(300)))
                self.assertEqual(set(out[0]) - PIPELINE_FIELDS - {"health"}, FLAT_FIELDS[measure.name])

    def test_a_parked_window_sends_every_field_too(self):
        parked = [synth.batch(t, carState=dict(v=0.0, gear="park"), carControl=dict(lat=False))
                  for t in synth.ticks(100, 100, T0)]
        out = synth.summaries(synth.run(Pipeline(), parked))
        self.assertEqual(set(out[0]) - PIPELINE_FIELDS - {"health"}, set().union(*FLAT_FIELDS.values()))
        self.assertEqual((out[0]["brakeEpisodes"], out[0]["lcStarts"], out[0]["lcTruncated"]), (0, 0, 0))

    def test_the_drive_end_fields(self):
        p = Pipeline()
        synth.run(p, drive(300))
        end = p.drive_end_fields()
        self.assertEqual(set(end), {"windows", "totals", "failed", "truncatedLaneChanges"})
        self.assertEqual(set(end["totals"]), {"windowS", "unobservedS", "steeringS", "movingS"})
        self.assertEqual((end["failed"], end["truncatedLaneChanges"]), ([], 0))

    def test_only_the_known_message_types_are_sent(self):
        messages = synth.run(Pipeline(), drive(900, stop_at_s=20.0, lane_change_at_s=30.0, limit_at_s=50.0))
        self.assertEqual({kind for kind, _ in messages}, RECORD_TYPES)


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

    def test_every_third_sample_missing_changes_no_count(self):
        full = drive(900, stop_at_s=20.0, lane_change_at_s=40.0)
        thinned = [b for i, b in enumerate(full) if i % 3 != 2]
        for batches in (full, thinned):
            messages = synth.run(Pipeline(), batches)
            out = synth.summaries(messages)
            self.assertEqual(sum(s["brakeEpisodes"] for s in out), 1)
            self.assertEqual((sum(s["lcStarts"] for s in out), sum(s["lcCompleted"] for s in out),
                              sum(s["lcTruncated"] for s in out)), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
