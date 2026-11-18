"""Synthetic deterministic tests. Never connect to truck or private S3."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "torque_command_replay", Path(__file__).with_name("torque_command_replay.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def r(ns, stream, **kw):
    return {"monoTimeNs": ns, "seconds": 1800 + ns / 1e9,
            "stream": stream, "valid": True, **kw}


class ModelLimitTests(unittest.TestCase):
    def test_measured_clip_then_rate_clip(self):
        # Target 350, EPS 50 => measured high 130, rate 100->106.
        p = mod.predict_command(1, 100, 50)
        self.assertEqual(p["targetNative"], 350)
        self.assertEqual(p["measHigh"], 130)
        self.assertEqual(p["candidateNative"], 106)
        self.assertTrue(p["measurementClipped"])
        self.assertTrue(p["rateClipped"])

    def test_wind_down_and_reverse(self):
        self.assertEqual(mod.predict_command(-1, 200, -10)["candidateNative"], 194)
        self.assertEqual(mod.predict_command(1, -200, 10)["candidateNative"], -194)
        self.assertEqual(mod.predict_command(0, -1, 0)["candidateNative"], 0)

    def test_measured_torque_excess_and_invalid_inputs(self):
        self.assertEqual(mod.predict_command(1, 348, 999)["candidateNative"], 350)
        self.assertEqual(mod.predict_command(-1, -348, -999)["candidateNative"], -350)
        for request in (float("nan"), 2.0, None):
            with self.assertRaises(ValueError):
                mod.predict_command(request, 0, 0)


class CausalReplayTests(unittest.TestCase):
    def test_matches_active_transition_with_past_only_sources(self):
        rows = [r(0, "carOutput", outputTorqueCan=100),
                r(5_000_000, "carControl", requestedTorque=1, carControlLatActive=True),
                r(6_000_000, "carState", steeringTorqueEps=50),
                r(10_000_000, "carOutput", outputTorqueCan=106)]
        counts = mod.analyze(rows)["counts"]
        self.assertEqual(counts["modelEligibleChangedSamples"], 1)
        self.assertEqual(counts["matchesHypotheticalActiveUpdate"], 1)
        self.assertEqual(counts["sourceMeasurementBoundActive"], 1)
        self.assertEqual(counts["sourceRateBoundActive"], 1)
        self.assertEqual(counts["capMatched"], 1)
        json.dumps(mod.analyze(rows), allow_nan=False)

    def test_future_request_not_used_and_held_output_not_scored(self):
        rows = [r(0, "carOutput", outputTorqueCan=100),
                r(10_000_000, "carOutput", outputTorqueCan=106),
                r(20_000_000, "carOutput", outputTorqueCan=106),
                r(11_000_000, "carControl", requestedTorque=1, carControlLatActive=True),
                r(11_000_000, "carState", steeringTorqueEps=50)]
        counts = mod.analyze(rows)["counts"]
        self.assertEqual(counts["changedOutputSamples"], 1)
        self.assertEqual(counts["heldOutputSamples"], 1)
        self.assertEqual(counts["missingFreshSources"], 1)
        self.assertNotIn("modelEligibleChangedSamples", counts)

    def test_invalid_message_and_stale_sources(self):
        rows = [r(0, "carOutput", outputTorqueCan=0),
                r(10_000_000, "carState", steeringTorqueEps=50, valid=False),
                r(10_000_000, "carControl", requestedTorque=1, carControlLatActive=True),
                r(20_000_000, "carOutput", outputTorqueCan=6)]
        report = mod.analyze(rows)
        self.assertEqual(report["counts"]["missingFreshSources"], 1)
        self.assertEqual(report["rejectedRows"]["invalidMessage"], 1)

    def test_inactive_not_classified_as_limited(self):
        rows = [r(0, "carOutput", outputTorqueCan=10),
                r(0, "carState", steeringTorqueEps=0),
                r(0, "carControl", requestedTorque=1, carControlLatActive=False),
                r(10_000_000, "carOutput", outputTorqueCan=0)]
        self.assertEqual(mod.analyze(rows)["counts"]["inactiveOrUnknownRequest"], 1)

    def test_bad_params_and_no_private_metadata(self):
        for kwargs in ({"start": -1}, {"end": float("inf")},
                       {"max_source_age_ms": 0}, {"near_normalized": 2}):
            with self.assertRaises(ValueError):
                mod.analyze([], **kwargs)
        row = r(0, "carOutput", outputTorqueCan=0, VIN="sensitiveVin", GPS="sensitiveGPS")
        report = json.dumps(mod.analyze([row]))
        self.assertNotIn("sensitiveVin", report)
        self.assertNotIn("sensitiveGPS", report)
        self.assertNotIn("monoTimeNs", report)


if __name__ == "__main__":
    unittest.main()
