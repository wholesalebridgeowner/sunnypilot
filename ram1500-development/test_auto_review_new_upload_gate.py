"""Fixture tests for new-upload priority and assisted-steering comparison eligibility."""
from pathlib import Path
import unittest
from auto_review_job import run, group_routes, route_key, PREFIX
from test_auto_review import FixtureS3, obj, T, source_header, car_state, FakeMessage

def cc(t, active):
    return FakeMessage("carControl", t, {"latActive": active, "longActive": False})

class ReviewEligibilityTests(unittest.TestCase):
    def test_active_route_with_minor_boundary_overlap_is_reviewed(self):
        name="private|000003ef--synthetic"
        s3=FixtureS3([obj(name, 0), obj(name, 1)])
        candidate=group_routes(s3.objects, T)[0]
        def reader(path):
            yield source_header()
            if Path(path).parent.name == "0":
                yield car_state(10)
                yield cc(10.1, True)
                yield car_state(11.1)
            else:
                yield car_state(11.0)
                yield cc(11.2, True)
                yield car_state(12.0)
        result=run(s3, "private", T, reader)
        self.assertEqual(result["status"], "review_saved")
        self.assertTrue(result["assistedSteeringReviewEligible"])
        self.assertEqual(result["lateralActiveSamples"], 2)
        self.assertIn(PREFIX+"latest.json", s3.reports)
        saved=s3.reports[route_key(candidate)]
        self.assertEqual(saved["lateralActiveSamples"], 2)
        self.assertEqual(saved["eventTimeOrdering"]["version"], 2)
        self.assertNotIn("private|", str(result))

    def test_manual_drive_never_replaces_steering_baseline(self):
        name="private|000003ed--synthetic"
        s3=FixtureS3([obj(name, 0)])
        candidate=group_routes(s3.objects, T)[0]
        def reader(_):
            yield source_header()
            yield car_state(10)
            yield cc(10.1, False)
        result=run(s3, "private", T, reader)
        self.assertEqual(result["status"], "review_saved")
        self.assertFalse(result["assistedSteeringReviewEligible"])
        self.assertEqual(result["lateralActiveSamples"], 0)
        self.assertNotIn(PREFIX+"latest.json", s3.reports)
        self.assertFalse(s3.reports[route_key(candidate)]["assistedSteeringReviewEligible"])

    def test_fresh_upload_precedes_old_quarantine_diagnostics(self):
        old="private|000003e0--older"
        new="private|000003ef--recent"
        s3=FixtureS3([obj(old, 0, hours_ago=24),
                      obj(new, 0, hours_ago=8)])
        old_candidate=next(x for x in group_routes(s3.objects, T) if x["route"]==old)
        s3.reports[route_key(old_candidate)]={"status":"quarantined"}
        def reader(_):
            yield source_header()
            yield car_state(10)
            yield cc(10.1, True)
        result=run(s3, "private", T, reader)
        self.assertEqual(result["status"], "review_saved")
        self.assertEqual(s3.reports[route_key(old_candidate)]["status"], "quarantined")

if __name__=="__main__":
    unittest.main()
