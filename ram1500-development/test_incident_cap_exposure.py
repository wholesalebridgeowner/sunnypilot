"""Regression fixtures for incident-only, non-installable native-cap exposure."""
from unittest import TestCase

from auto_review import SCHEMA_COMMIT
from incident_cap_exposure import NativeCapExposure


def healthy(t, observer, *, active=True, saturated=True, override=False):
    observer.observe("carState", t, {
        "canValid": True, "steerFaultTemporary": False,
        "steerFaultPermanent": False, "steeringPressed": override,
    })
    observer.observe("carControl", t + .001, {"latActive": active})
    observer.observe("controlsState", t + .002, {"saturated": saturated})


class CapExposureTests(TestCase):
    def test_verified_past_only_active_ceiling_coverage_is_not_physical_acceptance(self):
        a = NativeCapExposure()
        a.segment_boundary()
        healthy(1.0, a)
        a.observe("carOutput", 1.003, {"torqueOutputCan": 350})
        a.observe("carOutput", 1.013, {"torqueOutputCan": 350})
        a.observe("carOutput", 1.023, {"torqueOutputCan": 350})
        a.observe("carOutput", 1.024, {"torqueOutputCan": 300})
        r = a.report()
        self.assertEqual(r["status"], "qualified_observational_coverage")
        self.assertEqual(r["sourceCommit"], SCHEMA_COMMIT)
        self.assertEqual(r["atDocumentedNativeCeilingSamples"], 3)
        self.assertEqual(r["qualifiedCommaLateralAtCeilingSamples"], 3)
        self.assertEqual(r["qualifiedWithControllerSaturated"], 3)
        self.assertEqual(r["qualifiedCapSampleRuns"], 1)
        self.assertEqual(r["longestQualifiedRunObservedSeconds"], .02)
        self.assertTrue(r["noVehicleWrite"])
        self.assertNotIn("route", str(r).lower())
        self.assertNotIn("vin", str(r).lower())

    def test_missing_stale_inactive_or_override_evidence_never_qualifies(self):
        a = NativeCapExposure()
        a.segment_boundary()
        a.observe("carOutput", 1.0, {"torqueOutputCan": 350})
        healthy(2.0, a, active=False)
        a.observe("carOutput", 2.004, {"torqueOutputCan": 350})
        healthy(3.0, a, override=True)
        a.observe("carOutput", 3.004, {"torqueOutputCan": -350})
        healthy(4.0, a)
        a.observe("carOutput", 4.040, {"torqueOutputCan": 350})
        r = a.report()
        self.assertEqual(r["atDocumentedNativeCeilingSamples"], 4)
        self.assertEqual(r["qualifiedCommaLateralAtCeilingSamples"], 0)
        self.assertEqual(r["unqualifiedAtCeilingSamples"], 4)
        self.assertIsNone(r["longestQualifiedRunObservedSeconds"])

    def test_segment_boundary_resets_past_states_and_cap_runs(self):
        a = NativeCapExposure()
        a.segment_boundary()
        healthy(1.0, a, saturated=False)
        a.observe("carOutput", 1.003, {"torqueOutputCan": -350})
        a.segment_boundary()
        a.observe("carOutput", 1.010, {"torqueOutputCan": -350})
        healthy(1.020, a, saturated=False)
        a.observe("carOutput", 1.023, {"torqueOutputCan": -350})
        r = a.report()
        self.assertEqual(r["rlogSegmentsSeparated"], 2)
        self.assertEqual(r["qualifiedCommaLateralAtCeilingSamples"], 2)
        self.assertEqual(r["qualifiedWithoutControllerSaturated"], 2)
        self.assertEqual(r["qualifiedCapSampleRuns"], 2)
        self.assertEqual(r["unqualifiedAtCeilingSamples"], 1)

    def test_clock_inversion_quarantines_all_qualified_metrics(self):
        a = NativeCapExposure()
        a.segment_boundary()
        healthy(1.0, a)
        a.observe("carOutput", 1.01, {"torqueOutputCan": 350})
        a.observe("carState", 1.005, {"canValid": True})
        r = a.report()
        self.assertEqual(r["status"], "rejected_source_clock_or_ceiling")
        self.assertIsNone(r["qualifiedCommaLateralAtCeilingSamples"])
        self.assertIsNone(r["qualifiedCapSampleRuns"])

    def test_outside_documented_source_ceiling_rejects_measurements(self):
        a = NativeCapExposure()
        a.segment_boundary()
        healthy(1.0, a)
        a.observe("carOutput", 1.01, {"torqueOutputCan": 351})
        self.assertEqual(a.report()["overDocumentedCeilingSamples"], 1)
        self.assertEqual(a.report()["status"], "rejected_source_clock_or_ceiling")

    def test_unknown_controller_saturation_is_not_assumed_false(self):
        a = NativeCapExposure()
        a.segment_boundary()
        healthy(1.0, a)
        a.observe("controlsState", 1.002, {"saturated": None})
        a.observe("carOutput", 1.003, {"torqueOutputCan": 350})
        self.assertEqual(a.report()["qualifiedCommaLateralAtCeilingSamples"], 0)
