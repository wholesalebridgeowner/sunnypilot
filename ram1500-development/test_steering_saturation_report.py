"""Small deterministic synthetic tests; no Comma device or production logs."""
import importlib.util
from pathlib import Path
import unittest

p = Path(__file__).with_name("steering_saturation_report.py")
spec = importlib.util.spec_from_file_location("steering_saturation_report", p)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def rec(t, stream, **kwargs):
    return {"monoTimeNs": str(t), "stream": stream, "valid": "True", **kwargs}


class TestAlignment(unittest.TestCase):
    def test_past_only_tolerances_and_limit_counts(self):
        rows = [rec(1_000_000_000, "carState", vEgo_mps="20", steeringTorqueEps="190", steeringPressed="False"),
                rec(1_010_000_000, "carControl", requestedTorque="0.99"),
                rec(1_020_000_000, "carOutput", outputTorqueCan="345"),
                rec(1_040_000_000, "controlsState", lateralSaturated="True", lateralError="0.4"),
                rec(1_050_000_000, "onroadEvents", event="steerSaturated"),
                rec(1_300_000_000, "controlsState", lateralSaturated="False"),
                rec(1_310_000_000, "carState", vEgo_mps="21", steeringTorqueEps="200"),
                rec(1_320_000_000, "onroadEvents", event="steerSaturated")]
        r = a.analyze(rows, max_age_ms=100)
        self.assertEqual(r["controllerSamples"], 2)
        self.assertEqual(r["groups"]["controllerSaturated"]["nearReportedCommandLimitSamples"], 1)
        self.assertEqual(r["groups"]["controllerSaturated"]["metrics"]["requestedVsCommandedGapNative"]["count"], 1)
        self.assertAlmostEqual(r["groups"]["controllerSaturated"]["metrics"]["requestedVsCommandedGapNative"]["median"], 1.5)
        self.assertEqual(r["sourceMatchedWithinAgeSamples"], {"carState":1,"carControl":1,"carOutput":1})
        self.assertEqual(r["eventSampleCount"], 2)
        self.assertEqual(r["eventEpisodeCount"], 1)
        self.assertTrue(r["events"][0]["controllerMatched"])
        self.assertEqual(r["events"][0]["nearestOutputTorqueCan"],345)

    def test_no_future_lookahead(self):
        rows = [rec(1_000_000_000,"controlsState",lateralSaturated="True"),
                rec(1_010_000_000,"carOutput",outputTorqueCan="350")]
        r=a.analyze(rows,max_age_ms=50)
        self.assertEqual(r["sourceMatchedWithinAgeSamples"],{})
        self.assertEqual(r["groups"]["controllerSaturated"]["metrics"]["outputTorqueCan"]["count"],0)

    def test_invalid_input_and_nonfinite(self):
        rows=[rec(1_000_000_000,"controlsState",lateralError="NaN",lateralSaturated="True"),
              rec("n/a","carOutput"),rec(999,"carState",valid="False")]
        r=a.analyze(rows)
        self.assertEqual(r["discardedRows"],{"invalidTimestamp":1,"invalidMessage":1})
        self.assertEqual(r["groups"]["controllerSaturated"]["metrics"]["lateralError"]["count"],0)
        with self.assertRaises(ValueError):
            a.analyze([],max_age_ms=0)

    def test_empty_and_episodes(self):
        r=a.analyze([])
        self.assertEqual(r["eventEpisodeCount"],0)
        self.assertIsNone(r["groups"]["allControllerStateSamples"]["metrics"]["lateralError"]["median"])
        self.assertEqual(len(a.grouped_episodes([1,1_000_000_001,3_000_000_000])),2)

if __name__ == "__main__":
    unittest.main()
