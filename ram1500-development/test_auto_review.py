"""Fixture-only tests: no Comma, credentials, Railway or real driver inputs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import json
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent))
from auto_review import RouteMetrics, compare, summarize_log_messages, SCHEMA_COMMIT
from auto_review_job import (group_routes, route_key, clock_audit_key, bounded_trial_key,
                             regression_origin_key, anonymous_clock_counts, verified_prior_observational_metrics, MAX_COMPRESSED, pinned_qlog_messages,
                             SourcePinError, run, PREFIX, safe_trial_rejection_reason)
from auto_review_clock_audit import audit_clock_order, ClockAuditSourceError
from auto_review_bounded_order import source_pinned_bounded_messages, OrderingTrialError

T = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)

def obj(route, segment, hours_ago=12, size=100, etag="v1"):
    return {"Key": f"logs/{route}/{segment}/qlog.zst",
            "Size": size, "LastModified": T - timedelta(hours=hours_ago),
            "ETag": etag}

class AnalysisTests(unittest.TestCase):
    def test_measures_lane_change_headway_and_discrete_alert(self):
        a = RouteMetrics()
        a.ingest("carParams", 0, {"openpilotLongitudinalControl": False, "pcmCruise": True})
        a.ingest("carState", 1, {"vEgo": 20.0, "aEgo": 0.2,
                 "gasPressed": False, "brakePressed": False, "cruiseState": {"enabled": True},
                 "steeringRateDeg": 1.5})
        a.ingest("radarState", 1.1, {"leadOne": {"status": True, "dRel": 32}})
        a.ingest("drivingModelData", 1.15, {"meta": {"laneChangeState": "laneChangeStarting",
                                                      "laneChangeDirection": "left"}})
        a.ingest("carState", 1.2, {"vEgo": 20.0, "aEgo": 0.25, "gasPressed": False,
                 "brakePressed": False, "cruiseState": {"enabled": True},
                 "steeringRateDeg": 4.5})
        a.ingest("onroadEvents", 1.3, ["steerSaturated"])
        a.ingest("onroadEvents", 1.4, ["steerSaturated"])
        a.ingest("drivingModelData", 2.5, {"meta": {"laneChangeState": "laneChangeFinishing",
                                                      "laneChangeDirection": "left"}})
        # A new alert episode requires an observed published absence.
        a.ingest("onroadEvents", 2.6, [])
        a.ingest("onroadEvents", 3.1, ["steerSaturated"])
        result = a.report()
        self.assertEqual(result["leadHeadway"]["medianSeconds"], 1.6)
        self.assertEqual(result["laneChange"]["completeStartingWindows"], 1)
        self.assertEqual(result["laneChange"]["p90PeakSteeringRateDegPerSecond"], 4.5)
        self.assertEqual(result["steering"]["steerSaturatedEventEpisodes"], 2)
        self.assertFalse(result["cruiseOwnership"]["carParams"]["openpilotLongitudinalControl"])
        self.assertNotIn("route", result)

    def test_prevents_false_headway_when_driver_presses_pedal(self):
        a = RouteMetrics()
        a.ingest("carState", 1, {"vEgo": 20, "gasPressed": True,
                 "brakePressed": False, "cruiseState": {"enabled": True}})
        a.ingest("radarState", 1.1, {"leadOne": {"status": True, "dRel": 40}})
        self.assertEqual(a.report()["leadHeadway"]["eligibleCruiseSamples"], 0)

    def test_comparison_has_no_unsupported_causal_claim(self):
        current = {"laneChange": {"medianStartingSeconds": 2.4},
                   "steering": {"steerSaturatedEventEpisodes": 2}}
        previous = {"laneChange": {"medianStartingSeconds": 3.1},
                    "steering": {"steerSaturatedEventEpisodes": 4}}
        delta = compare(current, previous)
        self.assertTrue(delta["baselineAvailable"])
        self.assertEqual(delta["metrics"]["laneChange.medianStartingSeconds"]["delta"], -.7)
        self.assertIn("Observational", delta["interpretation"])
        self.assertFalse(compare(current, None)["baselineAvailable"])

    def test_route_gate_requires_contiguous_segs_and_30_minutes_quiet(self):
        route = "device|000003d8--abc"
        self.assertEqual(len(group_routes([obj(route, 0), obj(route, 1)], T)), 1)
        self.assertEqual(group_routes([obj(route, 0), obj(route, 2)], T), [])
        # Important: a fresh segment must not be discarded while earlier segments pass.
        self.assertEqual(group_routes([obj(route, 0), obj(route, 1, hours_ago=0.1)], T), [])
        self.assertEqual(len(group_routes([obj(route, 0, hours_ago=0.75)], T)), 1)
        self.assertEqual(group_routes([obj(route, 0, size=MAX_COMPRESSED+1)], T), [])
        self.assertEqual(group_routes([obj(route, 0, size=0)], T), [])
        self.assertEqual(group_routes([obj(route, 0, hours_ago=400)], T), [])

    def test_route_fingerprint_is_idempotent_and_private(self):
        route = "private-id|000003d8--abc"
        a = group_routes([obj(route, 0)], T)[0]
        b = group_routes([obj(route, 0)], T)[0]
        self.assertEqual(route_key(a), route_key(b))
        self.assertNotIn("private-id", route_key(a))
        changed = group_routes([obj(route, 0, etag="v2")], T)[0]
        self.assertNotEqual(route_key(a), route_key(changed))

    def test_out_of_order_route_fails(self):
        a = RouteMetrics()
        a.ingest("carState", 10, {})
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            a.ingest("carState", 8, {})


class FactoryAccObservedKinematicsTests(unittest.TestCase):
    """Synthetic stock-cruise observations, not an OEM ACC or chassis replay."""

    @staticmethod
    def state(t, accel, *, cruise=True, gas=False, brake=False, speed=20.0):
        return t, {
            "vEgo": speed, "aEgo": accel, "cruiseState": {"enabled": cruise},
            "gasPressed": gas, "brakePressed": brake,
        }

    @staticmethod
    def stock():
        a = RouteMetrics()
        a.ingest("carParams", 1, {
            "openpilotLongitudinalControl": False, "pcmCruise": True,
        })
        return a

    def test_contiguous_pcm_cruise_no_pedal_acceleration_and_jerk(self):
        a = self.stock()
        for t, state in (
            self.state(10, 0.0), self.state(10.1, 0.2),
            self.state(10.2, 0.1)):
            a.ingest("carState", t, state)
        v = a.report()["factoryAccObservedMotion"]
        self.assertTrue(v["pcmCarParamsSourceQualified"])
        self.assertEqual(v["eligibleCruiseSamples"], 3)
        self.assertEqual(v["contiguousObservedWindows"], 1)
        self.assertAlmostEqual(v["observedDurationSeconds"], .2)
        self.assertEqual(v["qualifiedJerkPairs"], 2)
        self.assertEqual(v["observedAccelMedianMps2"], .1)
        self.assertAlmostEqual(v["p90AbsSampledJerkMps3"], 1.9)
        self.assertFalse(v["actuatorAttributionVerified"])
        self.assertFalse(v["calibrationEligible"])
        self.assertIn("NOT factory ACC-specific", a.report()["comfort"]["caution"])

    def test_cruise_off_driver_override_and_invalid_state_break_jerk(self):
        a = self.stock()
        samples = [
            self.state(10, 0), self.state(10.1, .1),
            self.state(10.2, 4, brake=True),
            self.state(10.3, .1),
            self.state(10.4, 8, cruise=False),
            self.state(10.5, .1),
            self.state(10.6, 9, gas=True),
            self.state(10.7, .1),
            self.state(10.8, 2, speed=float("nan")),
            self.state(10.9, .1),
            self.state(11.0, .1, gas=None),
        ]
        for t, state in samples:
            a.ingest("carState", t, state)
        v = a.report()["factoryAccObservedMotion"]
        self.assertEqual(v["eligibleCruiseSamples"], 6)
        self.assertEqual(v["contiguousObservedWindows"], 5)
        self.assertEqual(v["qualifiedJerkPairs"], 1)
        self.assertAlmostEqual(v["observedDurationSeconds"], .1)

    def test_no_stock_pcm_provenance_is_ineligible(self):
        for params in (None,
                       {"openpilotLongitudinalControl": True, "pcmCruise": True},
                       {"openpilotLongitudinalControl": False, "pcmCruise": False},
                       {"openpilotLongitudinalControl": False, "pcmCruise": None}):
            a = RouteMetrics()
            if params is not None:
                a.ingest("carParams", 1, params)
            for t, d in (self.state(10, .2), self.state(10.1, .3)):
                a.ingest("carState", t, d)
            v = a.report()["factoryAccObservedMotion"]
            self.assertFalse(v["pcmCarParamsSourceQualified"])
            self.assertEqual(v["eligibleCruiseSamples"], 0)
            self.assertEqual(v["qualifiedJerkPairs"], 0)
            self.assertIsNone(v["p90AbsSampledJerkMps3"])

    def test_conflicting_source_metadata_wipes_earlier_motion(self):
        a = self.stock()
        for t, d in (self.state(10, .2), self.state(10.1, .3)):
            a.ingest("carState", t, d)
        a.ingest("carParams", 10.15, {
            "openpilotLongitudinalControl": True, "pcmCruise": True,
        })
        a.ingest("carParams", 10.16, {
            "openpilotLongitudinalControl": False, "pcmCruise": True,
        })
        for t, d in (self.state(10.2, .2), self.state(10.3, .3)):
            a.ingest("carState", t, d)
        v = a.report()["factoryAccObservedMotion"]
        self.assertTrue(v["sourceMetadataConflict"])
        self.assertFalse(v["pcmCarParamsSourceQualified"])
        self.assertEqual(v["eligibleCruiseSamples"], 0)
        self.assertEqual(v["qualifiedJerkPairs"], 0)

    def test_gap_clock_tie_and_reversal_cannot_create_jerk(self):
        a = self.stock()
        samples = [self.state(10, 0), self.state(10.6, 1),
                   self.state(10.6, 9), self.state(10.55, -9),
                   self.state(10.56, 8, cruise=False), self.state(10.7, 1)]
        for t, d in samples:
            a.ingest("carState", t, d)
        v = a.report()["factoryAccObservedMotion"]
        self.assertEqual(v["eligibleCruiseSamples"], 4)
        self.assertEqual(v["qualifiedJerkPairs"], 0)
        self.assertEqual(v["observedDurationSeconds"], 0)
        self.assertEqual(v["contiguousObservedWindows"], 4)

    def test_no_route_timestamps_or_raw_vehicle_fields_in_aggregate(self):
        a = self.stock()
        for t, d in (self.state(123456789.1, .1), self.state(123456789.2, .2)):
            a.ingest("carState", t, d)
        safe = json.dumps(a.report()["factoryAccObservedMotion"])
        self.assertNotIn("123456789", safe)
        self.assertNotIn("route", safe.lower())
        self.assertNotIn("VIN", safe)
        self.assertNotIn("throttleCommand", safe)


class FakeMessage:
    def __init__(self, kind, time, payload):
        self.kind = kind
        self.logMonoTime = int(time * 1_000_000_000)
        setattr(self, kind, payload)

    def which(self):
        return self.kind


def source_header(time=-900, commit=SCHEMA_COMMIT):
    return FakeMessage("initData", time, SimpleNamespace(gitCommit=commit))


def car_state(time):
    return FakeMessage("carState", time, {"vEgo": 20.0,
                       "aEgo": 0.0, "cruiseState": {"enabled": False}})


class MissingKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FixtureS3:
    def __init__(self, objects):
        self.objects = objects
        self.reports = {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **_):
        return [{"Contents": self.objects}]

    def get_object(self, *, Key, **_):
        if Key not in self.reports:
            raise MissingKey()
        return {"Body": SimpleNamespace(read=lambda: json.dumps(self.reports[Key]).encode())}

    def put_object(self, *, Key, Body, **_):
        self.reports[Key] = json.loads(Body)

    def download_file(self, _bucket, _key, output):
        Path(output).write_bytes(b"synthetic")


class ContinuityTests(unittest.TestCase):
    def test_replayed_old_initdata_does_not_shift_or_reverse_route_clock(self):
        def read(path):
            segment = int(Path(path).parent.name)
            yield source_header(-900 if segment else 1)
            yield car_state(10 + 10 * segment)
        data = summarize_log_messages(
            pinned_qlog_messages([Path("0/qlog.zst"), Path("1/qlog.zst")], read))
        self.assertEqual(data["messageCounts"]["carState"], 2)
        self.assertEqual(data["durationSeconds"], 10.0)
        self.assertIn("Q-logs are sampled", " ".join(data["limitations"]))

    def test_missing_or_mismatched_per_segment_source_fails_closed(self):
        def wrong(path):
            if Path(path).parent.name == "1":
                yield source_header(commit="bad")
            else:
                yield source_header()
            yield car_state(10)
        with self.assertRaises(SourcePinError):
            list(pinned_qlog_messages([Path("0/qlog.zst"), Path("1/qlog.zst")], wrong))

        def missing(path):
            if Path(path).parent.name != "1":
                yield source_header()
            yield car_state(10)
        with self.assertRaises(SourcePinError):
            list(pinned_qlog_messages([Path("0/qlog.zst"), Path("1/qlog.zst")], missing))

    def test_real_out_of_order_motion_is_not_silently_sorted(self):
        def read(_):
            yield source_header()
            yield car_state(10)
            yield car_state(8)
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            summarize_log_messages(pinned_qlog_messages([Path("0/qlog.zst")], read))

    def test_heartbeat_is_not_a_second_alert_episode(self):
        review = RouteMetrics()
        review.ingest("onroadEvents", 1.0, ["steerSaturated"])
        review.ingest("onroadEvents", 2.002, ["steerSaturated"])
        self.assertEqual(review.report()["steering"]["steerSaturatedEventEpisodes"], 1)
        review.ingest("onroadEvents", 3.004, [])
        review.ingest("onroadEvents", 4.005, ["steerSaturated"])
        self.assertEqual(review.report()["steering"]["steerSaturatedEventEpisodes"], 2)

    def test_failed_route_written_private_then_skipped_on_next_cron(self):
        s3 = FixtureS3([obj("synthetic|000003d8--abc", 0)])
        def fail(_):
            yield source_header()
            raise ValueError("SENSITIVE_UNTRUSTED_EXCEPTION_MESSAGE")
        first = run(s3, "fake-bucket", T, fail)
        self.assertEqual(first["status"], "route_quarantined")
        self.assertEqual(first["reason"], "decode_or_order_invalid")
        self.assertEqual(len(s3.reports), 1)
        self.assertNotIn("SENSITIVE", json.dumps(s3.reports))
        self.assertNotIn("synthetic|", json.dumps(s3.reports))
        second = run(s3, "fake-bucket", T, fail)
        self.assertEqual(second["status"], "clock_diagnostic_saved")
        self.assertEqual(second["diagnostic"], "reader_or_clock_invalid")
        self.assertNotIn("SENSITIVE", json.dumps(s3.reports))
        third = run(s3, "fake-bucket", T, fail)
        self.assertEqual(third["status"], "no_new_eligible_routes")

    def test_same_route_reupload_is_not_an_independent_comparison(self):
        s3 = FixtureS3([obj("synthetic|000003d8--abc", 0, etag="v2")])
        candidate = group_routes(s3.objects, T)[0]
        s3.reports[PREFIX + "latest.json"] = {
            "routeHash": candidate["hash"],
            "metrics": {"steering": {"steerSaturatedEventEpisodes": 99}},
            "routeUpdatedAt": (T - timedelta(days=1)).isoformat(),
        }
        def read(_):
            yield source_header()
            yield car_state(10)
        result = run(s3, "fake-bucket", T, read)
        self.assertEqual(result["status"], "review_saved")
        self.assertFalse(result["comparisonBaseline"])
        self.assertEqual(s3.reports[route_key(candidate)]["sourceVerifiedSegments"], 1)
        self.assertNotIn("largeDifference", str(compare(
            {"steering": {"steerSaturatedEventEpisodes": 1}},
            {"steering": {"steerSaturatedEventEpisodes": 0}})))


class ComparisonProvenanceTests(unittest.TestCase):
    """Unmatched route comparison can never certify a calibration candidate."""

    @staticmethod
    def prior():
        return {
            "routeHash": "a" * 20,
            "inputFingerprint": "b" * 24,
            "schemaCommit": SCHEMA_COMMIT,
            "segments": 2,
            "sourceVerifiedSegments": 2,
            "assistedSteeringReviewEligible": True,
            "routeUpdatedAt": (T - timedelta(days=1)).isoformat(),
            "metrics": {
                "cruiseOwnership": {"latActiveSamples": {"True": 8}},
                "laneChange": {"medianStartingSeconds": 2.5},
            },
        }

    def test_only_provenanced_disjoint_active_route_qualifies_as_observational_prior(self):
        self.assertIsNotNone(verified_prior_observational_metrics(self.prior(), "c" * 20))
        for key, value in (
            ("routeHash", "c" * 20),  # same route's revised upload is not independent
            ("routeHash", None),
            ("inputFingerprint", None),
            ("inputFingerprint", "unverified"),
            ("schemaCommit", "different-build"),
            ("segments", 0),
            ("sourceVerifiedSegments", 1),
            ("assistedSteeringReviewEligible", False),
        ):
            prior = self.prior()
            prior[key] = value
            with self.subTest(field=key, value=value):
                self.assertIsNone(verified_prior_observational_metrics(prior, "c" * 20))
        for malformed in (None, [], "unknown", {"True": True},
                          {"True": 0}, {"True": "8"}):
            prior = self.prior()
            prior["metrics"]["cruiseOwnership"]["latActiveSamples"] = malformed
            with self.subTest(malformed=malformed):
                self.assertIsNone(verified_prior_observational_metrics(prior, "c" * 20))

    def test_a_new_active_route_reports_only_unmatched_comparison_not_held_out_replay(self):
        name = "PRIVATE_DEVICE_PRIVATE_ROUTE"
        s3 = FixtureS3([obj(name, 0)])
        selected = group_routes(s3.objects, T)[0]
        prior = self.prior()
        prior["routeHash"] = "a" * 20 if selected["hash"] != "a" * 20 else "c" * 20
        s3.reports[PREFIX + "latest.json"] = prior
        def read(_):
            yield source_header()
            yield car_state(10)
            yield FakeMessage("carControl", 10.1, {"latActive": True, "longActive": False})
            yield car_state(11)
        result = run(s3, "private-fixture", T, read)
        self.assertEqual(result["status"], "review_saved")
        self.assertTrue(result["comparisonBaseline"])  # backward-compatible legacy flag
        self.assertEqual(result["comparisonKind"], "unmatched_prior_active_route")
        self.assertFalse(result["calibrationEligible"])
        saved = s3.reports[route_key(selected)]
        self.assertEqual(saved["reportVersion"], 2)
        self.assertTrue(saved["comparison"]["baselineAvailable"])
        evidence = saved["comparisonEvidence"]
        self.assertEqual(evidence["kind"], "unmatched_prior_active_route")
        self.assertTrue(evidence["priorActiveRouteSourceVerified"])
        for key in ("matchedConditionsVerified", "pairedBaselineCandidateReplayVerified",
                    "independentLaneTrackingVerified", "calibrationEligible"):
            self.assertFalse(evidence[key])
        self.assertNotIn(name, json.dumps(result))
        self.assertNotIn(name, json.dumps(saved))

    def test_unpinned_prior_cannot_masquerade_as_baseline(self):
        s3 = FixtureS3([obj("PRIVATE_DEVICE", 0)])
        selected = group_routes(s3.objects, T)[0]
        prior = self.prior()
        prior["schemaCommit"] = "another-source"
        s3.reports[PREFIX + "latest.json"] = prior
        def read(_):
            yield source_header()
            yield car_state(10)
            yield FakeMessage("carControl", 10.1, {"latActive": True, "longActive": False})
        result = run(s3, "private-fixture", T, read)
        self.assertFalse(result["comparisonBaseline"])
        self.assertEqual(result["comparisonKind"], "none")
        self.assertFalse(s3.reports[route_key(selected)]["comparisonEvidence"]["calibrationEligible"])


class AnonymousClockAuditTests(unittest.TestCase):
    def test_old_clock_metadata_is_ignored_but_inversions_are_counted(self):
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header(-900)
            if seg == 0:
                yield car_state(10)
                yield car_state(8)  # a within-segment reversal
            else:
                yield car_state(5)  # a cross-segment reversal
                yield car_state(6)
        result = audit_clock_order([Path("0/qlog.zst"),
                                    Path("1/qlog.zst")], reader)
        self.assertEqual(result["segmentsVerified"], 2)
        self.assertEqual(result["observations"], 4)
        self.assertEqual(result["inversionsOverHalfSecond"], 2)
        self.assertEqual(result["withinSegmentInversions"], 1)
        self.assertEqual(result["crossSegmentInversions"], 1)
        self.assertEqual(result["inversionSizeBands"]["twoToTenSeconds"], 2)
        self.assertNotIn("route", json.dumps(result).lower())
        self.assertNotIn("900", json.dumps(result))

    def test_console_allowlist_cannot_emit_private_payload(self):
        unsafe = {"status": "clock_order_audited", "route": "SECRET",
                  "driverNote": "SECRET", "routeHash": "SECRET",
                  "segmentsVerified": 2, "observations": 50,
                  "inversionsOverHalfSecond": 1,
                  "inversionSizeBands": {"underTwoSeconds": 1,
                                         "unknown": "SECRET"}}
        safe = anonymous_clock_counts(unsafe)
        self.assertEqual(safe["inversionsOverHalfSecond"], 1)
        self.assertNotIn("SECRET", json.dumps(safe))
        self.assertNotIn("route", json.dumps(safe).lower())
        self.assertIsNone(anonymous_clock_counts({"status": "quarantined"}))

    def test_clock_audit_source_pin_fails_closed(self):
        def read(_):
            yield source_header(commit="unverified")
            yield car_state(10)
        with self.assertRaises(ClockAuditSourceError):
            audit_clock_order([Path("0/qlog.zst")], read)

    def test_private_diagnostic_does_not_turn_quarantine_into_driving_report(self):
        s3 = FixtureS3([obj("secret-device|secret-route", 0)])
        def reversed_clock(_):
            yield source_header()
            yield car_state(10)
            yield car_state(8)
        # This is a pre-existing quarantine from the old arrival-order
        # implementation. The new cross-segment path can now safely sort
        # the bounded 2s inversion instead of quarantining a new upload.
        cand = group_routes(s3.objects, T)[0]
        s3.reports[route_key(cand)] = {
            "status": "quarantined", "reason": "historical_decode_or_order_invalid",
        }
        first = run(s3, "fake", T, reversed_clock)
        self.assertEqual(first["status"], "clock_diagnostic_saved")
        self.assertEqual(first["diagnostic"], "clock_order_audited")
        self.assertEqual(first["anonymousClockCounts"]["inversionsOverHalfSecond"], 1)
        self.assertEqual(s3.reports[route_key(cand)]["status"], "quarantined")
        self.assertEqual(s3.reports[clock_audit_key(cand)]["inversionsOverHalfSecond"], 1)
        self.assertNotIn("secret-", json.dumps(s3.reports))
        trial = run(s3, "fake", T, reversed_clock)
        self.assertEqual(trial["status"], "bounded_trial_saved")
        self.assertFalse(trial["releaseAuthorized"])
        self.assertEqual(s3.reports[route_key(cand)]["status"], "quarantined")
        self.assertEqual(s3.reports[bounded_trial_key(cand)]["status"],
                         "experimental_bounded_observation")
        self.assertNotIn("secret-", json.dumps(s3.reports))
        self.assertEqual(run(s3, "fake", T, reversed_clock)["status"], "no_new_eligible_routes")


class BoundedObservationTrialTests(unittest.TestCase):
    def test_stable_event_time_reordering_within_verified_segment(self):
        def reader(_):
            yield source_header(-900)
            yield car_state(10)
            yield car_state(11)
            yield car_state(10.25)
            yield car_state(12)
        counters = {}
        observed = list(source_pinned_bounded_messages(
            [Path("0/qlog.zst")], reader, counters))
        self.assertEqual([x.logMonoTime / 1e9 for x in observed],
                         [10, 10.25, 11, 12])
        self.assertEqual(counters["verifiedSegments"], 1)
        self.assertEqual(counters["arrivalInversionsOverHalfSecond"], 1)
        self.assertLessEqual(counters["maxBufferedEvents"], 10000)

    def test_rejects_late_outside_two_second_window(self):
        def reader(_):
            yield source_header()
            yield car_state(10)
            yield car_state(12.5)
            yield car_state(10)
        with self.assertRaises(OrderingTrialError):
            list(source_pinned_bounded_messages([Path("0/qlog.zst")], reader, {}))

    def test_rejects_even_small_cross_segment_clock_regression(self):
        def reader(path):
            yield source_header()
            yield car_state(10 if Path(path).parent.name == "0" else 9.9)
        with self.assertRaisesRegex(OrderingTrialError,
                                    "^cross-segment-clock-regression$"):
            list(source_pinned_bounded_messages(
                [Path("0/qlog.zst"), Path("1/qlog.zst")], reader, {}))

    def test_in_segment_bounded_inversion_still_succeeds_without_false_boundary(self):
        def reader(path):
            yield source_header()
            if Path(path).parent.name == "0":
                yield car_state(10)
                yield car_state(11)
                yield car_state(10.25)
            else:
                yield car_state(11.01)
                yield car_state(12)
        counts = {}
        ordered = list(source_pinned_bounded_messages(
            [Path("0/qlog.zst"), Path("1/qlog.zst")], reader, counts))
        self.assertEqual([m.logMonoTime / 1e9 for m in ordered],
                         [10, 10.25, 11, 11.01, 12])
        self.assertEqual(counts["verifiedSegments"], 2)

    def test_private_boundary_rejection_never_promotes_quarantine(self):
        s3 = FixtureS3([obj("SECRET_VEHICLE_AND_ROUTE", 0),
                        obj("SECRET_VEHICLE_AND_ROUTE", 1)])
        candidate = group_routes(s3.objects, T)[0]
        s3.reports[route_key(candidate)] = {"status": "quarantined"}
        s3.reports[clock_audit_key(candidate)] = {
            "status": "clock_order_audited", "inversionsOverHalfSecond": 1}
        def reader(path):
            yield source_header()
            yield car_state(11 if Path(path).parent.name == "0" else 10.99)
        outcome = run(s3, "private-synthetic", T, reader)
        self.assertEqual(outcome["status"], "bounded_trial_rejected")
        self.assertEqual(outcome["reason"], "segment_boundary_regression")
        self.assertEqual(s3.reports[bounded_trial_key(candidate)]["reason"],
                         "segment_boundary_regression")
        self.assertEqual(s3.reports[route_key(candidate)]["status"], "quarantined")
        self.assertNotIn(PREFIX + "latest.json", s3.reports)
        self.assertNotIn("SECRET_", json.dumps(s3.reports))

    def test_old_combined_regression_audit_is_private_and_immutable(self):
        s3 = FixtureS3([obj("SENSITIVE_DEVICE_ROUTE", 0),
                        obj("SENSITIVE_DEVICE_ROUTE", 1)])
        candidate = group_routes(s3.objects, T)[0]
        original = {"status": "trial_rejected", "reason": "event_clock_regression"}
        s3.reports[route_key(candidate)] = {"status": "quarantined"}
        s3.reports[clock_audit_key(candidate)] = {
            "status": "clock_order_audited", "inversionsOverHalfSecond": 1}
        s3.reports[bounded_trial_key(candidate)] = original.copy()
        def reader(path):
            yield source_header()
            yield car_state(12 if Path(path).parent.name == "0" else 11.99)
        result = run(s3, "private", T, reader)
        self.assertEqual(result["status"], "regression_origin_saved")
        self.assertEqual(result["reason"], "segment_boundary_regression")
        diagnostic = s3.reports[regression_origin_key(candidate)]
        self.assertEqual(diagnostic["status"], "diagnostic_only")
        self.assertFalse(diagnostic["releaseAuthorized"])
        self.assertEqual(s3.reports[bounded_trial_key(candidate)], original)
        self.assertEqual(s3.reports[route_key(candidate)]["status"], "quarantined")
        self.assertNotIn(PREFIX + "latest.json", s3.reports)
        self.assertNotIn("SENSITIVE_DEVICE_ROUTE", json.dumps(s3.reports))
        self.assertEqual(run(s3, "private", T, reader)["status"],
                         "no_new_eligible_routes")

    def test_origin_audit_does_not_echo_untrusted_decoder_errors(self):
        s3 = FixtureS3([obj("SENSITIVE_DEVICE_ROUTE", 0)])
        candidate = group_routes(s3.objects, T)[0]
        s3.reports[route_key(candidate)] = {"status": "quarantined"}
        s3.reports[bounded_trial_key(candidate)] = {
            "status": "trial_rejected", "reason": "event_clock_regression"}
        def reader(_):
            yield source_header()
            raise ValueError("SECRET_VIN_LOCATION_ERROR_SHOULD_NOT_BE_STORED")
        result = run(s3, "private", T, reader)
        self.assertEqual(result["reason"], "decode_or_metrics_invalid")
        self.assertNotIn("SECRET_VIN", json.dumps(s3.reports))
        self.assertNotIn(PREFIX + "latest.json", s3.reports)

    def test_rejects_source_mismatch_without_publishing_metrics(self):
        def reader(_):
            yield source_header(commit="unverified")
            yield car_state(10)
        with self.assertRaises(OrderingTrialError):
            list(source_pinned_bounded_messages([Path("0/qlog.zst")], reader, {}))


    def test_private_trial_rejection_categories_are_allowlisted(self):
        cases = {
            "late-event-beyond-window": "lateness_exceeds_window",
            "cross-segment-or-watermark-regression": "event_clock_regression",
            "cross-segment-clock-regression": "segment_boundary_regression",
            "emitted-watermark-regression": "emitted_order_regression",
            "missing-segment-source-header": "source_unverified",
            "recorded-source-not-verified": "source_unverified",
            "invalid-event-clock": "invalid_clock",
            "bounded-memory-ceiling": "bounded_memory_exceeded",
        }
        for code, category in cases.items():
            with self.subTest(code=code):
                self.assertEqual(safe_trial_rejection_reason(OrderingTrialError(code)),
                                 category)
        # An unexpected exception, even one raised by a decoder, must never
        # expose arbitrary text that might contain location or raw identifiers.
        secret = "SENSITIVE_ROUTE_OR_VIN_SHOULD_NEVER_LEAVE_PRIVATE_DECODER"
        self.assertEqual(safe_trial_rejection_reason(OrderingTrialError(secret)),
                         "bounded_order_or_source_invalid")
        self.assertEqual(safe_trial_rejection_reason(RuntimeError(secret)),
                         "decode_or_metrics_invalid")
        self.assertNotIn(secret, json.dumps([
            safe_trial_rejection_reason(OrderingTrialError(secret)),
            safe_trial_rejection_reason(RuntimeError(secret)),
        ]))

    def test_live_trial_failure_is_categorized_but_keeps_quarantine(self):
        s3 = FixtureS3([obj("SECRET_ORIGINAL_DEVICE_AND_ROUTE", 0)])
        candidate = group_routes(s3.objects, T)[0]
        s3.reports[route_key(candidate)] = {"status": "quarantined"}
        s3.reports[clock_audit_key(candidate)] = {
            "status": "clock_order_audited",
            "inversionsOverHalfSecond": 1,
        }
        def reader(_):
            yield source_header()
            yield car_state(10)
            yield car_state(13)
            yield car_state(10.1)  # >2s watermark lateness
        result = run(s3, "synthetic-bucket", T, reader)
        self.assertEqual(result["status"], "bounded_trial_rejected")
        self.assertEqual(result["reason"], "lateness_exceeds_window")
        self.assertEqual(s3.reports[bounded_trial_key(candidate)]["reason"],
                         "lateness_exceeds_window")
        self.assertEqual(s3.reports[route_key(candidate)]["status"], "quarantined")
        self.assertNotIn("SECRET_ORIGINAL_DEVICE_AND_ROUTE", json.dumps(s3.reports))
        self.assertNotIn("latest.json", json.dumps(s3.reports))


if __name__ == "__main__":
    unittest.main()
