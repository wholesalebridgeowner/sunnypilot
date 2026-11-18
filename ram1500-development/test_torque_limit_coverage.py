"""Offline unit tests: no Comma, credentials, network, or vehicle CAN required."""
from pathlib import Path
import importlib.util
import unittest

spec = importlib.util.spec_from_file_location(
    "torque_limit_coverage", Path(__file__).with_name("torque_limit_coverage.py"))
audit_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_mod)


def sample(stream, seconds, **values):
    return {"stream": stream, "seconds": seconds, "valid": True, **values}


class TorqueCapAuditTests(unittest.TestCase):
    def test_normalized_controller_cap_does_not_imply_can_reference_cap(self):
        rows = [
            sample("controlsState", 1801, lateralOutput=1.0, lateralSaturated=True),
            sample("carControl", 1801, requestedTorque=-1.0, carControlLatActive=True),
            sample("carOutput", 1801, outputTorqueCan=280),
        ]
        report = audit_mod.audit(rows)
        self.assertEqual(report["controller"]["outputAtNormalizedCeiling"], 1)
        self.assertEqual(report["controller"]["saturatedFlagTrue"], 1)
        self.assertEqual(report["request"]["torqueAtNormalizedCeiling"], 1)
        self.assertEqual(report["request"]["latActiveTrue"], 1)
        self.assertEqual(report["canCommand"]["nearNativeReference"], 0)
        self.assertAlmostEqual(report["canCommand"]["maxFractionOfReferenceLimit"], .8)

    def test_actual_native_command_near_reference_is_counted_separately(self):
        rows = [
            sample("carOutput", 1800, outputTorqueCan=-350),
            sample("carOutput", 1807, outputTorqueCan=333),
            sample("carOutput", 1801, outputTorqueCan=320),
        ]
        r = audit_mod.audit(rows)
        self.assertEqual(r["canCommand"]["nearNativeReference"], 2)
        self.assertEqual(r["canCommand"]["validNativeTorque"], 3)
        self.assertEqual(r["canCommand"]["maxAbsNativeTorque"], 350)

    def test_invalid_and_outside_window_excluded(self):
        rows = [
            sample("controlsState", 1799.9, lateralOutput=1),
            sample("controlsState", 1807.001, lateralOutput=1),
            sample("controlsState", 1801, valid=False, lateralOutput=1),
            sample("controlsState", 1801, lateralOutput=.5),
            sample("carOutput", 1802, outputTorqueCan="NaN"),
        ]
        report = audit_mod.audit(rows)
        self.assertEqual(report["discardedInvalidMessages"], 1)
        self.assertEqual(report["controller"]["samples"], 1)
        self.assertEqual(report["controller"]["outputAtNormalizedCeiling"], 0)
        self.assertEqual(report["canCommand"]["validNativeTorque"], 0)
        self.assertIsNone(report["canCommand"]["maxFractionOfReferenceLimit"])

    def test_unknown_validity_counted_without_replacing_true(self):
        rows = [{"stream": "carControl", "seconds": 1803,
                 "requestedTorque": .999, "carControlLatActive": "true"},
                {"stream": "carControl", "seconds": 1803.1,
                 "valid": "invalid", "requestedTorque": None}]
        result = audit_mod.audit(rows)
        self.assertEqual(result["unknownValidityMessages"], 2)
        self.assertEqual(result["request"]["torqueAtNormalizedCeiling"], 1)
        self.assertEqual(result["request"]["validTorque"], 1)

    def test_bad_parameters_fail_closed(self):
        for kwargs in (
            {"begin_seconds": 3, "end_seconds": 2},
            {"begin_seconds": float("nan")},
            {"steer_max_native": 0},
            {"normalized_ceiling": 1.2},
            {"near_native_fraction": 0},
        ):
            with self.assertRaises(ValueError):
                audit_mod.audit([], **kwargs)

    def test_only_summary_aggregates_no_private_source_details(self):
        rows = [sample("carOutput", 1804, outputTorqueCan=70,
                       routeId="sensitive-route", vin="sensitive-vin",
                       monoTimeNs=123456789)]
        result = audit_mod.audit(rows)
        self.assertNotIn("sensitive-route", str(result))
        self.assertNotIn("sensitive-vin", str(result))
        self.assertNotIn("monoTimeNs", str(result))


if __name__ == "__main__":
    unittest.main()
