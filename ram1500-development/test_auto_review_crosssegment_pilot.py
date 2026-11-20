"""Private, fixture-only tests of the one-shot intersegment pilot."""
import json
from pathlib import Path
import unittest

from auto_review_crosssegment_pilot import run, choose_candidate, TRIAL_PREFIX
from auto_review_job import group_routes, route_key, bounded_trial_key, PREFIX
from test_auto_review import FixtureS3, obj, source_header, car_state, T

class PrivatePilotTests(unittest.TestCase):
    def fixture(self):
        key = "secret-source|000003d8--synthetic"
        s3 = FixtureS3([obj(key, 0), obj(key, 1)])
        candidate = group_routes(s3.objects, T)[0]
        s3.reports[route_key(candidate)] = {"status": "quarantined", "reason": "decode_or_order_invalid"}
        s3.reports[bounded_trial_key(candidate)] = {"status": "trial_rejected",
                                                      "reason": "segment_boundary_regression"}
        return s3, candidate

    def test_valid_source_overlaps_produce_trial_but_not_latest(self):
        s3, candidate = self.fixture()
        def reader(path):
            seg = int(Path(path).parent.name)
            yield source_header()
            if seg == 0:
                yield car_state(10)
                yield car_state(11.1)
            else:
                yield car_state(11.0)
                yield car_state(12.0)
        outcome = run(s3, "synthetic", T, reader)
        self.assertEqual(outcome["status"], "experimental_intersegment_metrics_saved")
        self.assertEqual(outcome["carStateSamples"], 4)
        key = TRIAL_PREFIX + candidate["hash"] + "/" + candidate["fingerprint"] + ".json"
        self.assertEqual(s3.reports[key]["clockCounters"]["verifiedSegments"], 2)
        self.assertFalse(s3.reports[key]["releaseAuthorized"])
        self.assertEqual(s3.reports[route_key(candidate)]["status"], "quarantined")
        self.assertNotIn(PREFIX + "latest.json", s3.reports)
        self.assertNotIn("secret-source", json.dumps(s3.reports))

    def test_late_event_is_rejected_without_source_details(self):
        s3, candidate = self.fixture()
        def reader(path):
            yield source_header()
            yield car_state(14 if Path(path).parent.name == "0" else 10)
        outcome = run(s3, "synthetic", T, reader)
        self.assertEqual(outcome["status"], "experimental_intersegment_rejected")
        self.assertEqual(outcome["reason"], "lateness-exceeds-window")
        self.assertEqual(s3.reports[route_key(candidate)]["status"], "quarantined")
        self.assertNotIn("secret-source", str(outcome))

    def test_no_matching_existing_quarantine_returns_no_op(self):
        s3, _ = self.fixture()
        s3.reports.clear()
        self.assertIsNone(choose_candidate(s3, "synthetic", T))
        def reader(_):
            raise AssertionError("No reads expected")
        self.assertEqual(run(s3, "synthetic", T, reader)["status"],
                         "no_eligible_quarantined_boundary_routes")

if __name__ == "__main__":
    unittest.main()
