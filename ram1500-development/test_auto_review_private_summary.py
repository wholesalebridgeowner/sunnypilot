"""Privacy regression tests for printed aggregate trial summaries."""
import json
import unittest
from auto_review_private_summary import public_metric_summary, run

def valid():
    return {
        "status":"experimental_intersegment_metrics",
        "segments":54,
        "sourceVerificationComplete":True,
        "routeHash":"DO_NOT_LOG_ROUTE_HASH",
        "metrics":{
           "coverage":{"carStateSamples":32290},
           "laneChange":{"completeStartingWindows":0},
           "steering":{"steerSaturatedEventEpisodes":0, "lateralActiveSamples":10000,
                       "p90AbsSteeringRateDegPerSecond":8.21},
           "comfort":{"p90AbsJerkMps3":1.33},
           "leadHeadway":{"eligibleCruiseSamples":31, "medianSeconds":2.1},
           "privateLocation":"DO_NOT_LOG_GPS"
        }
    }

class SummaryTests(unittest.TestCase):
    def test_only_numeric_allowlisted_output(self):
        summary = public_metric_summary(valid())
        self.assertEqual(summary["carStateSamples"], 32290)
        self.assertEqual(summary["qualifiedHeadwaySamples"], 31)
        self.assertNotIn("DO_NOT_LOG", json.dumps(summary))
        self.assertNotIn("routeHash", summary)
        self.assertNotIn("privateLocation", summary)

    def test_invalid_or_unverified_reports_are_excluded(self):
        for value in (None, {}, {"status":"quarantined"}):
            self.assertIsNone(public_metric_summary(value))
        no_source = valid()
        no_source["sourceVerificationComplete"] = False
        self.assertIsNone(public_metric_summary(no_source))
        no_segments = valid()
        no_segments["segments"] = None
        self.assertIsNone(public_metric_summary(no_segments))

    def test_nonfinite_values_are_excluded(self):
        bad = valid()
        bad["metrics"]["comfort"]["p90AbsJerkMps3"] = float("nan")
        summary = public_metric_summary(bad)
        self.assertIsNone(summary["p90AbsObservedAccelerationJerk"])

class FakeS3:
    def get_paginator(self, _):
        return self
    def paginate(self, **_):
        yield {"Contents":[{"Key":"safe/a.json","Size":100},{"Key":"safe/b.json","Size":100}]}
    def get_object(self, *, Key, **_):
        from types import SimpleNamespace
        body=valid()
        if Key.endswith("b.json"):
            body["status"]="quarantined"
        return {"Body":SimpleNamespace(read=lambda:json.dumps(body).encode())}

class RunTests(unittest.TestCase):
    def test_stdout_payload_never_includes_secret_fields(self):
        value=run(FakeS3(),"private")
        self.assertEqual(value["acceptedExperimental"],1)
        self.assertEqual(value["skipped"],1)
        self.assertFalse(value["validForDeployment"])
        self.assertNotIn("DO_NOT_LOG",json.dumps(value))

if __name__=="__main__":
    unittest.main()
