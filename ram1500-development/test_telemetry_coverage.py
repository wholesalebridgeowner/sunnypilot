"""Synthetic-only tests; no real telemetry or device access."""
import importlib.util
from pathlib import Path
import unittest

p = Path(__file__).with_name("telemetry_coverage.py")
spec = importlib.util.spec_from_file_location("telemetry_coverage", p)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def rec(t, stream, valid="True", **fields):
    return {"monoTimeNs": str(t), "stream": stream, "valid": valid, **fields}


class CoverageTests(unittest.TestCase):
    def test_event_asof_is_past_only_even_if_future_closer(self):
        report = a.analyze([
            rec(1000000000, "carState"),
            rec(1200000000, "onroadEvents", event="steerSaturated"),
            rec(1201000000, "carState"),
            rec(1100000000, "carControl"),
        ], max_age_ms=150)
        source = report["steerSaturatedEpisodes"][0]["sources"]
        self.assertEqual(source["carState"]["precedingAgeMs"], 200)
        self.assertFalse(source["carState"]["withinAgeThreshold"])
        self.assertEqual(source["carControl"]["precedingAgeMs"], 100)
        self.assertTrue(source["carControl"]["withinAgeThreshold"])
        self.assertIsNone(source["carOutput"]["precedingAgeMs"])

    def test_duplicate_and_gap_are_reported_without_nan(self):
        r = a.analyze([rec(1000000000, "carState"), rec(1000000000, "carState"),
                       rec(1100000000, "carState"), rec(2000000000, "carState")])
        stream = r["streamCoverage"]["carState"]
        self.assertEqual(stream["duplicateTimestampSamples"], 1)
        self.assertEqual(stream["maxInterSampleMs"], 900)
        self.assertEqual(stream["gapsOverThreshold"], 1)
        self.assertEqual(stream["medianInterSampleMs"], 500)
        self.assertIsNone(r["streamCoverage"]["carOutput"]["maxInterSampleMs"])

    def test_exact_rank_percentile_for_odd_sample_count(self):
        r = a.analyze([rec(1000000000, "carState"), rec(1100000000, "carState"),
                       rec(1200000000, "carState"), rec(1300000000, "carState")])
        self.assertEqual(r["streamCoverage"]["carState"]["medianInterSampleMs"], 100)
        self.assertEqual(r["streamCoverage"]["carState"]["p95InterSampleMs"], 100)

    def test_invalid_and_unknown_validity_are_separate(self):
        r = a.analyze([rec(1, "carState", valid="False"),
                       rec(2, "carState", valid=""),
                       rec(3, "carState"),
                       rec(4, "onroadEvents", valid="False", event="steerSaturated"),
                       rec("NaN", "carOutput")])
        s = r["streamCoverage"]["carState"]
        self.assertEqual(s["inputRows"], 3)
        self.assertEqual(s["usableSamples"], 2)
        self.assertEqual(s["unknownValidityRows"], 1)
        self.assertEqual(r["steerSaturatedSampleCount"], 0)
        self.assertEqual(r["discardedRows"], {"invalidEvent":1, "invalidTimestamp":1})

    def test_event_episodes_and_unit_age(self):
        r = a.analyze([rec(1000000000, "onroadEvents", event="steerSaturated"),
                       rec(1050000000, "onroadEvents", event="steerSaturated"),
                       rec(2200000000, "onroadEvents", event="steerSaturated")])
        self.assertEqual(r["steerSaturatedSampleCount"], 3)
        self.assertEqual(r["steerSaturatedEpisodeCount"], 2)
        self.assertEqual(r["steerSaturatedEpisodes"][0]["eventSamplesInEpisode"], 2)

    def test_reject_invalid_thresholds(self):
        for age in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                a.analyze([], max_age_ms=age)

if __name__ == "__main__":
    unittest.main()
