"""Fixture-only tests for Ram read-only diagnostic sidecar; no Comma device needed."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

spec=importlib.util.spec_from_file_location("ram_device_diag",Path(__file__).with_name("device_telemetry.py"))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def event(name,payload):
  e=NS(logMonoTime=12345678)
  setattr(e,name,payload)
  return e

class RamDeviceDiagnosticsTests(unittest.TestCase):
  def test_raw_can_selective_and_bytes(self):
    msg=event("can",[NS(address=0x31,src=0,dat=b"\x00\xab"),
                     NS(address=0xA6,src=0,dat=b"\x01\x02"),
                     NS(address=0x555,src=1,dat=b"\xff")])
    r=mod.record_for_message(msg,"can")
    self.assertEqual([p["id"] for p in r["packets"]],[0x31,0xA6])
    self.assertEqual(r["packets"][0]["dataHex"],"00ab")
    self.assertEqual(len(mod.record_for_message(msg,"can",raw_can=True)["packets"]),3)
  def test_unmatched_can_not_written(self):
    self.assertIsNone(mod.record_for_message(event("can",[NS(address=0x555,src=0,dat=b"\x00")]),"can"))
  def test_car_state_and_cruise(self):
    p=NS(vEgo=14.2,cruiseState=NS(enabled=True,available=True,speed=20,standstill=False))
    r=mod.record_for_message(event("carState",p),"carState")
    self.assertEqual(r["vEgo"],14.2)
    self.assertTrue(r["cruise_enabled"])
    self.assertIsNone(r["steeringAngleDeg"])
  def test_lane_changes_and_can_output(self):
    lc=mod.record_for_message(event("drivingModelData",NS(meta=NS(
      laneChangeState="laneChangeStarting",laneChangeDirection="left"))),"drivingModelData")
    self.assertEqual(lc["laneChangeState"],"laneChangeStarting")
    r=mod.record_for_message(event("carOutput",NS(actuatorsOutput=NS(torque=.3,torqueOutputCan=105))),"carOutput")
    self.assertEqual(r["actuator_torqueOutputCan"],105)
  def test_rotation_and_retention(self):
    with tempfile.TemporaryDirectory() as folder:
      w=mod.RotatingWriter(folder,max_bytes=4096,keep=2)
      for i in range(250):
        w.write({"n":i,"padding":"x"*50})
      w.close()
      files=list(Path(folder).glob("ram-telemetry-*.jsonl"))
      self.assertEqual(len(files),2)
      self.assertTrue(all(f.stat().st_size<=4096 for f in files))
  def test_invalid_limits(self):
    with self.assertRaises(ValueError):
      mod.RotatingWriter(Path("ignored"),max_bytes=10,keep=2)

if __name__=="__main__":
  unittest.main()
