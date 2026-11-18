"""Offline OEM park steering context tests, no vehicle or raw CAN required."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("park_motion",Path(__file__).with_name("park_assist_motion_context.py"))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Tests(unittest.TestCase):
    def samples(self):
        ns=1_000_000_000
        # First carState precedes the CAN anchor; both analyzers anchor at first CAN.
        yield 4*ns,"carState",{"vEgo":10,"steeringAngleDeg":999}
        yield 5*ns,"can",{}
        for i in range(30):
            t=int((5+i*.1)*ns)
            yield t,"carState",{"vEgo":.2,"steeringAngleDeg":10+i*.3,
                                "steeringTorqueEps":3.}
            yield t,"carControl",{"latActive":False}
            yield t,"carOutput",{"torqueOutputCan":0}
        yield 13*ns,"can",{}
        for i in range(30):
            t=int((13+i*.1)*ns)
            yield t,"carState",{"vEgo":0.,"steeringAngleDeg":-15+i*1.5,
                                "steeringTorqueEps":40.}
            yield t,"carControl",{"latActive":False}
            yield t,"carOutput",{"torqueOutputCan":0}

    def test_zero_speed_steering_does_not_require_lkas_activation(self):
        report=m.describe(self.samples(),(0,4),(8,12))
        baseline=report["normal"]
        parking=report["factoryParking"]
        self.assertEqual(baseline["carStateSamples"],30)
        self.assertEqual(parking["carStateSamples"],30)
        self.assertEqual(parking["reportedStopSamples"],30)
        self.assertGreater(parking["steeringAngleRangeDeg"],40)
        self.assertEqual(parking["carControlLateralActiveCounts"],{"False":30})
        self.assertEqual(parking["maxAbsCommaTorqueCommandNative"],0)
        self.assertIn("NOT an inferred",str(report["caveats"]))
        self.assertNotIn("999",str(report))

    def test_windows_do_not_overlap(self):
        with self.assertRaisesRegex(ValueError,"overlap"):
            m.describe(self.samples(),(0,9),(8,12))

    def test_empty_and_invalid_measurement(self):
        out=m.describe([(0,"can",{})],(0,4),(8,12))
        self.assertEqual(out["normal"]["carStateSamples"],0)
        self.assertIsNone(out["factoryParking"]["steeringAngleRangeDeg"])
        self.assertIsNone(m.finite_number(float("nan")))


if __name__=="__main__":
    unittest.main()
