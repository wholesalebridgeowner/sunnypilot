"""Offline privacy and timing tests for the completed OEM parking R-log probe."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

spec=importlib.util.spec_from_file_location("ram_parking_probe",Path(__file__).with_name("private_parking_rlog_probe.py"))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class E:
    def __init__(self,name,**values):
        self.name=name
        self.payload=SimpleNamespace(**values)

    def which(self):
        return self.name

    def __getattr__(self,field):
        if field==self.name:
            return self.payload
        raise AttributeError(field)


class Tests(unittest.TestCase):
    def test_zero_speed_factory_angle_with_comma_inactive(self):
        summary=mod.Summary()
        for i in range(30):
            sec=1+i/10
            summary.observe(12,sec,E("carState",vEgo=0.,
                    steeringAngleDeg=-20+i*2,steeringTorqueEps=45,
                    steeringPressed=False,gearShifter="drive"))
            summary.observe(12,sec,E("carControl",latActive=False))
            summary.observe(12,sec,E("carOutput",actuatorsOutput=SimpleNamespace(torqueOutputCan=0)))
        r=summary.result()
        observed=[v for v in r["bins"] if v["relativeWindowSeconds"]==[720,725]][0]
        self.assertEqual(observed["stoppedSamplesLe0p3Mps"],30)
        self.assertGreater(observed["steeringAngleRangeDeg"],50)
        self.assertEqual(observed["commaLateralActiveSamples"],{"False":30})
        self.assertEqual(observed["commaNativeCommandAbsMax"],0)

    def test_selected_can_id_changes_are_aggregated_without_raw_payload(self):
        summary=mod.Summary()
        for i in range(12):
            frame=SimpleNamespace(src=2,address=0x777,dat=bytes([i%2+1]))
            summary.observe(11,20+i*.1,E("can",can=[frame]))
        for i in range(15):
            frame=SimpleNamespace(src=2,address=0x777,dat=bytes([i%3+40]))
            summary.observe(12,i*.1,E("can",can=[frame]))
        v=summary.result()
        row=next(x for x in v["canCandidateChanges"] if x["idHex"]=="0x777")
        self.assertEqual((row["baselineFrames"],row["parkingCandidateFrames"]),(12,15))
        output=json.dumps(v)
        self.assertNotIn("dataHex",output)
        self.assertNotIn("canFrameData",output)
        self.assertNotIn("GPS",output)
        self.assertNotIn("VIN",output)

    def test_no_fake_parking_claim_and_invalid_numbers(self):
        s=mod.Summary().result()
        self.assertEqual(s["status"],"unverified_oem_parking_annotation")
        self.assertIsNone(mod.valid_number(float("nan")))
        self.assertEqual(s["bins"],[])


if __name__=="__main__":
    unittest.main()
