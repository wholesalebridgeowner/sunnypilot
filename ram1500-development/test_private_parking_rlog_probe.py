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
            # Real cereal events return the frame list for can/sendcan (E("can", can=[...])), a struct otherwise.
            return getattr(self.payload,field,self.payload)
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
        self.assertGreater(observed["stoppedSteeringAngleRangeDeg"],50)
        self.assertEqual(observed["commaLateralActiveSamples"],{"False":30})
        self.assertEqual(observed["commaNativeCommandAbsMax"],0)

    def test_selected_can_id_changes_are_aggregated_without_raw_payload(self):
        summary=mod.Summary()
        for i in range(12):
            frame=SimpleNamespace(src=2,address=0x777,dat=bytes([i%2+1]))
            summary.observe(11,i*.1,E("can",can=[frame]))
        for i in range(15):
            frame=SimpleNamespace(src=2,address=0x777,dat=bytes([i%3+40]))
            summary.observe(11,25+i*.1,E("can",can=[frame]))
        v=summary.result()
        row=next(x for x in v["canCandidateChanges"] if x["idHex"]=="0x777")
        self.assertEqual((row["baselineFrames"],row["parkingCandidateFrames"]),(12,15))
        output=json.dumps(v)
        self.assertNotIn("dataHex",output)
        self.assertNotIn("canFrameData",output)
        self.assertNotIn("GPS",output)
        self.assertNotIn("VIN",output)

    def test_stale_initdata_clock_does_not_double_route_time(self):
        stale=E("initData")
        stale.logMonoTime=0
        frame=SimpleNamespace(src=0,address=0x31,dat=b"\\x00")
        first=E("can",can=[frame])
        first.logMonoTime=720_000_000_000
        state=E("carState",vEgo=0.0,steeringAngleDeg=45,
                steeringTorqueEps=60,steeringPressed=False,gearShifter="reverse")
        state.logMonoTime=721_000_000_000
        summary=mod.Summary()
        mod.observe_segment_events(summary,12,[stale,first,state])
        v=summary.result()
        self.assertEqual(v["bins"][0]["relativeWindowSeconds"],[720,725])
        self.assertEqual(v["bins"][0]["carStateSamples"],1)
        self.assertFalse(any(item["relativeWindowSeconds"][0]>=1400 for item in v["bins"]))

    def test_pinned_dbc_eps_auto_park_status_bit_and_transitions(self):
        def frame(flag, bus=0, address=0x31, byte_length=8):
            dat=bytearray(byte_length)
            if byte_length>6 and flag:
                dat[6]|=0x08  # DBC bit 51, single-bit Motorola
            return SimpleNamespace(src=bus,address=address,dat=bytes(dat))
        self.assertIs(mod.eps_auto_park_has_control(frame(True)),True)
        self.assertIs(mod.eps_auto_park_has_control(frame(False)),False)
        self.assertIsNone(mod.eps_auto_park_has_control(frame(True,bus=130)))
        self.assertIsNone(mod.eps_auto_park_has_control(frame(True,address=0xA6)))
        self.assertIsNone(mod.eps_auto_park_has_control(frame(True,byte_length=6)))
        summary=mod.Summary()
        summary.observe(11,1.0,E("can",can=[frame(False)]))
        summary.observe(11,25.0,E("can",can=[frame(True)]))
        summary.observe(11,25.01,E("can",can=[frame(True)]))
        summary.observe(11,40.0,E("can",can=[frame(False)]))
        self.assertEqual(summary.bins[132]["autoParkControl"]["false"],1)
        self.assertEqual(summary.bins[137]["autoParkControl"]["true"],2)
        self.assertEqual(summary.bins[137]["autoParkTransitions"]["enabled"],1)
        self.assertEqual(summary.bins[140]["autoParkTransitions"]["disabled"],1)

    def test_no_fake_parking_claim_and_invalid_numbers(self):
        s=mod.Summary().result()
        self.assertEqual(s["status"],"unverified_oem_parking_annotation")
        self.assertIsNone(mod.valid_number(float("nan")))
        self.assertEqual(s["bins"],[])


if __name__=="__main__":
    unittest.main()
