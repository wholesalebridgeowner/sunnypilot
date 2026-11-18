"""Offline tests for the passive EPS status timeline (synthetic data only; no vehicle, no private logs)."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("park_assist_eps_state", HERE / "park_assist_eps_state.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NS = 1_000_000_000
# Reference frames produced by opendbc's CANPacker('chrysler_ram_dt_generated') (EPS_2 layout byte-identical
# to the drive build sunnypilot@1f7233cb; COUNTER/CHECKSUM bytes are whatever the packer produced).
EPS2_REF = (bytes.fromhex("2bdb200044d30878"), {"LKAS_STATE": 2, "AUTO_PARK_HAS_CONTROL_2": 1, "COLUMN_TORQUE": -37,
                                                 "EPS_TORQUE_MOTOR": 211, "TORQUE_OVERLAY_STATUS": 5,
                                                 "LKAS_TEMPORARY_FAULT": 1})
EPS3_REFS = (
  (bytes.fromhex("0000000004000d8d"), {"DASM_FAULT": 1, "Activation_Status": 5, "Driver_Override": 0, "Hands_on_Wheel": 1}),
  (bytes.fromhex("00000000080013ec"), {"DASM_FAULT": 0, "Activation_Status": 3, "Driver_Override": 1, "Hands_on_Wheel": 0}),
)


def eps2(lkas, park, col, motor, overlay=0):
  """Build an EPS_2 payload with the module's own layout (verified against the packer vectors above)."""
  data = bytearray(8)
  def put(start, size, value):
    bit = start
    for i in range(size - 1, -1, -1):
      byte, pos = divmod(bit, 8)
      if (value >> i) & 1:
        data[byte] |= 1 << pos
      bit = bit + 15 if pos == 0 else bit - 1
  put(23, 4, lkas)
  put(2, 11, col + 1024)
  put(6, 4, overlay)
  put(34, 11, motor + 1024)
  put(51, 1, park)
  return bytes(data)


def session(mismatch=False):
  """10 s ordinary low-speed driving (LKAS_STATE 8), then 10 s of a parking phase (state 2, auto-park bit set)."""
  out = []
  for i in range(200):
    t = int((100 + i * 0.1) * NS)
    parking = i >= 100
    col = 15 if parking else 140 if i % 20 < 10 else 30
    motor = 300 if parking else 20
    out.append((t, "can", [(0, 0x31, eps2(2 if parking else 8, int(parking), col, motor)),
                           (2, 0x31, eps2(0, 0, 0, 0))]))
    out.append((t + 3_000_000, "carControl", {"latActive": False}))
    out.append((t + 5_000_000, "carState", {
      "vEgo": 0.4 if parking else 2.0, "steeringAngleDeg": (i - 100) * 4.0 if parking else 1.0,
      "steeringRateDeg": 40.0 if parking else 0.0, "steeringTorque": col + (7 if mismatch else 0),
      "steeringTorqueEps": motor, "gearShifter": "reverse" if parking else "drive"}))
  return out


class DecoderTests(unittest.TestCase):
  def test_matches_opendbc_packer_reference_frames(self):
    data, expected = EPS2_REF
    self.assertEqual(m.decode(data, m.EPS_2_SIGNALS), expected)
    for data, expected in EPS3_REFS:
      self.assertEqual({k: v for k, v in m.decode(data, m.EPS_3_SIGNALS).items()}, expected)

  def test_matches_live_opendbc_packer_when_available(self):
    sys.path.insert(0, str(HERE.parent / "opendbc_repo"))
    try:
      from opendbc.can.packer import CANPacker
    except Exception:
      self.skipTest("opendbc not importable here")
    finally:
      sys.path.pop(0)
    packer = CANPacker("chrysler_ram_dt_generated")
    for values in ({"LKAS_STATE": 8, "AUTO_PARK_HAS_CONTROL_2": 0, "COLUMN_TORQUE": 512, "EPS_TORQUE_MOTOR": -700,
                    "TORQUE_OVERLAY_STATUS": 9, "LKAS_TEMPORARY_FAULT": 0},
                   {"LKAS_STATE": 15, "AUTO_PARK_HAS_CONTROL_2": 1, "COLUMN_TORQUE": -1024, "EPS_TORQUE_MOTOR": 1023,
                    "TORQUE_OVERLAY_STATUS": 0, "LKAS_TEMPORARY_FAULT": 1}):
      self.assertEqual(m.decode(bytes(packer.pack(0x31, values)), m.EPS_2_SIGNALS), values)
    self.assertEqual(m.decode(eps2(2, 1, -37, 211, 5), m.EPS_2_SIGNALS)["COLUMN_TORQUE"], -37)

  def test_short_frame_is_not_decoded(self):
    self.assertIsNone(m.decode(b"\x00" * 4, m.EPS_2_SIGNALS))


class TimelineTests(unittest.TestCase):
  def test_status_timeline_and_hands_off_motion_isolate_parking_phase(self):
    r = m.analyze(session())
    self.assertTrue(r["decoderSelfCheck"]["pass"])
    self.assertEqual(r["decoderSelfCheck"]["columnTorqueMatchFraction"], 1.0)
    states = [(s["LKAS_STATE"], s["AUTO_PARK_HAS_CONTROL_2"], s["Activation_Status"]) for s in r["statusTimeline"]]
    # No EPS_3 in this input: the timeline still runs on EPS_2 alone.
    self.assertEqual(states, [(8, 0, None), (2, 1, None)])
    self.assertAlmostEqual(r["statusTimeline"][1]["startS"], 10.0, delta=0.01)
    self.assertEqual(r["statusValueFrameCounts"]["AUTO_PARK_HAS_CONTROL_2"], {"0": 100, "1": 100})
    self.assertEqual(r["statusValueFrameCounts"]["LKAS_STATE"], {"2": 100, "8": 100})
    motion = r["handsOffWheelMotionIntervals"]
    self.assertEqual(len(motion), 1)
    self.assertAlmostEqual(motion[0]["startS"], 10.0, delta=0.11)
    self.assertEqual(r["epsFramesOnOtherBuses"], {"bus2:0x31": 200})

  def test_timeline_segments_with_eps3_present(self):
    rows = session()
    eps3_off, eps3_on = EPS3_REFS[1][0], EPS3_REFS[0][0]
    rows = [(t, k, p + [(0, 0xA4, eps3_on if t >= 110 * NS else eps3_off)]) if k == "can" else (t, k, p)
            for t, k, p in rows]
    r = m.analyze(rows)
    segs = r["statusTimeline"]
    self.assertEqual([(s["LKAS_STATE"], s["AUTO_PARK_HAS_CONTROL_2"], s["Activation_Status"]) for s in segs],
                     [(8, 0, 3), (2, 1, 5)])
    self.assertAlmostEqual(segs[1]["startS"], 10.0, delta=0.01)
    self.assertEqual(segs[1]["gears"], ["reverse"])
    self.assertEqual(segs[1]["commaLatActive"], {"False": 100})
    self.assertLess(segs[1]["maxAbsDriverColumnTorque"], m.STEER_THRESHOLD)
    self.assertGreater(segs[0]["maxAbsDriverColumnTorque"], m.STEER_THRESHOLD)

  def test_self_check_fails_on_decoder_mismatch(self):
    r = m.analyze(session(mismatch=True))
    self.assertFalse(r["decoderSelfCheck"]["pass"])
    self.assertEqual(r["decoderSelfCheck"]["columnTorqueMatchFraction"], 0.0)

  def test_bounded_reordering_is_tolerated_and_measured(self):
    rows = session()
    rows[10], rows[11] = rows[11], rows[10]  # carControl/carState written out of time order
    late = (rows[30][0] - int(1.5 * NS), "carControl", {"latActive": False})
    rows.insert(31, late)
    r = m.analyze(rows)
    self.assertGreaterEqual(r["maxReorderSeconds"], 1.5)
    self.assertTrue(r["decoderSelfCheck"]["pass"])

  def test_reordering_beyond_tolerance_fails_closed(self):
    with self.assertRaisesRegex(ValueError, "chronological"):
      m.analyze(list(reversed(session())))
    with self.assertRaisesRegex(ValueError, "No CAN"):
      m.analyze([(0, "carState", {})])

  def test_output_has_no_payloads_or_absolute_times(self):
    text = json.dumps(m.analyze(session()))
    self.assertNotIn("2bdb", text)
    self.assertNotIn(str(100 * NS), text)
    self.assertNotIn("dataHex", text)
    self.assertIn("hypothesis", text)


if __name__ == "__main__":
  unittest.main()
