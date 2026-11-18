#!/usr/bin/env python3
"""Off-device benchmark for device_telemetry.py using real Cap'n Proto messages (pinned drive-build schema).

Builds a synthetic but realistically shaped stream (Ram DT CAN rates measured on the truck 2026-11-17, plus background
frames), pushes it through the sidecar's own processing path (Capture.handle) and reports CPU per simulated second and
Python heap growth (tracemalloc). Nothing touches a vehicle or msgq; output goes to a temporary directory.

  python ram1500-development/bench_device_telemetry.py --schema-dir ~/.cache/ram1500/schema-1f7233cb --seconds 30

Requires pycapnp. The ratio between this PC and the Comma's CPU is not known, so compare runs relative to each other
(the device measured 21.6 % of one core for the first version).
"""
import argparse
import importlib.util
import tempfile
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ram_device_diag", HERE / "device_telemetry.py")
dt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dt)

# Ram DT frames seen per 10 ms CAN message on the truck (bus, id, period in 10 ms ticks); 128+ = panda TX echo/forward.
TRACKED = [(0, 0x31, 1), (130, 0x31, 1), (0, 0x79, 2), (130, 0x79, 2), (0, 0x83, 2), (130, 0x83, 2), (0, 0x9D, 2),
           (130, 0x9D, 2), (0, 0xB1, 2), (130, 0xB1, 2), (2, 0x99, 2), (128, 0x99, 2), (2, 0xA6, 2), (128, 0xA6, 2),
           (2, 0xFA, 6), (128, 0xFA, 25), (0, 0x28A, 100), (130, 0x28A, 100)]


def load_schema(schema_dir):
  import capnp
  d = Path(schema_dir).expanduser()
  return capnp.load(str(d / "log.capnp"), imports=[str(d), str(d / "include")])


def build_stream(log, seconds, background_frames):
  """List of (service, raw bytes) in publish order for `seconds` of traffic at 100 Hz control rate."""
  out = []
  t = 1_000_000_000
  for tick in range(int(seconds * 100)):
    t += 10_000_000
    frames = [(b, a) for b, a, period in TRACKED if tick % period == 0]
    frames += [(tick % 3, 0x100 + (k * 7 + tick) % 0x500) for k in range(background_frames)]
    e = log.Event.new_message(logMonoTime=t, valid=True)
    lst = e.init("can", len(frames))
    for i, (b, a) in enumerate(frames):
      lst[i].address, lst[i].src, lst[i].dat = a, b, bytes([tick & 0xFF] * 8)
    out.append(("can", e.to_bytes()))
    if tick % 2 == 0:
      s = log.Event.new_message(logMonoTime=t + 1, valid=True)
      sl = s.init("sendcan", 2 if tick % 25 == 0 else 1)
      sl[0].address, sl[0].src, sl[0].dat = 0xA6, 0, b"\x00" * 8
      if tick % 25 == 0:
        sl[1].address, sl[1].src, sl[1].dat = 0xFA, 0, b"\x00" * 8
      out.append(("sendcan", s.to_bytes()))
    for svc in ("carState", "carControl", "carOutput", "controlsState", "selfdriveState"):
      m = log.Event.new_message(logMonoTime=t + 2, valid=True)
      body = m.init(svc)
      if svc == "carState":
        body.vEgo, body.gearShifter, body.steeringTorqueEps = 0.0, "park", 0.0
      out.append((svc, m.to_bytes()))
    if tick % 5 == 0:
      for svc in ("drivingModelData", "radarState", "longitudinalPlan"):
        m = log.Event.new_message(logMonoTime=t + 3, valid=True)
        m.init(svc)
        out.append((svc, m.to_bytes()))
    if tick % 100 == 0:
      m = log.Event.new_message(logMonoTime=t + 4, valid=True)
      ev = m.init("onroadEvents", 2)
      ev[0].name, ev[1].name = "wrongGear", "seatbeltNotLatched"
      out.append(("onroadEvents", m.to_bytes()))
  return out


def _pass(log, stream, sample_hz, trace):
  with tempfile.TemporaryDirectory() as folder:
    writer = dt.RotatingWriter(folder, 8 * 1024 * 1024, 2)
    cap = dt.Capture(writer, log, sample_hz=sample_hz, all_can_ids=False, allow_moving=False)
    if trace:
      tracemalloc.start()
      base = tracemalloc.get_traced_memory()[0]
    c0 = time.process_time()
    for service, raw in stream:
      cap.handle(service, raw)
    cpu = time.process_time() - c0
    heap = None
    if trace:
      heap = tracemalloc.get_traced_memory()[0] - base
      tracemalloc.stop()
    writer.close()
    size = sum(p.stat().st_size for p in Path(folder).glob("*.jsonl"))
  return cpu, heap, size, cap


def run_bench(log, stream, seconds, sample_hz=2, repeats=3):
  """CPU from untraced passes (best of `repeats`); heap growth from a separate tracemalloc pass."""
  cpus = []
  for _ in range(repeats):
    cpu, _, size, cap = _pass(log, stream, sample_hz, trace=False)
    cpus.append(cpu)
  _, heap, _, _ = _pass(log, stream, sample_hz, trace=True)
  cpu = min(cpus)
  return {"simSeconds": seconds, "messages": len(stream), "cpuS": round(cpu, 3),
          "cpuPctOneCoreThisPC": round(100 * cpu / seconds, 2), "pyHeapGrowthKiB": round(heap / 1024, 1),
          "bytesWritten": size, "rows": dict(cap.counts), "stop": cap.stop_reason}


def main():
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--schema-dir", required=True)
  ap.add_argument("--seconds", type=float, default=30)
  ap.add_argument("--background-frames", type=int, default=40, help="untracked frames per 10 ms CAN message")
  a = ap.parse_args()
  log = load_schema(a.schema_dir)
  stream = build_stream(log, a.seconds, a.background_frames)
  print(run_bench(log, stream, a.seconds))


if __name__ == "__main__":
  main()
