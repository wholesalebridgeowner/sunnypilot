"""Synthetic-only fixture tests for the passive Ram low-speed steering audit."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).parent

def load(module, filename):
    spec = importlib.util.spec_from_file_location(module, ROOT / filename)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

audit = load("ram_low_speed", "low_speed_report.py")
route = load("ram_route_exporter", "analyze_route.py")


def row(t, stream, **values):
    return {"monoTimeNs": str(t), "stream": stream, "valid": "True", **values}


class LowSpeedAuditTests(unittest.TestCase):
    def test_true_lateral_request_and_nonzero_can_output_at_standstill(self):
        base = 2_000_000_000
        rows = [
            row(base - 10_000_000, "carControl", carControlLatActive="True"),
            row(base - 5_000_000, "carOutput", outputTorqueCan="120"),
            row(base, "carState", vEgo_mps="0"),
        ]
        report = audit.analyze(rows)
        b = report["bins"]["standstill_below_0p5_mps"]
        self.assertEqual(b["speedSamples"], 1)
        self.assertEqual(b["carControlLatActiveTrue"], 1)
        self.assertEqual(b["nonzeroRequestedCanOutputSamples"], 1)
        self.assertEqual(b["activeAndNonzeroOutputSamples"], 1)
        self.assertEqual(sum(s["speedSamples"] for s in report["bins"].values()), 1)
        # Output alone establishes no authorization or EPS response.
        self.assertIn("NOT measured EPS actuation", " ".join(report["caveats"]))

    def test_future_output_and_future_active_do_not_leak_backward(self):
        t = 10_000_000_000
        rows = [
            row(t, "carState", vEgo_mps="0.1"),
            row(t + 1, "carControl", carControlLatActive="True"),
            row(t + 1, "carOutput", outputTorqueCan="350"),
        ]
        b = audit.analyze(rows)["bins"]["standstill_below_0p5_mps"]
        self.assertEqual(b["carControlMatched"], 0)
        self.assertEqual(b["carOutputMatched"], 0)
        self.assertEqual(b["activeAndNonzeroOutputSamples"], 0)

    def test_stale_output_cannot_be_used_as_zero_or_nonzero(self):
        t = 10_000_000_000
        rows = [
            row(t - 151_000_000, "carOutput", outputTorqueCan="330"),
            row(t - 149_000_000, "carControl", carControlLatActive="False"),
            row(t, "carState", vEgo_mps="1.1"),
        ]
        b = audit.analyze(rows)["bins"]["crawl_0p5_to_2_mps"]
        self.assertEqual(b["carControlMatched"], 1)
        self.assertEqual(b["carControlLatActiveFalse"], 1)
        self.assertEqual(b["carOutputMatched"], 0)
        self.assertEqual(b["nonzeroOutputWithInactiveLatSamples"], 0)

    def test_boundaries_invalid_flags_and_nonfinite_speeds(self):
        rows = [row(t, "carState", vEgo_mps=str(v)) for t, v in enumerate([0.5, 2.0, 14.5, -1.0, float("nan")], 1)]
        rows.append(row(6, "carState", vEgo_mps="0", valid="False"))
        rows.append(row("broken", "carControl", carControlLatActive="True"))
        result = audit.analyze(rows)
        self.assertEqual([result["bins"][key]["speedSamples"] for key, _, _ in audit.SPEED_BINS], [0, 1, 1, 1])
        self.assertEqual(result["rejectedRows"], {"invalidTimestamp": 1, "invalidMessage": 1, "invalidSpeed": 2})
        json.dumps(result, allow_nan=False)

    def test_unknown_active_stays_unknown_and_deadband_is_configurable(self):
        t = 1_000_000_000
        rows = [
            row(t - 100_000_000, "carControl", carControlLatActive=""),
            row(t - 10_000_000, "carOutput", outputTorqueCan="0.1"),
            row(t, "carState", vEgo_mps="3"),
        ]
        result = audit.analyze(rows, command_deadband=0.2)
        b = result["bins"]["low_2_to_14p5_mps"]
        self.assertEqual(b["carControlLatActiveUnknown"], 1)
        self.assertEqual(b["nonzeroRequestedCanOutputSamples"], 0)
        with self.assertRaises(ValueError):
            audit.analyze(rows, max_age_ms=0)
        with self.assertRaises(ValueError):
            audit.analyze(rows, max_age_ms=float("nan"))
        with self.assertRaises(ValueError):
            audit.analyze(rows, command_deadband=-1)

    def test_original_exporter_preserves_lateral_active_flag(self):
        class Msg:
            logMonoTime = 100
            valid = True
            carControl = NS(actuators=NS(torque=0.3, steeringAngleDeg=0), latActive=True)
        record = route.timeline(Msg(), "carControl")
        self.assertIs(record["carControlLatActive"], True)
        self.assertIn("carControlLatActive", route.COLUMNS)
        class OutputMsg:
            logMonoTime = 110
            valid = True
            carOutput = NS(actuatorsOutput=NS(torque=0.3, steeringAngleDeg=0, torqueOutputCan=100))
        self.assertNotIn("carControlLatActive", route.timeline(OutputMsg(), "carOutput"))


if __name__ == "__main__":
    unittest.main()
