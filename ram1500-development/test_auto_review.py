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
                             regression_origin_key, anonymous_clock_counts, MAX_COMPRESSED, pinned_qlog_messages,
                             SourcePinError, run, PREFIX, safe_trial_rejection_reason)
from auto_review_clock_audit import audit_clock_order, ClockAuditSourceError
from auto_review_bounded_order import source_pinned_bounded_messages, OrderingTrialError

T = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)

def obj(route, segment, hours_ago=12, size=100, etag="v1"):
    return {"Key": f"logs/{route}/{segment}/qlog.zst",
            "Size": size, "LastModified": T - timedelta(hours=hours_ago),
            "ETag": etag}

class AnalysisTests(unittest.TestCase):
    @staticmethod
    def healthy_car(rate=1.5, **overrides):
        row = {"vEgo": 20.0, "aEgo": 0.2, "gasPressed": False,
               "brakePressed": False, "cruiseState": {"enabled": True},
               "canValid": True, "steerFaultTemporary": False,
               "steerFaultPermanent": False, "steeringPressed": False,
               "steeringRateDeg": rate}
        row.update(overrides)
        return row

    def test_measures_lane_change_headway_and_discrete_alert(self):
        a = RouteMetrics()
        a.ingest("carParams", 0, {"openpilotLongitudinalControl": False, "pcmCruise": True})
        a.ingest("carState", 1, self.healthy_car())
        a.ingest("radarState", 1.1, {"leadOne": {"status": True, "dRel": 32}})
        a.ingest("carControl", 1.12, {"latActive": True})
        a.ingest("carState", 1.13, self.healthy_car())
        a.ingest("drivingModelData", 1.15, {"meta": {
            "laneChangeState": "laneChangeStarting", "laneChangeDirection": "left"}})
        for i in range(12, 25):
            t = i / 10
            a.ingest("carState", t, self.healthy_car(rate=4.5 if i == 12 else 1.5))
            a.ingest("carControl", t + .01, {"latActive": True})
            if i in (13, 14):
                a.ingest("onroadEvents", t + .02, ["steerSaturated"])
        a.ingest("carState", 2.45, self.healthy_car())
        a.ingest("carControl", 2.46, {"latActive": True})
        a.ingest("drivingModelData", 2.5, {"meta": {
            "laneChangeState": "laneChangeFinishing", "laneChangeDirection": "left"}})
        a.ingest("onroadEvents", 2.6, [])
        a.ingest("onroadEvents", 3.1, ["steerSaturated"])
        result = a.report()
        self.assertEqual(result["leadHeadway"]["medianSeconds"], 1.6)
        self.assertEqual(result["laneChange"]["completeStartingWindows"], 1)
        self.assertEqual(result["laneChange"]["p90PeakSteeringRateDegPerSecond"], 4.5)
        self.assertEqual(result["laneChange"]["rejectedModelWindows"], {})
        self.assertEqual(result["steering"]["steerSaturatedEventEpisodes"], 2)
        self.assertFalse(result["cruiseOwnership"]["carParams"]["openpilotLongitudinalControl"])
        self.assertNotIn("route", result)

    def test_model_phase_without_comma_control_never_counts(self):
        a = RouteMetrics()
        a.ingest("carState", 1, self.healthy_car())
        a.ingest("drivingModelData", 1.05, {"meta": {
            "laneChangeState": "laneChangeStarting", "laneChangeDirection": "left"}})
        a.ingest("carState", 1.1, self.healthy_car(rate=7))
        a.ingest("drivingModelData", 1.15, {"meta": {
            "laneChangeState": "laneChangeFinishing", "laneChangeDirection": "left"}})
        self.assertEqual(a.report()["laneChange"]["completeStartingWindows"], 0)
        self.assertEqual(a.report()["laneChange"]["rejectedModelWindows"],
                         {"missing_active_or_healthy_evidence": 1})

    def test_lateral_disengagement_mid_window_is_irreversible(self):
        a = RouteMetrics()
        a.ingest("carState", 1, self.healthy_car())
        a.ingest("carControl", 1.01, {"latActive": True})
        a.ingest("drivingModelData", 1.04, {"meta": {
            "laneChangeState": "laneChangeStarting", "laneChangeDirection": "right"}})
        a.ingest("carState", 1.1, self.healthy_car(rate=8))
        a.ingest("carControl", 1.11, {"latActive": False})
        a.ingest("carState", 1.2, self.healthy_car())
        a.ingest("carControl", 1.21, {"latActive": True})
        a.ingest("drivingModelData", 1.24, {"meta": {
            "laneChangeState": "laneChangeFinishing", "laneChangeDirection": "right"}})
        self.assertEqual(a.report()["laneChange"]["completeStartingWindows"], 0)

    def test_stale_lateral_or_health_blocks_model_window(self):
        for stale in ("lateral", "health"):
            with self.subTest(stale=stale):
                a = RouteMetrics()
                a.ingest("carState", 1, self.healthy_car())
                a.ingest("carControl", 1.01, {"latActive": True})
                a.ingest("drivingModelData", 1.04, {"meta": {
                    "laneChangeState": "laneChangeStarting", "laneChangeDirection": "left"}})
                if stale == "lateral":
                    a.ingest("carState", 1.3, self.healthy_car(rate=6))
                else:
                    a.ingest("carControl", 1.3, {"latActive": True})
                a.ingest("drivingModelData", 1.32, {"meta": {
                    "laneChangeState": "laneChangeFinishing", "laneChangeDirection": "left"}})
                self.assertEqual(a.report()["laneChange"]["completeStartingWindows"], 0)

    def test_unsafe_can_eps_or_driver_input_disqualifies_window(self):
        for change in ({"canValid": False}, {"steerFaultTemporary": True},
                       {"steerFaultPermanent": True}, {"steeringPressed": True},
                       {"canValid": None}):
            with self.subTest(change=change):
                a = RouteMetrics()
                a.ingest("carState", 1, self.healthy_car())
                a.ingest("carControl", 1.01, {"latActive": True})
                a.ingest("drivingModelData", 1.04, {"meta": {
                    "laneChangeState": "laneChangeStarting", "laneChangeDirection": "left"}})
                a.ingest("carState", 1.1, self.healthy_car(rate=6, **change))
                a.ingest("carControl", 1.11, {"latActive": True})
                a.ingest("drivingModelData", 1.14, {"meta": {
                    "laneChangeState": "laneChangeFinishing", "laneChangeDirection": "left"}})
                self.assertEqual(a.report()["laneChange"]["completeStartingWindows"], 0)

    def test_model_phase_aborted_never_counts_as_crossing(self):
        a = RouteMetrics()
        a.ingest("carState", 1, self.healthy_car())
        a.ingest("carControl", 1.01, {"latActive": True})
        a.ingest("drivingModelData", 1.04, {"meta": {
            "laneChangeState": "laneChangeStarting", "laneChangeDirection": "left"}})
        a.ingest("carState", 1.1, self.healthy_car(rate=3))
        a.ingest("carControl", 1.11, {"latActive": True})
        a.ingest("drivingModelData", 1.14, {"meta": {
            "laneChangeState": "off", "laneChangeDirection": "left"}})
        self.assertEqual(a.report()["laneChange"]["completeStartingWindows"], 0)
        self.assertEqual(a.report()["laneChange"]["rejectedModelWindows"],
                         {"model_phase_interrupted": 1})

    def test_old_lane_metrics_are_not_comparable_to_qualified_new_schema(self):
        a = RouteMetrics().report()
        old = {"laneChange": {"medianStartingSeconds": 1.0,
                               "p90PeakSteeringRateDegPerSecond": 10.0},
               "steering": {"steerSaturatedEventEpisodes": 2}}
        result = compare(a, old)
        self.assertNotIn("laneChange.medianStartingSeconds", result["metrics"])
        self.assertNotIn("laneChange.p90PeakSteeringRateDegPerSecond", result["metrics"])

    def test_prevents_false_headway_when_driver_presses_pedal(self):
        a = RouteMetrics()
        a.ingest("carState", 1, {"vEgo": 20, "gasPressed": True,
                 "brakePressed": False, "cruiseState": {"enabled": True}})
        a.ingest("radarState", 1.1, {"leadOne": {"status": True, "dRel": 40}})
        self.assertEqual(a.report()["leadHeadway"]["eligibleCruiseSamples"], 0)

    def test_comparison_has_no_unsupported_causal_claim(self):
        marker = "model_start_to_finish_fresh_active_healthy_qlog_v2"
        current = {"laneChange": {"qualification": marker, "medianStartingSeconds": 2.4},
                   "steering": {"steerSaturatedEventEpisodes": 2}}
        previous = {"laneChange": {"qualification": marker, "medianStartingSeconds": 3.1},
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
        first = run(s3, "fake", T, reversed_clock)
        self.assertEqual(first["status"], "route_quarantined")
        cand = group_routes(s3.objects, T)[0]
        self.assertEqual(s3.reports[route_key(cand)]["status"], "quarantined")
        second = run(s3, "fake", T, reversed_clock)
        self.assertEqual(second["status"], "clock_diagnostic_saved")
        self.assertEqual(second["diagnostic"], "clock_order_audited")
        self.assertEqual(second["anonymousClockCounts"]["inversionsOverHalfSecond"], 1)
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


class FactoryACCDataQualityTests(unittest.TestCase):
    """Anonymous synthetic observations; never use device data or identifiers."""

    @staticmethod
    def car(*, accel=0.2, cruise=True, gas=False, brake=False, can=True):
        return {"vEgo": 20.0, "aEgo": accel,
                "cruiseState": {"enabled": cruise}, "gasPressed": gas,
                "brakePressed": brake, "canValid": can}

    @staticmethod
    def configure(a, *, op_long=False, pcm=True, at=0):
        a.ingest("carParams", at, {
            "openpilotLongitudinalControl": op_long, "pcmCruise": pcm})

    def test_factory_qualified_headway_and_motion_are_observational(self):
        a = RouteMetrics()
        self.configure(a)
        a.ingest("carState", 1.0, self.car(accel=.2))
        a.ingest("carState", 1.1, self.car(accel=.3))
        a.ingest("radarState", 1.12,
                 {"leadOne": {"status": True, "dRel": 30.0}})
        result = a.report()
        self.assertTrue(result["cruiseOwnership"]["factoryAccSourceConsistent"])
        self.assertEqual(result["leadHeadway"]["eligibleCruiseSamples"], 1)
        self.assertEqual(result["leadHeadway"]["medianSeconds"], 1.5)
        self.assertEqual(result["factoryAccObservedMotion"]["eligibleJerkSamples"], 1)
        self.assertEqual(result["factoryAccObservedMotion"]["p90AbsObservedJerkMps3"], 1.0)
        self.assertIn("not OEM", result["leadHeadway"]["caution"])

    def test_no_headway_from_unknown_pedals_can_or_unverified_source(self):
        cases = [
            {"car": {"gas": None}}, {"car": {"brake": None}},
            {"car": {"gas": True}}, {"car": {"brake": True}},
            {"car": {"can": None}}, {"car": {"can": False}},
            {"car": {"cruise": False}}, {"source": {"op_long": True}},
            {"source": {"pcm": False}}, {"source": None},
        ]
        for case in cases:
            with self.subTest(case=case):
                a = RouteMetrics()
                if case.get("source", {}) is not None:
                    self.configure(a, **case.get("source", {}))
                a.ingest("carState", 1.0, self.car(**case.get("car", {})))
                a.ingest("carState", 1.1, self.car(**case.get("car", {})))
                a.ingest("radarState", 1.12,
                         {"leadOne": {"status": True, "dRel": 32.0}})
                result = a.report()
                self.assertEqual(result["leadHeadway"]["eligibleCruiseSamples"], 0)
                self.assertEqual(result["factoryAccObservedMotion"]["eligibleJerkSamples"], 0)

    def test_pedal_interruption_never_bridges_derivative(self):
        a = RouteMetrics()
        self.configure(a)
        a.ingest("carState", 1.0, self.car(accel=.1))
        a.ingest("carState", 1.1, self.car(accel=2.0, brake=True))
        a.ingest("carState", 1.2, self.car(accel=.3))
        self.assertEqual(a.report()["factoryAccObservedMotion"]["eligibleJerkSamples"], 0)
        a.ingest("carState", 1.3, self.car(accel=.5))
        self.assertEqual(a.report()["factoryAccObservedMotion"]["eligibleJerkSamples"], 1)

    def test_later_contradictory_carparams_invalidates_all_prior_factory_metrics(self):
        a = RouteMetrics()
        self.configure(a)
        a.ingest("carState", 1.0, self.car(accel=.1))
        a.ingest("carState", 1.1, self.car(accel=.2))
        a.ingest("radarState", 1.12, {"leadOne": {"status": True, "dRel": 25}})
        self.assertEqual(a.report()["leadHeadway"]["eligibleCruiseSamples"], 1)
        self.configure(a, op_long=True, pcm=False, at=1.2)
        r = a.report()
        self.assertTrue(r["cruiseOwnership"]["carParamsConflict"])
        self.assertFalse(r["cruiseOwnership"]["factoryAccSourceConsistent"])
        self.assertEqual(r["leadHeadway"]["eligibleCruiseSamples"], 0)
        self.assertIsNone(r["leadHeadway"]["medianSeconds"])
        self.assertEqual(r["factoryAccObservedMotion"]["eligibleJerkSamples"], 0)
        self.configure(a, at=1.3)  # restored values cannot erase contradictory provenance
        self.assertEqual(a.report()["leadHeadway"]["eligibleCruiseSamples"], 0)

    def test_factory_comparison_requires_same_version_and_provenance(self):
        def healthy():
            a = RouteMetrics()
            self.configure(a)
            a.ingest("carState", 1.0, self.car(accel=.1))
            a.ingest("carState", 1.1, self.car(accel=.2))
            a.ingest("radarState", 1.12,
                     {"leadOne": {"status": True, "dRel": 30}})
            return a.report()
        new = healthy()
        old = {"leadHeadway": {"medianSeconds": 0.2},
               "factoryAccObservedMotion": {"p90AbsObservedJerkMps3": 0.1}}
        self.assertNotIn("leadHeadway.medianSeconds", compare(new, old)["metrics"])
        self.assertNotIn("factoryAccObservedMotion.p90AbsObservedJerkMps3",
                         compare(new, old)["metrics"])
        other = healthy()
        self.assertIn("leadHeadway.medianSeconds", compare(new, other)["metrics"])
        self.assertIn("factoryAccObservedMotion.p90AbsObservedJerkMps3",
                      compare(new, other)["metrics"])
        other["cruiseOwnership"]["factoryAccSourceConsistent"] = False
        self.assertNotIn("leadHeadway.medianSeconds", compare(new, other)["metrics"])


class SegmentIsolationTests(unittest.TestCase):
    """No cross-file attribution even at timestamps closer than join limits."""

    paths = [Path("0/qlog.zst"), Path("1/qlog.zst")]

    @staticmethod
    def factory_car(time, accel):
        return FakeMessage("carState", time, {
            "vEgo": 20, "aEgo": accel, "canValid": True,
            "cruiseState": {"enabled": True},
            "gasPressed": False, "brakePressed": False,
            "steerFaultTemporary": False, "steerFaultPermanent": False,
            "steeringPressed": False, "steeringRateDeg": 5.0})

    @staticmethod
    def params(time=9.9):
        return FakeMessage("carParams", time, {
            "openpilotLongitudinalControl": False, "pcmCruise": True})

    def test_cross_segment_radar_lead_cannot_borrow_prior_carstate(self):
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header()
            if seg == 0:
                yield self.params()
                yield self.factory_car(10.0, .2)
            else:
                yield FakeMessage("radarState", 10.05,
                                  {"leadOne": {"status": True, "dRel": 30}})
        legacy = summarize_log_messages(pinned_qlog_messages(self.paths, reader))
        isolated = summarize_log_messages(
            pinned_qlog_messages(self.paths, reader, emit_boundaries=True))
        self.assertEqual(legacy["leadHeadway"]["eligibleCruiseSamples"], 1)
        self.assertEqual(isolated["leadHeadway"]["eligibleCruiseSamples"], 0)
        self.assertEqual(isolated["coverage"]["segmentBoundariesSeen"], 2)
        self.assertNotIn("0/qlog.zst", json.dumps(isolated))

    def test_cross_segment_acceleration_derivative_resets(self):
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header()
            if seg == 0:
                yield self.params()
                yield self.factory_car(10.0, .1)
            else:
                yield self.factory_car(10.1, .5)
        legacy = summarize_log_messages(pinned_qlog_messages(self.paths, reader))
        isolated = summarize_log_messages(
            pinned_qlog_messages(self.paths, reader, emit_boundaries=True))
        self.assertEqual(legacy["factoryAccObservedMotion"]["eligibleJerkSamples"], 1)
        self.assertEqual(isolated["factoryAccObservedMotion"]["eligibleJerkSamples"], 0)
        self.assertEqual(isolated["comfort"]["observedAccelerationJerkSamples"], 0)

    def test_cross_segment_lane_model_transition_is_not_a_maneuver(self):
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header()
            if seg == 0:
                yield self.factory_car(10, .1)
                yield FakeMessage("carControl", 10.01, {"latActive": True})
                yield FakeMessage("drivingModelData", 10.02, {
                    "meta": {"laneChangeState": "laneChangeStarting",
                             "laneChangeDirection": "left"}})
                yield self.factory_car(10.03, .1)
            else:
                yield self.factory_car(10.04, .1)
                yield FakeMessage("carControl", 10.05, {"latActive": True})
                yield FakeMessage("drivingModelData", 10.06, {
                    "meta": {"laneChangeState": "laneChangeFinishing",
                             "laneChangeDirection": "left"}})
        legacy = summarize_log_messages(pinned_qlog_messages(self.paths, reader))
        isolated = summarize_log_messages(
            pinned_qlog_messages(self.paths, reader, emit_boundaries=True))
        self.assertEqual(legacy["laneChange"]["completeStartingWindows"], 1)
        self.assertEqual(isolated["laneChange"]["completeStartingWindows"], 0)
        self.assertEqual(isolated["laneChange"]["rejectedModelWindows"],
                         {"segment_boundary_incomplete": 1})

    def test_bounded_replay_opt_in_resets_instead_of_mixing_segments(self):
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header()
            if seg == 0:
                yield self.params()
                yield self.factory_car(10, .1)
            else:
                yield FakeMessage("radarState", 10.02,
                                  {"leadOne": {"status": True, "dRel": 32}})
        counters = {}
        report = summarize_log_messages(source_pinned_bounded_messages(
            self.paths, reader, counters, emit_boundaries=True))
        self.assertEqual(counters["verifiedSegments"], 2)
        self.assertEqual(report["coverage"]["segmentBoundariesSeen"], 2)
        self.assertEqual(report["leadHeadway"]["eligibleCruiseSamples"], 0)

    def test_segment_reset_never_waives_clock_order(self):
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header()
            yield self.factory_car(10 if seg == 0 else 9.0, .1)
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            summarize_log_messages(
                pinned_qlog_messages(self.paths, reader, emit_boundaries=True))


if __name__ == "__main__":
    unittest.main()
