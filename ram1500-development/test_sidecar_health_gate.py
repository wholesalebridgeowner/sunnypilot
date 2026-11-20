"""Synthetic-only validation for the passive Comma health gate."""
import unittest

from sidecar_health_gate import assess


def fixture():
    before = {"can": 8, "carState": 14, "carOutput": 4}
    after = {k: v + 1 for k, v in before.items()}
    events = [{"service": "sidecar_start", "readerCountsBefore": before,
               "allowMoving": False, "allCanIds": False, "sampleHz": 2}]
    for i in range(1, 13):
        events.append({"service": "sidecar_health", "wallElapsedS": i*10.0,
                       "windowCpuPercentOneCore": 8.0, "rssAnonKiB": 30000+i*10,
                       "rssShmemKiB": 25000+i*1000,
                       "rows": {"carState": 20*i, "can": 1000*i}})
    events.append({"service": "sidecar_health", "wallElapsedS": 120.01,
                   "windowCpuPercentOneCore": None, "rssAnonKiB": 30125,
                   "rssShmemKiB": 37100,
                   "rows": {"carState": 240, "can": 12000}})
    events.append({"service": "sidecar_stop", "readerCountsAfter": after,
                   "reason": "duration reached"})
    return events


class AuditTests(unittest.TestCase):
    def test_clean_parked_session_is_only_metrics_pass(self):
        r = assess(fixture())
        self.assertEqual(r["metricGate"], "passed")
        self.assertEqual(r["status"], "metrics_pass_other_gates_pending")
        self.assertGreater(len(r["separateGatesNotEvaluated"]), 0)
        self.assertEqual(r["cpuWindows"], 12)

    def test_cpu_one_spike_fails_strict_pilot_criterion(self):
        s = fixture(); s[4]["windowCpuPercentOneCore"] = 21.6
        self.assertIn("sidecar_cpu_limit_exceeded", assess(s)["reasons"])

    def test_shared_memory_growth_not_private_leak(self):
        s = fixture(); s[-2]["rssShmemKiB"] = 600_000
        self.assertEqual(assess(s)["metricGate"], "passed")

    def test_private_memory_limit_exceeded(self):
        s = fixture(); s[6]["rssAnonKiB"] = 200*1024
        self.assertIn("private_memory_limit_exceeded", assess(s)["reasons"])

    def test_missing_memory_split_is_not_pass(self):
        s = fixture(); s[5].pop("rssAnonKiB")
        self.assertEqual(assess(s)["status"], "not_validated")

    def test_eviction_or_missing_reader_counts(self):
        s = fixture(); s[-1]["readerCountsAfter"]["carState"] = 0
        self.assertFalse(assess(s)["readersAddedWithoutEviction"])

    def test_incomplete_capture_is_not_pass(self):
        s = fixture(); s[-1]["reason"] = "self-limit: CPU"
        self.assertIn("capture_did_not_finish_normally", assess(s)["reasons"])

    def test_multiple_sessions_cannot_be_combined(self):
        s = fixture() + fixture()
        self.assertIn("incomplete_or_multiple_sessions", assess(s)["reasons"])

    def test_missing_cpu_windows_cannot_pass(self):
        s = fixture()
        for h in s[1:-1]: h["windowCpuPercentOneCore"] = None
        self.assertIn("insufficient_cpu_windows", assess(s)["reasons"])

    def test_missing_one_interior_cpu_window_fails_even_with_enough_others(self):
        s = fixture()
        s[5]["windowCpuPercentOneCore"] = None
        r = assess(s)
        self.assertEqual(r["status"], "not_validated")
        self.assertIn("missing_or_invalid_periodic_cpu_window", r["reasons"])
        self.assertNotIn("insufficient_cpu_windows", r["reasons"])

    def test_terminal_health_requires_unwindowed_cpu_row(self):
        s = fixture()
        s[-2]["windowCpuPercentOneCore"] = 8.0
        self.assertIn("unwindowed_terminal_health_missing", assess(s)["reasons"])

    def test_terminal_health_must_have_explicit_none_not_missing_field(self):
        s = fixture()
        s[-2].pop("windowCpuPercentOneCore")
        self.assertIn("unwindowed_terminal_health_missing", assess(s)["reasons"])

    def test_terminal_health_can_share_rounded_clock_with_last_periodic(self):
        s = fixture()
        s[-2]["wallElapsedS"] = s[-3]["wallElapsedS"]
        self.assertEqual(assess(s)["metricGate"], "passed")

    def test_late_clustered_samples_cannot_fake_full_session_cpu_coverage(self):
        s = fixture()
        # Twelve nominal CPU windows packed into the last ~20s would pass
        # old count, span and gap checks despite no startup observations.
        for i, h in enumerate(s[1:-2]):
            h["wallElapsedS"] = 100.0 + 19.0 * i / 11
        r = assess(s)
        self.assertIn("initial_health_window_missing", r["reasons"])
        self.assertEqual(r["metricGate"], "failed_or_incomplete")

    def test_out_of_order_or_gap_cannot_pass(self):
        s = fixture(); s[6]["wallElapsedS"] = 999
        self.assertIn("invalid_health_clock", assess(s)["reasons"])
        s = fixture(); s[4]["wallElapsedS"] = 50
        self.assertIn("invalid_health_clock", assess(s)["reasons"])

    def test_raw_can_records_are_rejected_not_echoed(self):
        s = fixture(); s.insert(2, {"service": "can", "privateIdentifier": "SECRET",
                                    "packets": [{"dataHex": "deadbeef"}]})
        result = assess(s)
        self.assertEqual(result["status"], "indeterminate")
        self.assertNotIn("SECRET", str(result)); self.assertNotIn("deadbeef", str(result))

    def test_unapproved_moving_or_all_ids_or_rate(self):
        for field, value in [("allowMoving", True), ("allCanIds", True), ("sampleHz", 20)]:
            s = fixture(); s[0][field] = value
            self.assertNotEqual(assess(s)["metricGate"], "passed")

    def test_no_boolean_masquerade_as_numbers(self):
        s = fixture(); s[2]["rssAnonKiB"] = True
        self.assertIn("split_memory_metrics_missing_or_invalid", assess(s)["reasons"])

    def test_nominal_2hz_traffic_is_representative(self):
        s = fixture()
        r = assess(s)
        self.assertEqual(r["metricGate"], "passed")
        self.assertTrue(r["requestedRateEvidenceConfirmed"])

    def test_low_volume_counters_must_not_pass_as_nominal_load(self):
        s = fixture()
        # Every CPU window progresses, but only one carState/10s at 2 Hz.
        for i, h in enumerate(s[1:-2], start=1):
            h["rows"]["carState"] = i
        s[-2]["rows"]["carState"] = 12
        r = assess(s)
        self.assertTrue(r["telemetryStreamProgressConfirmed"])
        self.assertFalse(r["requestedRateEvidenceConfirmed"])
        self.assertIn("carstate_rate_not_representative", r["reasons"])

    def test_one_underloaded_window_fails_even_if_overall_volume_high(self):
        s = fixture()
        s[6]["rows"]["carState"] = s[5]["rows"]["carState"] + 1
        r = assess(s)
        self.assertIn("carstate_rate_not_representative", r["reasons"])
        self.assertNotIn("parked_capture_stream_progress_missing", r["reasons"])

    def test_catchup_burst_does_not_count_as_a_full_cpu_window(self):
        s = fixture()
        # Window at 11s after one at 10s, later windows still cover 120s.
        s[2]["wallElapsedS"] = 11.0
        r = assess(s)
        self.assertIn("cpu_window_duration_too_short", r["reasons"])
        self.assertNotEqual(r["metricGate"], "passed")

    def test_boolean_sample_rate_rejected(self):
        s = fixture()
        s[0]["sampleHz"] = True
        self.assertIn("unreviewed_sample_rate", assess(s)["reasons"])

    def test_legitimate_1hz_carstate_traffic_passes_sampling_gate(self):
        s = fixture()
        s[0]["sampleHz"] = 1
        for i, h in enumerate(s[1:-2], start=1):
            h["rows"]["carState"] = i * 10
        s[-2]["rows"]["carState"] = 120
        self.assertEqual(assess(s)["metricGate"], "passed")

    def test_idle_sidecar_cannot_pass_resource_evidence(self):
        s = fixture()
        for h in s[2:-1]:
            h["rows"] = dict(s[1]["rows"])
        r = assess(s)
        self.assertEqual(r["status"], "not_validated")
        self.assertIn("parked_capture_stream_progress_missing", r["reasons"])
        self.assertFalse(r["telemetryStreamProgressConfirmed"])

    def test_partial_capture_freeze_is_rejected(self):
        s = fixture()
        s[6]["rows"] = dict(s[5]["rows"])
        r = assess(s)
        self.assertIn("parked_capture_stream_progress_missing", r["reasons"])

    def test_decode_errors_in_any_window_fail_even_if_cpu_low(self):
        s = fixture()
        for h in s[5:-1]:
            h["rows"]["decode_errors"] = 1
        r = assess(s)
        self.assertIn("telemetry_decode_errors_observed", r["reasons"])
        self.assertFalse(r["decodeErrorFree"])
        self.assertEqual(r["cpuPeakPercentOneCore"], 8.0)

    def test_invalid_missing_and_boolean_counters_fail_closed(self):
        for modify in (
            lambda h: h.pop("rows"),
            lambda h: h["rows"].update({"carState": True}),
            lambda h: h["rows"].update({"can": -1}),
            lambda h: h["rows"].update({"unknown": 1}),
        ):
            s = fixture()
            modify(s[5])
            self.assertIn("missing_or_invalid_telemetry_counters", assess(s)["reasons"])

    def test_counter_rollback_invalidates_session(self):
        s = fixture()
        s[7]["rows"]["carState"] = 1
        self.assertIn("telemetry_counter_regression", assess(s)["reasons"])

    def test_terminal_counters_may_equal_last_periodic(self):
        s = fixture()
        self.assertEqual(s[-2]["rows"], s[-3]["rows"])
        r = assess(s)
        self.assertEqual(r["metricGate"], "passed")
        self.assertTrue(r["telemetryStreamProgressConfirmed"])
        self.assertTrue(r["decodeErrorFree"])

    def test_counter_metadata_does_not_echo_sensitive_inputs(self):
        s = fixture()
        s[4]["rows"]["privateIdentifier"] = "SECRET-VEHICLE"
        r = assess(s)
        self.assertIn("missing_or_invalid_telemetry_counters", r["reasons"])
        self.assertNotIn("SECRET-VEHICLE", str(r))

    def test_invalid_thresholds_fail_fast(self):
        for x in [float('nan'), float('inf'), 0, -1]:
            with self.assertRaises(ValueError): assess(fixture(), cpu_limit=x)


if __name__ == '__main__':
    unittest.main()
