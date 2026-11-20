"""Synthetic-only adversarial tests; no Comma identifiers or raw driving data."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "gate", Path(__file__).with_name("heldout_stability_gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def synthetic():
    metrics = {k: 1.0 for k in gate.FLOAT_METRICS}
    metrics["nativeCommandClippingFraction"] = 0.1
    metrics.update({k: 1 for k in gate.COUNT_METRICS})
    before = dict(metrics, eligibleSeconds=120.0, eligibleSamples=1200)
    after = dict(before, laneCenterRmseMeters=0.9)
    def held(token, input_digest, window_digest):
        paired = {"routeInputSha256": input_digest,
                  "eligibleWindowSha256": window_digest}
        return {"token": token, "sourceCommit": gate.SOURCE, "quality": {
            k: True for k in gate.QUALITY_FLAGS},
            "laneChangeEpisodes": 2, "baseline": dict(before, **paired),
            "candidate": dict(after, **paired)}
    return {"schemaVersion": 1, "sourceCommit": gate.SOURCE,
            "measurementQualification": gate.QUALITY,
            "purpose": "OFFLINE_PAIRED_REPLAY_NOT_INSTALLABLE",
            "holdoutFrozenBeforeCandidateFit": True,
            "independentReviewComplete": True,
            "developmentRoutes": [
                {"token": "synthetic-development-one", "sourceCommit": gate.SOURCE,
                 "routeInputSha256": "1" * 64},
                {"token": "synthetic-development-two", "sourceCommit": gate.SOURCE,
                 "routeInputSha256": "2" * 64}],
            "heldoutRoutes": [held("synthetic-heldout-one", "3" * 64, "5" * 64),
                              held("synthetic-heldout-two", "4" * 64, "6" * 64)]}


class GateTests(unittest.TestCase):
    def assert_blocked(self, d, reason):
        v = gate.evaluate(d)
        self.assertEqual(v["status"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(v["vehicleReleaseAuthorized"])
        self.assertIn(reason, v["reasons"])
        text = repr(v)
        self.assertNotIn("synthetic-heldout-one", text)
        self.assertNotIn("synthetic-development-one", text)

    def test_synthetic_positive_is_offline_review_only(self):
        v = gate.evaluate(synthetic())
        self.assertEqual(v["status"], "OFFLINE_EVIDENCE_ELIGIBLE_FOR_REVIEW")
        self.assertFalse(v["vehicleReleaseAuthorized"])
        self.assertFalse(v["calibrationAuthorized"])
        self.assertEqual(v["independentHeldoutRouteCount"], 2)

    def test_missing_actual_ground_truth_fails_closed(self):
        d = synthetic()
        d["heldoutRoutes"][0]["quality"]["independentLateralPositionMeasured"] = False
        self.assert_blocked(d, "heldout_1_quality_or_attribution_unverified")

    def test_ambiguous_controller_phase_fails_closed(self):
        d = synthetic()
        d["heldoutRoutes"][0]["quality"]["controllerPhaseResolved"] = None
        self.assert_blocked(d, "heldout_1_quality_or_attribution_unverified")

    def test_heldout_and_fit_overlap_fails_closed(self):
        d = synthetic()
        d["heldoutRoutes"][0]["token"] = d["developmentRoutes"][0]["token"]
        self.assert_blocked(d, "route_identity_missing_repeated_or_overlapping")

    def test_only_one_independent_heldout_fails_closed(self):
        d = synthetic()
        d["heldoutRoutes"].pop()
        self.assert_blocked(d, "insufficient_independent_heldout_routes")

    def test_ping_pong_can_override_centering_improvement(self):
        d = synthetic()
        d["heldoutRoutes"][0]["candidate"]["steeringReversalsPerMinute"] = 1.2
        self.assert_blocked(d, "heldout_1_regressed_steeringReversalsPerMinute")

    def test_overshoot_can_override_lateral_rmse_improvement(self):
        d = synthetic()
        d["heldoutRoutes"][1]["candidate"]["lateralOvershootP95Meters"] = 1.2
        self.assert_blocked(d, "heldout_2_regressed_lateralOvershootP95Meters")

    def test_torque_clipping_regression_fails_closed(self):
        d = synthetic()
        d["heldoutRoutes"][0]["candidate"]["nativeCommandClippingFraction"] = 0.2
        self.assert_blocked(d, "heldout_1_regressed_nativeCommandClippingFraction")

    def test_missing_lane_crossing_ground_truth_fails_closed(self):
        d = synthetic()
        d["heldoutRoutes"][0]["laneChangeEpisodes"] = 0
        self.assert_blocked(d, "heldout_1_physical_lane_change_unobserved")

    def test_negative_nonfinite_boolean_and_missing_fail_closed(self):
        for bad in (-1, float("nan"), float("inf"), True, None):
            with self.subTest(bad=str(bad)):
                d = synthetic()
                d["heldoutRoutes"][0]["candidate"]["laneCenterRmseMeters"] = bad
                self.assert_blocked(d, "heldout_1_invalid_laneCenterRmseMeters")

    def test_exposure_and_pairing_integrity(self):
        d = synthetic()
        d["heldoutRoutes"][0]["candidate"]["eligibleSamples"] -= 1
        self.assert_blocked(d, "heldout_1_insufficient_or_unpaired_exposure")
        d = synthetic()
        d["heldoutRoutes"][0]["baseline"]["eligibleSeconds"] = 20
        d["heldoutRoutes"][0]["candidate"]["eligibleSeconds"] = 20
        self.assert_blocked(d, "heldout_1_insufficient_or_unpaired_exposure")

    def test_stale_source_or_metric_version(self):
        d = synthetic()
        d["sourceCommit"] = "different"
        self.assert_blocked(d, "pinned_source_or_manifest_mismatch")
        d = synthetic()
        d["measurementQualification"] = "qlog_wheel_rate_only"
        self.assert_blocked(d, "missing_independent_ground_truth_qualification")

    def test_no_claim_if_all_replay_metrics_equal(self):
        d = synthetic()
        for r in d["heldoutRoutes"]:
            r["candidate"]["laneCenterRmseMeters"] = 1.0
        self.assert_blocked(d, "no_measured_nonregressing_improvement")

    def test_no_precommitted_holdout_or_independent_review(self):
        d = synthetic()
        d["holdoutFrozenBeforeCandidateFit"] = False
        self.assert_blocked(d, "holdout_not_precommitted")
        d = synthetic()
        d["independentReviewComplete"] = False
        self.assert_blocked(d, "independent_review_pending")

    def test_source_pins_on_each_cohort(self):
        d = synthetic()
        d["developmentRoutes"][0]["sourceCommit"] = "old"
        self.assert_blocked(d, "development_source_not_pinned")
        d = synthetic()
        d["heldoutRoutes"][0]["sourceCommit"] = "old"
        self.assert_blocked(d, "heldout_1_source_not_pinned")

    def test_same_counts_different_eligible_window_rejected(self):
        d = synthetic()
        d["heldoutRoutes"][0]["candidate"]["eligibleWindowSha256"] = "7" * 64
        self.assert_blocked(d, "heldout_1_unpaired_or_invalid_eligibleWindowSha256")

    def test_same_exposure_different_underlying_replay_input_rejected(self):
        d = synthetic()
        d["heldoutRoutes"][0]["candidate"]["routeInputSha256"] = "7" * 64
        self.assert_blocked(d, "heldout_1_unpaired_or_invalid_routeInputSha256")

    def test_missing_or_noncanonical_fingerprints_fail_closed(self):
        for bad in (None, "", "g" * 64, "A" * 64, "a" * 63, True, 123):
            with self.subTest(bad=repr(bad)):
                d = synthetic()
                d["heldoutRoutes"][1]["baseline"]["eligibleWindowSha256"] = bad
                self.assert_blocked(d, "heldout_2_unpaired_or_invalid_eligibleWindowSha256")
        d = synthetic()
        d["developmentRoutes"][1]["routeInputSha256"] = None
        self.assert_blocked(d, "development_input_fingerprint_invalid")

    def test_renaming_same_replay_input_cannot_fake_independent_holdout(self):
        d = synthetic()
        d["heldoutRoutes"][0]["baseline"]["routeInputSha256"] = "1" * 64
        d["heldoutRoutes"][0]["candidate"]["routeInputSha256"] = "1" * 64
        self.assert_blocked(d, "route_input_fingerprint_reused_across_cohorts")
        d = synthetic()
        d["heldoutRoutes"][1]["baseline"]["routeInputSha256"] = "3" * 64
        d["heldoutRoutes"][1]["candidate"]["routeInputSha256"] = "3" * 64
        self.assert_blocked(d, "route_input_fingerprint_reused_across_cohorts")

    def test_hash_values_never_echoed_in_success_or_failure(self):
        for d in (synthetic(), synthetic()):
            result = gate.evaluate(d)
            d["heldoutRoutes"][0]["candidate"]["eligibleWindowSha256"] = "7" * 64
            output = repr(result) + repr(gate.evaluate(d))
            for digit in "1234567":
                self.assertNotIn(digit * 64, output)

    def test_no_privacy_leak_even_when_input_invalid(self):
        d = synthetic()
        d["heldoutRoutes"][0]["candidate"]["driverOverrideEpisodes"] = "private-token"
        self.assert_blocked(d, "heldout_1_invalid_driverOverrideEpisodes")


if __name__ == "__main__":
    unittest.main()
