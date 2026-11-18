"""Synthetic-only tests: no real vehicle logs or CAN commands."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("eps_feedback_lag", Path(__file__).with_name("eps_feedback_lag.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def row(ns, stream, **cols):
    return {"monoTimeNs": str(ns), "stream": stream, "valid": "True", **cols}


class EpsLagAuditTests(unittest.TestCase):
    def test_lagged_same_sign_is_separate_from_at_time_match(self):
        t = 1_000_000_000
        rows = [
            row(t-80_000_000, "carControl", carControlLatActive="True"),
            row(t-100_000_000, "carOutput", outputTorqueCan="130"),
            row(t, "carState", vEgo_mps="7", steeringTorque="10", steeringTorqueEps="90"),
        ]
        report = mod.analyze(rows, delays_ms=(0, 100), join_skew_ms=50)
        b = report["speedBands"]["low_5_to_10"]
        self.assertEqual(b["eligibleActiveLowDriverSamples"], 1)
        self.assertEqual(b["delaysMs"]["0"]["matchedOutput"], 0)
        self.assertEqual(b["delaysMs"]["100"]["sameSignFeedback"], 1)
        self.assertIn("not proof", " ".join(report["caveats"]))
        json.dumps(report, allow_nan=False)

    def test_future_command_does_not_leak_back(self):
        t = 2_000_000_000
        rows = [
            row(t-10_000_000, "carControl", carControlLatActive="True"),
            row(t+1, "carOutput", outputTorqueCan="350"),
            row(t, "carState", vEgo_mps="0", steeringTorque="0", steeringTorqueEps="300"),
        ]
        b = mod.analyze(rows, delays_ms=(0,))["speedBands"]["reported_zero"]
        self.assertEqual(b["matchedOutput"] if "matchedOutput" in b else b["delaysMs"]["0"]["matchedOutput"], 0)
        self.assertEqual(b["eligibleActiveLowDriverSamples"], 1)

    def test_inactive_and_driver_effort_excluded(self):
        t = 3_000_000_000
        rows = [
            row(t-10_000_000, "carControl", carControlLatActive="False"),
            row(t-10_000_000, "carOutput", outputTorqueCan="100"),
            row(t, "carState", vEgo_mps="1", steeringTorque="0", steeringTorqueEps="70"),
            row(t+100_000_000, "carControl", carControlLatActive="True"),
            row(t+110_000_000, "carState", vEgo_mps="1", steeringTorque="200", steeringTorqueEps="70"),
        ]
        b = mod.analyze(rows)["speedBands"]["crawl_0p5_to_2"]
        self.assertEqual(b["carStateSamples"], 2)
        self.assertEqual(b["eligibleActiveLowDriverSamples"], 0)

    def test_signed_feedback_and_weak_feedback_distinct(self):
        t = 4_000_000_000
        rows = [
            row(t, "carControl", carControlLatActive="True"),
            row(t, "carOutput", outputTorqueCan="150"),
            row(t, "carState", vEgo_mps="15", steeringTorque="0", steeringTorqueEps="-80"),
            row(t+100_000_000, "carOutput", outputTorqueCan="150"),
            row(t+100_000_000, "carControl", carControlLatActive="True"),
            row(t+100_000_000, "carState", vEgo_mps="15", steeringTorque="0", steeringTorqueEps="10"),
        ]
        b = mod.analyze(rows, delays_ms=(0,))["speedBands"]["above_14p5"]["delaysMs"]["0"]
        self.assertEqual(b["oppositeSignFeedback"], 1)
        self.assertEqual(b["weakFeedback"], 1)
        self.assertEqual(b["commandAboveThreshold"], 2)

    def test_stale_command_and_negative_speed_not_classified(self):
        t = 5_000_000_000
        rows = [
            row(t-200_000_000, "carOutput", outputTorqueCan="300"),
            row(t-10_000_000, "carControl", carControlLatActive="True"),
            row(t, "carState", vEgo_mps="0.1", steeringTorque="0", steeringTorqueEps="100"),
            row(t+1, "carState", vEgo_mps="-0.1", steeringTorque="0", steeringTorqueEps="100"),
        ]
        report = mod.analyze(rows, delays_ms=(0,), join_skew_ms=50)
        self.assertEqual(report["speedBands"]["rolling_below_0p5"]["delaysMs"]["0"]["matchedOutput"], 0)
        self.assertEqual(report["rejectedRows"]["invalidSpeed"], 1)

    def test_invalid_parameters_and_message_rejections(self):
        for invalid in [(float("nan"),), (-1,), (0, 0), ()]:
            with self.assertRaises(ValueError):
                mod.analyze([], delays_ms=invalid)
        for age in (-1, float("inf"), 0):
            with self.assertRaises(ValueError):
                mod.analyze([], join_skew_ms=age)
        r = mod.analyze([row("bad", "carState"), row(10, "carState", valid="False"),
                         row(20, "carOutput", valid="")])
        self.assertEqual(r["rejectedRows"], {"badTimestamp": 1, "invalidMessage": 1, "unknownValidity": 1})


if __name__ == "__main__":
    unittest.main()
