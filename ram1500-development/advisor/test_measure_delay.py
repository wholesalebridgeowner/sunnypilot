"""The learned steering delay: the block of each window and the delay record of a drive."""
import unittest
from types import SimpleNamespace as ns

import measure_set
import shared_rules
import synth
from measure_delay import LearnedDelay, delay_record, delay_status
from pipeline import Pipeline, PipelineConfig
from synth import SEC

T0 = 1_000 * SEC
FULL = 0.3456789012345678          # an invented value with the precision a learned delay really has


def drive(n, delay_at=None, period_ms=100.0):
    """A straight drive of `n` ticks. `delay_at(i)` gives the liveDelay message of tick `i`: a dict of the
    builder's arguments, a ready-made message, or None for no message on that tick."""
    def change(i, t_ns):
        msg = delay_at(i) if delay_at else None
        return None if msg is None else {"liveDelay": msg}
    return synth.drive(synth.ticks(n, period_ms, T0), change)


def run(batches, **config):
    p = Pipeline(PipelineConfig(measures=(LearnedDelay,), **config))
    return synth.summaries(synth.run(p, batches)), p


def every(k, **fields):
    """A liveDelay message on every k-th tick (the learner sends 4 a second; the agent ticks 10 times)."""
    return lambda i: dict(fields) if i % k == 0 else None


class BlockTests(unittest.TestCase):
    def test_it_is_an_owner_facing_measure_of_the_build_that_listens_to_the_learner(self):
        self.assertIn(LearnedDelay, measure_set.MEASURES)
        self.assertEqual((LearnedDelay.name, LearnedDelay.owner_facing, LearnedDelay.streams),
                         ("delay", True, ("liveDelay",)))

    def test_newest_value_smallest_and_largest_of_the_window(self):
        values = {0: 0.3501, 100: 0.3487, 200: 0.3512, 300: 0.3499, 650: 0.3505, 900: 0.3507}
        out, _ = run(drive(1300, lambda i: dict(delay=values[i]) if i in values else None))
        self.assertEqual(out[0]["delay"], {"valueS": 0.3499, "minS": 0.3487, "maxS": 0.3512, "status": "estimated",
                                           "blocks": 30})
        self.assertEqual((out[1]["delay"]["valueS"], out[1]["delay"]["minS"], out[1]["delay"]["maxS"]),
                         (0.3507, 0.3505, 0.3507))

    def test_values_are_sent_with_four_decimals(self):
        out, _ = run(drive(100, every(3, delay=FULL)))
        self.assertEqual(out[0]["delay"]["valueS"], 0.3457)
        self.assertEqual((out[0]["delay"]["minS"], out[0]["delay"]["maxS"]), (0.3457, 0.3457))

    def test_no_block_before_the_first_message_of_the_drive(self):
        out, _ = run(drive(1900, lambda i: dict(delay=0.33) if i >= 700 and i % 3 == 0 else None))
        self.assertEqual(["delay" in s for s in out], [False, True, True, True])

    def test_a_window_without_a_new_message_carries_the_newest_value(self):
        out, _ = run(drive(1900, lambda i: dict(delay=0.35 + i / 1e5, blocks=12) if i < 300 and i % 3 == 0 else None))
        self.assertEqual(out[0]["delay"], {"valueS": 0.353, "minS": 0.35, "maxS": 0.353, "status": "estimated",
                                           "blocks": 12})
        for later in out[1:]:
            self.assertEqual(later["delay"], {"valueS": 0.353, "minS": 0.353, "maxS": 0.353, "status": "estimated",
                                              "blocks": 12})

    def test_status_and_blocks_are_those_of_the_newest_message(self):
        def at(i):
            if i % 3:
                return None
            return dict(delay=0.4, status="unestimated", blocks=2) if i < 300 else dict(delay=0.351, blocks=31)
        out, _ = run(drive(500, at))
        self.assertEqual(out[0]["delay"], {"valueS": 0.351, "minS": 0.351, "maxS": 0.4, "status": "estimated",
                                           "blocks": 31})

    def test_the_value_is_sent_whatever_the_status(self):
        # The status is sent beside it; only the delay record of a drive decides what is trusted.
        for status in ("unestimated", "invalid"):
            with self.subTest(status=status):
                out, _ = run(drive(100, every(3, delay=0.4, status=status)))
                self.assertEqual(out[0]["delay"], {"valueS": 0.4, "minS": 0.4, "maxS": 0.4, "status": status,
                                                   "blocks": 30})

    def test_a_table_that_holds_the_value_only_gives_no_status(self):
        out, p = run(drive(100, lambda i: ns(lateralDelay=0.3521) if i % 3 == 0 else None))
        self.assertEqual(out[0]["delay"], {"valueS": 0.3521, "minS": 0.3521, "maxS": 0.3521, "status": None,
                                           "blocks": None})
        self.assertEqual(p.failed, [])

    def test_a_message_without_a_finite_value(self):
        def at(i):
            if i % 3:
                return None
            return dict(delay=0.33) if i < 300 else ns(lateralDelay=float("nan"), status="invalid", validBlocks=0)
        out, p = run(drive(500, at))
        self.assertEqual(out[0]["delay"], {"valueS": None, "minS": 0.33, "maxS": 0.33, "status": "invalid",
                                           "blocks": 0})
        out, _ = run(drive(100, lambda i: ns(lateralDelay="0.33") if i % 3 == 0 else None))
        self.assertEqual(out[0]["delay"], {"valueS": None, "minS": None, "maxS": None, "status": None, "blocks": None})
        self.assertEqual(p.failed, [])

    def test_the_block_books_to_the_window_of_the_sample_that_brought_the_message(self):
        # One message, stamped just before the first window ends and delivered with the first Sample of the next.
        batches = drive(700)
        batches[600]["liveDelay"] = (T0 + 60 * SEC - 5_000_000, synth.live_delay(0.3512))
        out, _ = run(batches)
        self.assertNotIn("delay", out[0])
        self.assertEqual(out[1]["delay"]["valueS"], 0.3512)

    def test_a_message_left_over_from_before_the_drive_is_not_used(self):
        batches = drive(100)
        batches[0]["liveDelay"] = (T0 - 20 * SEC, synth.live_delay(0.2))
        out, p = run(batches)
        self.assertNotIn("delay", out[0])
        self.assertIsNone(p.drive_end_fields()["delay"]["learnedS"])

    def test_a_stall_or_the_end_of_the_drive_changes_nothing(self):
        times = synth.ticks(300, 100, T0)
        times = times[:150] + [t + 3 * SEC for t in times[150:]]
        batches = [synth.batch(t, liveDelay=dict(delay=0.35 + i / 1e5)) for i, t in enumerate(times)]
        out, _ = run(batches)
        self.assertEqual(out[0]["health"]["gapN"], 1)
        self.assertEqual(out[0]["delay"], {"valueS": 0.353, "minS": 0.35, "maxS": 0.353, "status": "estimated",
                                           "blocks": 30})

    def test_every_drive_starts_without_a_value(self):
        run(drive(100, every(3, delay=0.33)))
        out, _ = run(drive(100))
        self.assertNotIn("delay", out[0])

    def test_it_sends_no_flat_field_and_no_record(self):
        p = Pipeline(PipelineConfig(measures=(LearnedDelay,)))
        messages = synth.run(p, drive(700, every(3, delay=0.33)))
        self.assertEqual({kind for kind, _ in messages}, {"summary"})
        for s in synth.summaries(messages):
            self.assertEqual(set(s), {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health", "delay"})


class StatusTests(unittest.TestCase):
    def test_the_three_statuses_by_name_and_by_number(self):
        for given, token in (("unestimated", "unestimated"), ("estimated", "estimated"), ("invalid", "invalid"),
                             (0, "unestimated"), (1, "estimated"), (2, "invalid")):
            with self.subTest(given=given):
                self.assertEqual(delay_status(given), token)

    def test_an_enum_value_is_read_by_its_name(self):
        class Enum:
            def __str__(self):
                return "estimated"
        self.assertEqual(delay_status(Enum()), "estimated")

    def test_anything_else_is_unknown(self):
        for given in (None, "", "learned", 3, -1, True, 1.0, "ESTIMATED", b"estimated"):
            with self.subTest(given=given):
                self.assertIsNone(delay_status(given))


class RecordTests(unittest.TestCase):
    """The delay object of drive_start and drive_end."""
    KEYS = {"learnedS", "estimateS", "estimateStdS", "validBlocks", "status", "cacheS", "baseActuatorS", "outOfBand"}

    def test_an_estimated_plausible_value_is_the_learned_delay(self):
        record = delay_record(synth.live_delay(delay=FULL, estimate=0.3512345, std=0.0123456, blocks=30),
                              cache_s=0.4, base_actuator_s=0.2)
        self.assertEqual(record, {"learnedS": 0.3457, "estimateS": 0.3512, "estimateStdS": 0.0123, "validBlocks": 30,
                                  "status": "estimated", "cacheS": 0.4, "baseActuatorS": 0.2, "outOfBand": False})

    def test_the_cache_is_never_the_learned_delay(self):
        # The cache holds a stale fixed total; the learner's own message says 0.3457.
        self.assertEqual(delay_record(synth.live_delay(delay=0.3457), cache_s=0.40)["learnedS"], 0.3457)
        self.assertIsNone(delay_record(None, cache_s=0.33)["learnedS"])
        self.assertIsNone(delay_record(synth.live_delay(delay=0.4, status="unestimated"), cache_s=0.33)["learnedS"])

    def test_a_value_that_is_not_estimated_is_not_trusted(self):
        for status in ("unestimated", "invalid", None, "learned"):
            with self.subTest(status=status):
                record = delay_record(synth.live_delay(delay=0.33, status=status))
                self.assertIsNone(record["learnedS"])
                self.assertIs(record["outOfBand"], False)
                self.assertEqual(record["estimateS"], 0.33)            # the other fields are still recorded

    def test_an_estimated_value_must_be_plausible(self):
        self.assertEqual((shared_rules.DELAY_TRUST_MIN_S, shared_rules.DELAY_TRUST_MAX_S), (0.05, 0.95))
        for value, learned in ((0.05, None), (0.0501, 0.0501), (0.9499, 0.9499), (0.95, None), (-0.1, None),
                               (float("nan"), None), (float("inf"), None)):
            with self.subTest(value=value):
                self.assertEqual(delay_record(synth.live_delay(delay=value))["learnedS"], learned)

    def test_out_of_band(self):
        self.assertEqual((shared_rules.DELAY_BAND_MIN_S, shared_rules.DELAY_BAND_MAX_S), (0.28, 0.38))
        for value, out in ((0.40, True), (0.2799, True), (0.28, False), (0.33, False), (0.38, False),
                           (0.3801, True)):
            with self.subTest(value=value):
                record = delay_record(synth.live_delay(delay=value))
                self.assertEqual((record["learnedS"], record["outOfBand"]), (value, out))

    def test_no_message_or_one_that_cannot_be_read_gives_nulls(self):
        for msg in (None, object(), "liveDelay", b"\x00\x01", {"other": 1}):
            with self.subTest(msg=msg):
                record = delay_record(msg, cache_s=0.4, base_actuator_s=0.2)
                self.assertEqual(set(record), self.KEYS)
                self.assertEqual(record, {"learnedS": None, "estimateS": None, "estimateStdS": None,
                                          "validBlocks": None, "status": None, "cacheS": 0.4, "baseActuatorS": 0.2,
                                          "outOfBand": False})

    def test_cache_and_base_delay_are_recorded_only_as_plain_numbers(self):
        for given, sent in ((0.2, 0.2), (0.3456789012345678, 0.3457), (1, 1.0), (None, None), (True, None),
                            ("0.2", None), (float("nan"), None)):
            with self.subTest(given=given):
                record = delay_record(None, cache_s=given, base_actuator_s=given)
                self.assertEqual((record["cacheS"], record["baseActuatorS"]), (sent, sent))

    def test_valid_blocks_is_a_whole_number_or_null(self):
        for given, sent in ((30, 30), (0, 0), (30.0, None), (True, None), (-1, None), ("30", None), (None, None)):
            with self.subTest(given=given):
                self.assertEqual(delay_record(synth.live_delay(blocks=given))["validBlocks"], sent)

    def test_a_dict_reads_like_a_message(self):
        record = delay_record({"lateralDelay": 0.3457, "status": "estimated", "validBlocks": 30,
                               "lateralDelayEstimate": 0.33, "lateralDelayEstimateStd": 0.01})
        self.assertEqual((record["learnedS"], record["status"], record["validBlocks"]), (0.3457, "estimated", 30))


class DriveEndTests(unittest.TestCase):
    def test_the_record_of_the_newest_message(self):
        _, p = run(drive(700, lambda i: dict(delay=0.3457 + i / 1e6, estimate=0.33, std=0.01, blocks=30 + i // 100)
                         if i % 3 == 0 else None))
        self.assertEqual(p.drive_end_fields()["delay"],
                         {"learnedS": 0.3464, "estimateS": 0.33, "estimateStdS": 0.01, "validBlocks": 36,
                          "status": "estimated", "cacheS": None, "baseActuatorS": None, "outOfBand": False})

    def test_a_drive_without_a_message(self):
        _, p = run(drive(100))
        self.assertEqual(p.drive_end_fields()["delay"],
                         {"learnedS": None, "estimateS": None, "estimateStdS": None, "validBlocks": None,
                          "status": None, "cacheS": None, "baseActuatorS": None, "outOfBand": False})

    def test_the_learner_lost_its_estimate_during_the_drive(self):
        _, p = run(drive(700, lambda i: (dict(delay=0.33) if i < 300 else dict(delay=0.33, status="unestimated"))
                         if i % 3 == 0 else None))
        record = p.drive_end_fields()["delay"]
        self.assertEqual((record["learnedS"], record["status"]), (None, "unestimated"))


if __name__ == "__main__":
    unittest.main()
