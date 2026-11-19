"""Recorded-build ACC source reconciliation regression fixtures (offline only)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as S
import unittest

from ramtools.cruise_state_audit import decode_das3,summarize_segment,aggregate_segments

path=Path(__file__).resolve().parents[1]/"private_full_route_cruise_audit.py"
spec=importlib.util.spec_from_file_location("full_cruise",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

N=1_000_000_000

def can(active=False,available=False):
    b=bytearray(8)
    b[2]=(int(active)<<5)|(int(available)<<4)
    return bytes(b)


class Tests(unittest.TestCase):
    def test_das3_original_bit_positions(self):
        self.assertEqual(decode_das3(can(True,True)),{"active":True,"available":True})
        self.assertEqual(decode_das3(can(False,True)),{"active":False,"available":True})
        self.assertEqual(decode_das3(can(True,False)),{"active":True,"available":False})
        self.assertIsNone(decode_das3(can()[:7]))

    def test_raw_camera_acc_enabled_correlates_exact_carstate(self):
        car=[(i*10_000_000,True,True,i>=3,True,20.0) for i in range(10)]
        raw=[(i*10_000_000,can(i>=3,True)) for i in range(10)]
        controls=[(i*10_000_000, i>=5) for i in range(10)]
        selfd=[(i*10_000_000, i>=5) for i in range(10)]
        r=summarize_segment(car,controls,selfd,raw)
        self.assertEqual(r["carStateRows"],10)
        self.assertEqual(r["cruiseEnabledTrue"],7)
        self.assertEqual(r["cruiseEnabledFalse"],3)
        self.assertEqual(r["rawACCActiveFrames"],7)
        self.assertEqual(r["rawVsCarStateCompared"],10)
        self.assertEqual(r.get("rawVsCarStateDisagree",0),0)
        self.assertEqual(r["latActiveTrue"],5)
        self.assertEqual(r["selfdriveEnabledTrue"],5)
        self.assertEqual(r.get("latActiveButCruiseReportedOff",0),0)

    def test_cruise_off_lateral_on_is_separately_reported(self):
        rows=[(i*10_000_000,True,True,False,True,17.0) for i in range(10)]
        c=[(i*10_000_000,True) for i in range(10)]
        r=summarize_segment(rows,c,[],[(0,can(False,True))])
        self.assertEqual(r["latActiveButCruiseReportedOff"],10)
        self.assertEqual(r["cruiseEnabledFalse"],10)
        self.assertEqual(r["rawACCStatusMatched"],10)

    def test_raw_missing_is_unknown_not_false(self):
        r=summarize_segment([(0,True,True,True,True,19.)],[],[],[])
        self.assertEqual(r["cruiseEnabledTrue"],1)
        self.assertEqual(r["rawACCStatusUnavailable"],1)
        self.assertEqual(r.get("rawVsCarStateDisagree",0),0)

    def test_future_camera_sample_not_used_as_present(self):
        car=[(N,True,True,False,True,20.)]
        raw=[(N+10_000_000,can(True,True))]
        r=summarize_segment(car,[],[],raw)
        self.assertEqual(r["rawACCStatusUnavailable"],1)
        self.assertEqual(r.get("rawVsCarStateCompared",0),0)

    def test_unexpected_cam_disagreement_is_not_silenced(self):
        cs=[(i*10_000_000,True,True,False,False,20.) for i in range(10)]
        raw=[(i*10_000_000,can(True,True)) for i in range(10)]
        result=summarize_segment(cs,[],[],raw)
        self.assertEqual(result["rawVsCarStateDisagree"],10)
        self.assertEqual(result["rawAvailabilityDisagree"],10)

    def test_complete_route_private_inventory_and_duplicate_rejected(self):
        def x(n,route="000003e6--0123456789"):
            return {"Key":f"logs/fake|{route}/{n}/rlog.zst","Size":5_000}
        self.assertEqual(len(mod.select_complete_route([x(0),x(1),x(2)],"000003e6")),3)
        with self.assertRaisesRegex(ValueError,"missing contiguous"):
            mod.select_complete_route([x(0),x(2)],"000003e6")
        with self.assertRaisesRegex(ValueError,"ambiguous"):
            mod.select_complete_route([x(0),x(0,"000003e6--abcdef0123")],"000003e6")
        with self.assertRaisesRegex(ValueError,"duplicate"):
            mod.select_complete_route([x(0),x(0)],"000003e6")
        with self.assertRaisesRegex(ValueError,"invalid private log size"):
            mod.select_complete_route([{"Key":x(0)["Key"],"Size":-5}],"000003e6")

    def test_grouped_output_has_no_route_or_raw_can(self):
        summary=aggregate_segments([{"cruiseEnabledTrue":10,"carStateRows":20} for _ in range(31)])
        self.assertEqual(summary["completeSegments"],31)
        self.assertEqual(len(summary["windows"]),3)
        self.assertEqual(summary["totals"]["cruiseEnabledTrue"],310)
        output=json.dumps(summary)
        self.assertNotIn("dataHex",output)
        self.assertNotIn("longitude",output)
        self.assertNotIn("deviceId",output)

if __name__=="__main__":
    unittest.main()
