"""Offline acceptance-gate tests. No Comma access, no private route contents."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from parked_capture_acceptance import (
    BLOCKING_EVENTS, REQUIRED_SERVICES, validate_rows, capture_rows,
)


def good_capture():
    before = {service: 3 for service in REQUIRED_SERVICES}
    before["carState"] = 14  # actual parked Comma baseline
    after = {key: value + 1 for key, value in before.items()}
    rows = [{
        "service": "sidecar_start", "monoTimeNs": 1,
        "readerCountsBefore": before, "allowMoving": False,
        "allCanIds": False, "sampleHz": 2,
    }]
    for i in range(12):
        rows.append({
            "service": "sidecar_health", "monoTimeNs": 2 + i,
            "windowCpuPercentOneCore": 10.0 if i < 11 else None,
            "rssAnonKiB": (35 + i) * 1024,
            "wallElapsedS": 10 * (i + 1),
            "rows": {"carState": 23 * (i + 1)}
        })
    for i in range(250):
        rows.append({"service": "carState", "monoTimeNs": 100+i,
                     "vEgo": 0.0, "canValid": True})
    rows.append({"service": "can", "monoTimeNs": 600, "packets": [
        {"bus": 0, "id": 49, "dataHex": "00000000"}
    ]})
    rows.append({"service": "onroadEvents", "monoTimeNs": 610,
                 "events": ["wrongGear", "belowSteerSpeed"]})
    rows.append({"service": "sidecar_stop", "monoTimeNs": 700,
                 "reason": "duration reached", "readerCountsAfter": after})
    return rows


class AcceptanceTests(unittest.TestCase):
    def test_accepts_realistic_parked_capture(self):
        report = validate_rows(good_capture())
        self.assertTrue(report["passed"], report["blockers"])
        self.assertEqual(report["status"], "parked_diagnostic_accepted")
        self.assertEqual(report["coverage"]["verifiedSubscriberIncrements"], len(REQUIRED_SERVICES))
        self.assertFalse(report["vehicleReleaseAuthorized"])

    def test_rejects_high_cpu_and_no_release_even_if_otherwise_valid(self):
        rows = good_capture()
        rows[1]["windowCpuPercentOneCore"] = 21.6
        report = validate_rows(rows)
        self.assertIn("cpu_overhead_above_pilot_limit", report["blockers"])
        self.assertFalse(report["passed"])
        self.assertFalse(report["controllerSoftwareValidated"])

    def test_rejects_oversubscribed_queue(self):
        rows = good_capture()
        rows[-1]["readerCountsAfter"]["carState"] = 16
        report = validate_rows(rows)
        self.assertIn("subscriber_count_change_invalid", report["blockers"])

    def test_rejects_any_movement_and_unexpected_fault(self):
        rows = good_capture()
        rows[14]["vEgo"] = 0.5  # remains a carState row after 1 start + 12 health
        rows.append({"service": "onroadEvents", "monoTimeNs": 900,
                     "events": ["canError", "steerSaturated"]})
        report = validate_rows(rows)
        self.assertIn("vehicle_moved_during_capture", report["blockers"])
        self.assertIn("fault_event_observed", report["blockers"])
        self.assertEqual(report["observedFaultEventSampleCounts"]["canError"], 1)

    def test_rejects_no_health_stop_or_unbounded_settings(self):
        rows = good_capture()
        rows[0]["allowMoving"] = True
        rows[0]["allCanIds"] = True
        rows.pop()  # missing sidecar_stop
        report = validate_rows(rows)
        for code in ("moving_capture_not_permitted", "unbounded_can_capture_not_permitted",
                     "capture_must_have_one_start_and_stop"):
            self.assertIn(code, report["blockers"])

    def test_rejects_private_rss_excess_and_can_invalid(self):
        rows = good_capture()
        rows[2]["rssAnonKiB"] = 180 * 1024
        rows[13]["canValid"] = False
        report = validate_rows(rows)
        self.assertIn("private_memory_above_pilot_limit", report["blockers"])
        self.assertIn("invalid_can_state", report["blockers"])

    def test_processes_jsonl_without_echoing_input_privacy_fields(self):
        rows = good_capture()
        rows[13]["vin"] = "PRIVATE_VIN_DO_NOT_LOG"
        rows[13]["location"] = "PRIVATE_COORDINATES_DO_NOT_LOG"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private_capture.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            report = validate_rows(capture_rows([path]))
            self.assertTrue(report["passed"])
            self.assertNotIn("PRIVATE_", json.dumps(report))
            self.assertNotIn("private_capture", json.dumps(report))

    def test_rejects_invalid_record_without_exception_details(self):
        rows = good_capture()
        rows.append(None)
        report = validate_rows(rows)
        self.assertFalse(report["passed"])
        self.assertIn("malformed_capture_record", report["blockers"])


if __name__ == "__main__":
    unittest.main()
