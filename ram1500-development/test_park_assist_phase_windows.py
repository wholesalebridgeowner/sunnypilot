"""Synthetic tests: OEM parking comparison stays phase-isolated and payload-private."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("park_assist_phase_windows",Path(__file__).with_name("park_assist_phase_windows.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def packets():
    data=[]
    # Normal phase 0..4 s, first CAN event at t=5 s.
    for i in range(40):
        t=int((5 + i*.1)*1e9)
        data.append((t,"can",0,0x31,b"\x10" if i%2 else b"\x20"))
        data.append((t,"can",0,0xA6,b"\x00"))
    # Gap 4..8 s; OEM park-assist activation example 8..12 s.
    for i in range(40):
        t=int((13 + i*.1)*1e9)
        data.append((t,"can",0,0x31,b"\x11" if i%2 else b"\x33"))
        data.append((t,"can",2,0x789,b"\x01"))
    return data


class Tests(unittest.TestCase):
    def test_parser_rejects_bad_intervals(self):
        for x in ("5", "-1:2", "2:2", "3:2", "foo:bar", "1:86401", "1:2:3"):
            with self.assertRaisesRegex(ValueError, "Window"):
                m.parse_window(x)
        self.assertEqual(m.parse_window("10:15"),(10.,15.))

    def test_same_session_window_isolation(self):
        result=m.measure_windows(packets(),(0,4),(8,12))
        changes={r["idHex"]:r for r in result["changes"]}
        self.assertEqual(result["windowDefinitions"]["normalWindowFrames"],80)
        self.assertEqual(result["windowDefinitions"]["parkingWindowFrames"],80)
        self.assertEqual(changes["0x789"]["normalFrames"],0)
        self.assertEqual(changes["0x789"]["parkingFrames"],40)
        self.assertEqual(changes["0xA6"]["parkingFrames"],0)
        self.assertEqual(changes["0x31"]["normalFrames"],40)
        self.assertEqual(changes["0x31"]["parkingFrames"],40)
        output=json.dumps(result)
        self.assertNotIn("dataHex",output)
        self.assertNotIn("monoTimeNs",output)
        self.assertNotIn("location",output.lower())
        self.assertNotIn("0x11",output)  # no raw payload bytes

    def test_fails_closed_on_overlap_and_absent_interval(self):
        with self.assertRaisesRegex(ValueError,"overlap"):
            m.measure_windows(packets(),(0,9),(8,12))
        with self.assertRaisesRegex(ValueError,"No CAN frames"):
            m.measure_windows(packets(),(0,4),(20,21))
        with self.assertRaisesRegex(ValueError,"chronological"):
            m.measure_windows(list(reversed(packets())),(0,4),(8,12))

    def test_does_not_infer_parking_command_meaning(self):
        report=m.measure_windows(packets(),(0,4),(8,12))
        self.assertIn("cannot prove",str(report["caveats"]))
        self.assertTrue(all("payload" not in key.lower() for row in report["changes"] for key in row))


if __name__ == "__main__":
    unittest.main()
