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
                       "rssShmemKiB": 25000+i*1000})
    events.append({"service": "sidecar_health", "wallElapsedS": 120.01,
                   "windowCpuPercentOneCore": None, "rssAnonKiB": 30125,
                   "rssShmemKiB": 37100})
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

    def test_invalid_thresholds_fail_fast(self):
        for x in [float('nan'), float('inf'), 0, -1]:
            with self.assertRaises(ValueError): assess(fixture(), cpu_limit=x)


if __name__ == '__main__':
    unittest.main()
