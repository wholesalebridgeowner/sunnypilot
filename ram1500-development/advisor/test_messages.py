"""Schema v2 messages: the envelope, the wire form, the size cap of a summary and the drive records."""
import json
import unittest
from unittest import mock

import messages
import synth
from pipeline import Pipeline, PipelineConfig
from test_measure_set import BLOCKS, T0, road

DRIVE = "0123456789abcdef0123456789abcdef"
PROBE = {"result": "inert", "helperSha": "0123456789ab", "probeMs": 12}
SETTINGS = {"lagdToggle": True, "lagdToggleDelayS": 0.2, "nnlc": False, "laneChangeTimer": 0,
            "modelBundleId": "default", "build": "0123456789ab"}
DELAY = {"learnedS": 0.33, "estimateS": 0.33, "estimateStdS": 0.01, "validBlocks": 30, "status": "estimated",
         "cacheS": 0.33, "baseActuatorS": 0.2, "outOfBand": False}


AGENT_HEALTH = {"cpuPct": 3.1, "cpuPeakPct": 4.2, "rssMb": 21.3, "queue": 0, "dropped": 0, "tickErrors": 0}


def full_summary(delay=False) -> dict:
    """The summary of a window in which every log-only block has something to say, with the agent's own
    health values as a live drive sends them, and with the learned-delay block when asked for."""
    config = PipelineConfig(health_source=lambda: dict(AGENT_HEALTH))
    out = synth.summaries(synth.run(Pipeline(config), road(1300, live_delay=delay)))
    return next(s for s in out if set(BLOCKS) <= set(s) and ("delay" in s) == delay)


def size(event) -> int:
    return len(messages.encode(event))


class EnvelopeTests(unittest.TestCase):
    def test_every_message_has_the_same_five_fields(self):
        event = messages.envelope("summary", DRIVE, "0123456789ab-7", {"w": 0})
        self.assertEqual(event, {"v": 2, "type": "summary", "driveId": DRIVE, "id": "0123456789ab-7",
                                 "data": {"w": 0}})

    def test_wire_form_is_compact_json_with_sorted_keys(self):
        raw = messages.encode(messages.envelope("agent_start", None, "0123456789ab-1", {"restore": "restored"}))
        self.assertEqual(raw, b'{"data":{"restore":"restored"},"driveId":null,"id":"0123456789ab-1",'
                              b'"type":"agent_start","v":2}')

    def test_a_replay_names_its_drive_replay(self):
        self.assertEqual(messages.REPLAY_DRIVE_ID, "replay")


class SizeCapTests(unittest.TestCase):
    def setUp(self):
        self.event = messages.envelope("summary", DRIVE, "0123456789ab-123456", full_summary())

    def test_the_cap_is_2048_bytes_and_a_full_summary_is_not_touched(self):
        self.assertEqual(messages.SUMMARY_CAP_BYTES, 2048)
        self.assertLess(size(self.event), 2048)
        self.assertIs(messages.fit_summary(self.event), self.event)
        self.assertNotIn("trimmed", self.event["data"])

    def test_blocks_go_in_the_stated_order_until_it_fits(self):
        self.assertEqual(messages.TRIM_ORDER, ("lag", "curve", "ay", "swings", "lane"))
        whole = size(self.event)
        seen = []
        for cap in range(whole, 0, -1):
            out = messages.fit_summary(self.event, cap)
            trimmed = out["data"].get("trimmed", [])
            if trimmed != (seen[-1] if seen else []):
                seen.append(trimmed)
            self.assertEqual(trimmed, list(messages.TRIM_ORDER[:len(trimmed)]))     # always a head of the order
            for name in BLOCKS:
                self.assertEqual(name in out["data"], name not in trimmed)
            if len(trimmed) < 5:
                self.assertLessEqual(size(out), cap)
            if len(trimmed) == 5:
                break
        self.assertEqual(seen, [["lag"], ["lag", "curve"], ["lag", "curve", "ay"], ["lag", "curve", "ay", "swings"],
                                ["lag", "curve", "ay", "swings", "lane"]])

    def test_a_cap_of_900_bytes(self):
        with mock.patch.object(messages, "SUMMARY_CAP_BYTES", 900):
            out = messages.fit_summary(self.event)
        self.assertLessEqual(size(out), 900)
        self.assertEqual(out["data"]["trimmed"], ["lag", "curve", "ay", "swings", "lane"])
        self.assertFalse(set(BLOCKS) & set(out["data"]))

    def test_with_the_learned_delay_block_the_least_a_summary_can_be_is_over_900_bytes(self):
        # The delay block is never removed, so with it a summary cannot get under about 915 bytes: a lowered
        # cap that every summary can meet is 1,000.
        event = messages.envelope("summary", DRIVE, "0123456789ab-123456", full_summary(delay=True))
        self.assertGreater(size(messages.fit_summary(event, 900)), 900)
        out = messages.fit_summary(event, 1000)
        self.assertLessEqual(size(out), 1000)
        self.assertEqual(out["data"]["trimmed"], ["lag", "curve", "ay", "swings", "lane"])
        self.assertEqual(out["data"]["delay"], event["data"]["delay"])
        self.assertLess(size(event), 1600)                             # all five blocks, health and delay: untrimmed

    def test_the_names_of_what_went_count_toward_the_size(self):
        # A cap that the summary would meet without its lag block, but not once `trimmed` names it.
        without_lag = dict(self.event, data={k: v for k, v in self.event["data"].items() if k != "lag"})
        cap = size(without_lag) + 5
        out = messages.fit_summary(self.event, cap)
        self.assertEqual(out["data"]["trimmed"], ["lag", "curve"])
        self.assertLessEqual(size(out), cap)

    def test_nothing_else_is_ever_removed(self):
        out = messages.fit_summary(self.event, 10)                     # a cap nothing can meet
        self.assertEqual(out["data"]["trimmed"], list(messages.TRIM_ORDER))
        kept = {k: v for k, v in self.event["data"].items() if k not in BLOCKS}
        self.assertEqual({k: v for k, v in out["data"].items() if k != "trimmed"}, kept)
        self.assertIn("health", out["data"])
        self.assertIn("steeringS", out["data"])

    def test_the_summary_handed_in_is_left_as_it_was(self):
        before = json.dumps(self.event, sort_keys=True)
        messages.fit_summary(self.event, 900)
        self.assertEqual(json.dumps(self.event, sort_keys=True), before)

    def test_a_block_that_is_absent_is_not_named(self):
        data = {k: v for k, v in self.event["data"].items() if k not in ("lag", "ay")}
        out = messages.fit_summary(dict(self.event, data=data), 900)
        self.assertEqual(out["data"]["trimmed"], ["curve", "swings", "lane"])

    def test_only_a_summary_is_ever_trimmed(self):
        record = messages.envelope("lane_change", DRIVE, "0123456789ab-9", {"lane": {"n": 1}, "lag": {"n": 2}})
        self.assertIs(messages.fit(record, 10), record)
        summary = messages.fit(self.event, 900)
        self.assertEqual(summary["data"]["trimmed"], list(messages.TRIM_ORDER))
        self.assertIs(messages.fit(self.event), self.event)


class DriveRecordTests(unittest.TestCase):
    def test_drive_start(self):
        data = messages.drive_start_data(resumed=False, settings=SETTINGS, delay=DELAY, version="2.0",
                                         measures=["steering", "lane"], apply_enabled=False)
        self.assertEqual(data, {"resumed": False, "settings": SETTINGS, "delay": DELAY,
                                "agent": {"version": "2.0", "measures": ["steering", "lane"], "applyEnabled": False}})

    def test_drive_ready(self):
        data = messages.drive_ready_data(probe=PROBE, nnlc_model="ram", nnlc_fuzzy=False)
        self.assertEqual(data, {"delayProbe": PROBE, "nnlcModel": "ram", "nnlcFuzzy": False})

    def test_a_replay_says_the_probe_was_not_run(self):
        data = messages.drive_ready_data(probe=messages.PROBE_NOT_RUN, nnlc_model=None, nnlc_fuzzy=None)
        self.assertEqual(data["delayProbe"], {"result": "not_run", "helperSha": None, "probeMs": 0})

    def test_drive_end_takes_what_the_pipeline_knows_and_adds_the_rest(self):
        p = Pipeline()
        synth.run(p, synth.drive(synth.ticks(700, 100, T0)))
        fields = p.drive_end_fields()
        data = messages.drive_end_data(fields, listening=True, restore="nothing_to_restore", settings=SETTINGS,
                                       delay=DELAY, probe=None)
        self.assertEqual(set(data), {"windows", "listening", "restore", "failed", "totals", "settings", "delay",
                                     "delayProbe", "truncatedLaneChanges"})
        self.assertEqual((data["windows"], data["listening"], data["restore"], data["failed"]),
                         (2, True, "nothing_to_restore", []))
        self.assertEqual(set(data["totals"]), {"windowS", "unobservedS", "steeringS", "movingS"})
        self.assertEqual((data["settings"], data["delay"], data["delayProbe"]), (SETTINGS, DELAY, None))
        self.assertEqual(data["truncatedLaneChanges"], 0)

    def test_drive_end_of_a_drive_that_was_never_listened_to(self):
        data = messages.drive_end_data(Pipeline().drive_end_fields(), listening=False, restore="restored",
                                       settings=SETTINGS, delay=DELAY, probe=None)
        self.assertEqual((data["windows"], data["listening"], data["truncatedLaneChanges"]), (0, False, 0))
        self.assertEqual(data["totals"], {"windowS": 0.0, "unobservedS": 0.0, "steeringS": 0.0, "movingS": 0.0})


if __name__ == "__main__":
    unittest.main()
