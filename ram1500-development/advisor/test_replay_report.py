"""The replay tool feeds the agent's own pipeline: decoded log events in, the agent's messages out."""
import importlib.util
import json
import random
import unittest
from pathlib import Path

import replay_report
import schema_v2
import synth
from pipeline import Pipeline, PipelineConfig
from replay_report import decimated_batches, header_settings, replay_messages
from synth import MS, SEC
from test_measure_set import drive as eventful_drive
from test_settings_snapshot import BUNDLE

T0 = 1_000 * SEC


def events_of(batches):
    """The (stream, time, message) events of synthetic batches, as a log decoder would hand them over."""
    return [(stream, t_ns, msg) for batch in batches for stream, (t_ns, msg) in batch.items()]


class BatchTests(unittest.TestCase):
    def test_one_batch_per_car_state_with_the_newest_of_each_other_stream_at_or_before_it(self):
        events = [("carControl", T0 - 40 * MS, "c0"), ("carControl", T0 - 5 * MS, "c1"), ("carState", T0, "s0"),
                  ("drivingModelData", T0 + 20 * MS, "m0"), ("carControl", T0 + 95 * MS, "c2"),
                  ("carState", T0 + 100 * MS, "s1"), ("carState", T0 + 200 * MS, "s2"),
                  ("carControl", T0 + 250 * MS, "late")]
        self.assertEqual(list(decimated_batches(events)), [
            {"carControl": (T0 - 5 * MS, "c1"), "carState": (T0, "s0")},
            {"drivingModelData": (T0 + 20 * MS, "m0"), "carControl": (T0 + 95 * MS, "c2"),
             "carState": (T0 + 100 * MS, "s1")},
            {"carState": (T0 + 200 * MS, "s2")}])                      # nothing after the last carState is used

    def test_a_message_with_the_time_of_its_car_state_belongs_to_it(self):
        events = [("carState", T0, "s0"), ("carControl", T0, "c0"), ("carState", T0 + 100 * MS, "s1")]
        self.assertEqual(list(decimated_batches(events))[0], {"carControl": (T0, "c0"), "carState": (T0, "s0")})

    def test_file_order_does_not_matter(self):
        events = events_of(eventful_drive(900, stop_at_s=20.0, lane_change_at_s=30.0, limit_at_s=50.0))
        shuffled = list(events)
        random.Random(3).shuffle(shuffled)
        self.assertEqual(replay_messages(shuffled), replay_messages(events))

    def test_streams_the_agent_does_not_listen_to_are_left_out(self):
        events = [("carState", T0, "s0"), ("livePose", T0, "x"), ("initData", T0 - SEC, "x")]
        self.assertEqual(list(decimated_batches(events)), [{"carState": (T0, "s0")}])


class ReplayTests(unittest.TestCase):
    def test_the_messages_are_those_of_the_pipeline_in_the_agents_envelope(self):
        batches = eventful_drive(900, stop_at_s=20.0, lane_change_at_s=30.0, limit_at_s=50.0)
        pipeline = Pipeline(PipelineConfig.for_decimated_log())
        expected = synth.run(pipeline, batches)
        out = replay_messages(events_of(batches))
        self.assertEqual([(m["type"], m["data"]) for m in out], expected)
        self.assertEqual({m["type"] for m in out}, {"summary", "brake", "lane_change", "limit_event"})
        self.assertEqual([m["id"] for m in out], [f"replay-{i}" for i in range(1, len(out) + 1)])
        for message in out:
            self.assertEqual((message["v"], message["driveId"]), (2, "replay"))
            self.assertEqual(schema_v2.problems(message), [])

    def test_one_summary_per_minute_plus_the_remainder(self):
        out = replay_messages(events_of(synth.drive(synth.ticks(1250, 100, T0))))
        self.assertEqual([m["data"]["n"] for m in out], [600, 600, 50])
        self.assertEqual([m["data"]["latOnS"] for m in out], [60.0, 60.0, 5.0])

    def test_the_slow_streams_of_a_decimated_log_are_not_missing(self):
        # Model and lead messages 0.7 s apart, as a decimated log can hold them: with the limits of a live
        # drive (0.5 s) each would be missing for part of the time.
        def change(i, t_ns):
            return {"skip": ("drivingModelData", "radarState") if i % 7 else ()}
        batches = [synth.batch(t, **change(i, t)) for i, t in enumerate(synth.ticks(700, 100, T0))]
        out = replay_messages(events_of(batches))
        self.assertEqual([m["data"]["health"]["staleS"] for m in out], [{}, {}])
        self.assertEqual(out[0]["data"]["latOnS"], 60.0)
        live = replay_messages(events_of(batches), PipelineConfig())
        self.assertEqual(set(live[0]["data"]["health"]["staleS"]), {"dmd", "rs"})

    def test_a_given_configuration_is_used(self):
        events = events_of(synth.drive(synth.ticks(700, 100, T0)))
        out = replay_messages(events, PipelineConfig(t0_mono_s=1010.0))
        self.assertEqual([m["data"]["tMonoS"] for m in out], [1010.0])

    def test_no_events_no_messages(self):
        self.assertEqual(replay_messages([]), [])

    def test_nothing_of_the_first_version_is_left(self):
        for module in ("metrics",):
            self.assertIsNone(importlib.util.find_spec(module), module)
        source = Path(replay_report.__file__).read_text(encoding="utf-8")
        for gone in ("liveSuggestion", "MinuteSummary", "SampleBuilder", "sample_hz", "live_suggestion"):
            self.assertNotIn(gone, source)
        self.assertFalse(hasattr(replay_report, "summaries_from_events"))

    def test_it_holds_no_measure_and_no_sampling_logic_of_its_own(self):
        source = Path(replay_report.__file__).read_text(encoding="utf-8")
        for own in ("vEgo", "latActive", "WINDOW_S", "DT_CAP_S", "60.0"):
            self.assertNotIn(own, source)


class HeaderTests(unittest.TestCase):
    """The settings of a drive as its log header holds them: raw text values."""
    ENTRIES = {"LagdToggle": b"1", "LagdToggleDelay": b"0.2", "NeuralNetworkLateralControl": b"0",
               "AutoLaneChangeTimer": b"2", "ModelManager_ActiveBundle": json.dumps(BUNDLE).encode(),
               "GitCommit": b"0123456789abcdef0123456789abcdef01234567", "Other": b"x"}

    def test_the_snapshot_of_a_header(self):
        snapshot = header_settings(self.ENTRIES)
        self.assertEqual({k: v for k, v in snapshot.items() if k != "modelBundleId"},
                         {"lagdToggle": True, "lagdToggleDelayS": 0.2, "nnlc": False, "laneChangeTimer": 2,
                          "build": "0123456789ab"})
        self.assertEqual(schema_v2.SETTING_KEYS, set(snapshot))

    def test_the_model_bundle_id_is_the_one_the_agent_gives(self):
        from settings_snapshot import model_bundle_id
        self.assertEqual(header_settings(self.ENTRIES)["modelBundleId"], model_bundle_id(BUNDLE))
        self.assertEqual(header_settings({**self.ENTRIES, "ModelManager_ActiveBundle": b""})["modelBundleId"],
                         "default")

    def test_switches_are_on_only_for_the_value_1(self):
        for raw, value in ((b"1", True), (b"0", False), (b"", False), (b"true", False), ("1", True)):
            with self.subTest(raw=raw):
                self.assertIs(header_settings({**self.ENTRIES, "LagdToggle": raw})["lagdToggle"], value)

    def test_what_is_missing_or_cannot_be_parsed_is_null(self):
        snapshot = header_settings({"LagdToggleDelay": b"fast", "AutoLaneChangeTimer": b"2.5", "GitCommit": b"\xff\xfe"})
        self.assertEqual(snapshot, {"lagdToggle": None, "lagdToggleDelayS": None, "nnlc": None,
                                    "laneChangeTimer": None, "modelBundleId": "default", "build": None})
        self.assertIsNone(header_settings({"LagdToggleDelay": b"nan"})["lagdToggleDelayS"])
        self.assertEqual(header_settings({"AutoLaneChangeTimer": b"-1"})["laneChangeTimer"], -1)


if __name__ == "__main__":
    unittest.main()
