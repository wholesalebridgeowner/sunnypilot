"""Synthetic passive source-matched CAN activity tests; no device dependency."""
import importlib.util
from pathlib import Path
import unittest
import json

spec=importlib.util.spec_from_file_location("stratified",Path(__file__).with_name("park_assist_stratified_can.py"))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

N=1_000_000_000


class Tests(unittest.TestCase):
    def test_state_matching_and_no_payload_leak(self):
        vehicle=[]
        flags=[]
        frames=[]
        # Both strata are the same gear and same low-speed band.
        for active,offset in [(False,0),(True,10)]:
            for i in range(50):
                t=int((offset+i*.01)*N)
                vehicle.append((t,0.15,"reverse"))
                flags.append((t,active))
                # Data changes in active state but stays constant inactive.
                frame=bytes((i%2 if active else 0,2,3,4,5,6,7,8))
                frames.append((t,"can",0,0x81,frame))
                # Changing trailing checksum/counter must not create a candidate.
                frames.append((t,"can",0,0x82,bytes((0,0,0,0,0,0,i%256,i%256))))
                # Forwarding and defining EPS status are excluded.
                frames.append((t,"can",130,0x81,frame))
                frames.append((t,"can",0,0x31,frame))
        r=m.inspect_activity(frames,vehicle,flags,min_frames=30)
        a=next(x for x in r["candidates"] if x["idHex"]=="0x81")
        self.assertEqual(a["inactiveMatchedFrames"],50)
        self.assertEqual(a["activeMatchedFrames"],50)
        self.assertEqual(a["inactivePayloadChangeFraction"],0)
        self.assertGreater(a["activePayloadChangeFraction"],.95)
        b=next(x for x in r["candidates"] if x["idHex"]=="0x82")
        self.assertEqual(b["changeFractionDelta"],0)
        ids=[r["idHex"] for r in r["candidates"]]
        self.assertNotIn("0x31",ids)
        serialized=json.dumps(r)
        self.assertNotIn("dataHex",serialized)
        self.assertNotIn("VIN",serialized)
        self.assertNotIn("location",serialized.lower())

    def test_stale_and_future_only_dropped(self):
        rows=[(int(1.0*N),"can",0,0x81,b"12345678")]
        car=[(int(1.2*N),.15,"reverse")]
        flag=[(int(1.0*N),True)]
        r=m.inspect_activity(rows,car,flag,min_frames=2)
        self.assertEqual(r["candidateCount"],0)
        self.assertEqual(r["filteredCounts"].get("missingFreshStateOrEPS"),1)

    def test_unknown_gear_and_unbalanced_baseline_is_not_evidence(self):
        frames=[];vehicle=[];flags=[]
        for i in range(50):
            t=i*10_000_000
            frames.append((t,"can",0,0x81,b"abcxyz12"))
            vehicle.append((t,0.2,"drive"))
            flags.append((t,True))
        r=m.inspect_activity(frames,vehicle,flags)
        self.assertEqual(r["candidateCount"],0)
        self.assertGreater(r["filteredCounts"].get("insufficientMatchedFrames",0),0)

    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):
            m.inspect_activity([],[],[],min_frames=1)


if __name__=="__main__":
    unittest.main()
