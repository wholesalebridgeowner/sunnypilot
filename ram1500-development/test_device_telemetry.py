"""Fixture-only tests for Ram read-only diagnostic sidecar; no Comma device needed."""
import importlib.util
import tempfile
import os
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
      if os.name == "posix":
        self.assertEqual(os.stat(folder).st_mode & 0o777,0o700)
        self.assertTrue(all((f.stat().st_mode & 0o777)==0o600 for f in files))
  def test_invalid_limits(self):
    with self.assertRaises(ValueError):
      mod.RotatingWriter(Path("ignored"),max_bytes=10,keep=2)

  # --- guards added in the Claude review (msgq slot eviction, parked-only, overhead) ---
  def _fake_shm(self, folder, counts, prefix=None):
    import struct
    for service, n in counts.items():
      p = mod.msgq_path(service, folder, prefix)
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_bytes(struct.pack("<Q", n) + b"\x00" * 64)  # header starts with uint64 num_readers

  def test_reader_counts_read_only_and_missing_queue(self):
    with tempfile.TemporaryDirectory() as folder:
      self._fake_shm(folder, {"carState": 9, "can": 3})
      before = {p: p.read_bytes() for p in Path(folder).iterdir()}
      counts = mod.msgq_reader_counts(("carState", "can", "drivingModelData"), shm_dir=folder)
      self.assertEqual(counts, {"carState": 9, "can": 3, "drivingModelData": None})
      self.assertEqual(before, {p: p.read_bytes() for p in Path(folder).iterdir()})  # nothing written

  def test_slot_preflight_blocks_eviction(self):
    self.assertEqual(mod.slot_problems({"carState": 14, "can": 15, "x": None}), {"can": 15})
    self.assertEqual(mod.slot_problems({"carState": 14}), {})  # 15th reader still fits

  def test_queue_file_layouts(self):
    # drive build (verified on the device): /dev/shm/<service>; upstream msgq master: /dev/shm/msgq_<service>
    self.assertEqual(mod.msgq_path("can", "/dev/shm").as_posix(), "/dev/shm/can")
    self.assertEqual(mod.msgq_path("can", "/dev/shm", "abc").as_posix(), "/dev/shm/abc/can")
    self.assertEqual(mod.msgq_path("can", "/dev/shm", layout="upstream").as_posix(), "/dev/shm/msgq_can")
    self.assertEqual(mod.msgq_path("can", "/dev/shm", "abc", layout="upstream").as_posix(), "/dev/shm/msgq_abc/can")

  def test_reader_counts_find_either_layout(self):
    import struct
    with tempfile.TemporaryDirectory() as folder:
      Path(folder, "carState").write_bytes(struct.pack("<Q", 14) + b"\x00" * 64)       # drive-build layout
      Path(folder, "msgq_onroadEvents").write_bytes(struct.pack("<Q", 3) + b"\x00" * 64)  # upstream layout
      counts, paths = mod.msgq_reader_counts(("carState", "onroadEvents", "can"), shm_dir=folder, with_paths=True)
      self.assertEqual(counts, {"carState": 14, "onroadEvents": 3, "can": None})
      self.assertTrue(paths["carState"].endswith("carState") and "msgq_" not in paths["carState"])
      self.assertEqual(mod.slot_problems(counts), {})  # 14 -> one free slot: exactly one sidecar fits
      Path(folder, "carState").write_bytes(struct.pack("<Q", 15) + b"\x00" * 64)       # after one sidecar run
      self.assertEqual(mod.slot_problems(mod.msgq_reader_counts(("carState",), shm_dir=folder)), {"carState": 15})

  def test_parked_guard(self):
    moving = {"service": "carState", "vEgo": 1.2}
    self.assertIn("moving", mod.motion_stop_reason(moving))
    self.assertIsNone(mod.motion_stop_reason(moving, allow_moving=True))
    self.assertIsNone(mod.motion_stop_reason({"service": "carState", "vEgo": 0.0}))
    self.assertIsNone(mod.motion_stop_reason({"service": "carState", "vEgo": None}))
    self.assertIsNone(mod.motion_stop_reason({"service": "can", "vEgo": 9.0}))

  def test_health_row_reports_own_overhead(self):
    import time
    row = mod.health_row(time.monotonic() - 1.0, time.process_time(), {"can": 5})
    self.assertEqual(row["service"], "sidecar_health")
    self.assertIsNotNone(row["cpuPercentOneCore"])
    self.assertEqual(row["rows"], {"can": 5})

  def test_gear_enum_recorded_as_text(self):
    r = mod.record_for_message(event("carState", NS(vEgo=0.0, gearShifter="park", cruiseState=None)), "carState")
    self.assertEqual(r["gearShifter"], "park")

  def test_run_loop_with_fake_bus_parked_then_moving(self):
    """Regression for G3 run 1 (KeyError): msgq's Poller.poll() returns NEW objects, not the subscribed sockets."""
    import contextlib
    import json
    import sys
    import types
    from unittest import mock

    events = {
      b"cs0": event("carState", NS(vEgo=0.0, gearShifter="park", cruiseState=None)),
      b"can0": event("can", [NS(address=0x31, src=0, dat=b"\x01\x02")]),
      b"cs1": event("carState", NS(vEgo=2.0, gearShifter="drive", cruiseState=None)),
    }

    class FakeSock:
      def __init__(self, queue):
        self.queue = list(queue)

      def receive(self, non_blocking=False):
        return self.queue.pop(0) if self.queue else None

    queues = {"carState": [b"cs0", b"cs1"], "can": [b"can0"]}

    class FakePoller:
      def poll(self, timeout_ms):
        return [object()]  # fresh object every time, exactly like msgq's ipc_pyx Poller

    fake_messaging = types.ModuleType("cereal.messaging")
    fake_messaging.Poller = FakePoller
    fake_messaging.sub_sock = lambda service, poller: FakeSock(queues.get(service, []))
    fake_log = types.ModuleType("cereal.log")
    fake_log.Event = NS(from_bytes=lambda raw: contextlib.nullcontext(events[raw]))
    fake_cereal = types.ModuleType("cereal")
    fake_cereal.messaging, fake_cereal.log = fake_messaging, fake_log

    with tempfile.TemporaryDirectory() as folder, \
         mock.patch.dict(sys.modules, {"cereal": fake_cereal, "cereal.messaging": fake_messaging, "cereal.log": fake_log}):
      args = NS(output_dir=Path(folder), max_mb=1, keep_files=2, sample_hz=20, minutes=0.05,
                all_can_ids=False, allow_moving=False)
      result = mod.run(args)
      rows = [json.loads(line) for f in sorted(Path(folder).glob("*.jsonl")) for line in f.read_text().splitlines()]
    self.assertIn("moving", result["stopReason"])
    self.assertEqual(result["rows"].get("carState"), 1)  # parked sample kept; moving sample stopped the capture
    services = [r["service"] for r in rows]
    self.assertEqual(services[0], "sidecar_start")
    self.assertEqual(services[-1], "sidecar_stop")
    self.assertIn("moving", rows[-1]["reason"])

if __name__=="__main__":
  unittest.main()
