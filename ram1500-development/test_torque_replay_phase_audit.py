"""Synthetic-only replay phase sensitivity tests. No private telemetry."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import torque_replay_phase_audit as mod


def row(t, kind, **kwargs):
    return {"monoTimeNs": t, "seconds": 1800 + t / 1e9,
            "stream": kind, "valid": True, **kwargs}


def base_rows():
    return [row(0, "carOutput", outputTorqueCan=100),
            row(1_000_000, "carState", steeringTorqueEps=50),
            row(2_000_000, "carControl", requestedTorque=1, carControlLatActive=True)]


class CausalPhaseAuditTests(unittest.TestCase):
    def test_latest_sources_match(self):
        rows = base_rows() + [row(10_000_000, "carOutput", outputTorqueCan=106)]
        counts = mod.audit(rows)["counts"]
        self.assertEqual(counts["baselineMatched"], 1)
        self.assertNotIn("baselineUnmatched", counts)

    def test_older_control_counterfactual(self):
        rows = base_rows() + [
            row(5_000_000, "carControl", requestedTorque=0, carControlLatActive=True),
            row(10_000_000, "carOutput", outputTorqueCan=106)]
        counts = mod.audit(rows)["counts"]
        self.assertEqual(counts["baselineUnmatched"], 1)
        self.assertEqual(counts["olderControlOnly"], 1)

    def test_older_state_counterfactual(self):
        rows = [row(0, "carOutput", outputTorqueCan=100),
                row(1_000_000, "carControl", requestedTorque=1, carControlLatActive=True),
                row(2_000_000, "carState", steeringTorqueEps=-2000),
                row(5_000_000, "carState", steeringTorqueEps=1000),
                row(10_000_000, "carOutput", outputTorqueCan=94)]
        counts = mod.audit(rows)["counts"]
        self.assertEqual(counts["baselineUnmatched"], 1)
        self.assertEqual(counts["olderStateOnly"], 1)

    def test_future_input_cannot_explain(self):
        rows = [row(0, "carOutput", outputTorqueCan=100),
                row(1_000_000, "carState", steeringTorqueEps=50),
                row(2_000_000, "carControl", requestedTorque=0, carControlLatActive=True),
                row(10_000_000, "carOutput", outputTorqueCan=106),
                row(11_000_000, "carControl", requestedTorque=1, carControlLatActive=True)]
        self.assertEqual(mod.audit(rows)["counts"]["notExplainedByOlderInputs"], 1)

    def test_expired_older_source_cannot_explain(self):
        rows = [row(0, "carOutput", outputTorqueCan=100),
                row(1_000_000, "carState", steeringTorqueEps=50),
                row(2_000_000, "carControl", requestedTorque=1, carControlLatActive=True),
                row(90_000_000, "carOutput", outputTorqueCan=100),
                row(100_000_000, "carControl", requestedTorque=0, carControlLatActive=True),
                row(101_000_000, "carState", steeringTorqueEps=50),
                row(105_000_000, "carOutput", outputTorqueCan=106)]
        self.assertEqual(mod.audit(rows)["counts"]["notExplainedByOlderInputs"], 1)

    def test_invalid_parameters_and_no_identifying_output(self):
        for options in ({"max_older": True}, {"max_source_age_ms": 500},
                        {"tolerance": float("nan")}, {"start": -3}):
            with self.assertRaises(ValueError):
                mod.audit([], **options)
        rows = base_rows() + [row(10_000_000, "carOutput", outputTorqueCan=106,
                                  VIN="SECRET_VIN", gps="SECRET_LOCATION")]
        encoded = json.dumps(mod.audit(rows), allow_nan=False)
        for sensitive in ("SECRET_VIN", "SECRET_LOCATION", "monoTimeNs"):
            self.assertNotIn(sensitive, encoded)

    def test_same_time_and_duplicate_prior_is_not_a_previous_output(self):
        rows = base_rows() + [row(0, "carOutput", outputTorqueCan=100),
                              row(10_000_000, "carOutput", outputTorqueCan=106)]
        counts = mod.audit(rows)["counts"]
        self.assertIn("missingFreshPreviousOutput", counts)
        self.assertEqual(counts["baselineMatched"], 1)

    def test_not_matched_at_ceiling_is_measured_not_suppressed(self):
        rows = [row(0, "carOutput", outputTorqueCan=100),
                row(1_000_000, "carControl", requestedTorque=1, carControlLatActive=True),
                row(2_000_000, "carState", steeringTorqueEps=50),
                row(10_000_000, "carOutput", outputTorqueCan=104)]
        counts = mod.audit(rows)["counts"]
        self.assertEqual(counts["unmatchedAtNormalizedCeiling"], 1)


if __name__ == "__main__":
    unittest.main()
