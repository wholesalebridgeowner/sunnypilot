#!/usr/bin/env python3
"""Read-only, opt-in Ram telemetry sidecar for a *parked* Comma device.

Subscribes to existing cereal messaging streams. Never publishes messages,
issues CAN commands, changes ECU parameters, or touches openpilot control code.
No automatic startup or upload. User must start explicitly after validation.

Safety guards (added in Claude review, 2026-11-17):
  * Subscriber-slot preflight. msgq allows NUM_READERS=15 subscribers per service and never frees a slot
    when a process exits; the 16th subscriber EVICTS EVERY subscriber of that service (msgq.cc,
    msgq_init_subscriber), including card/controlsd. The sidecar reads the reader count from the first
    8 bytes of /dev/shm/msgq_<service> (read-only) and refuses to subscribe if that would evict anyone.
  * Parked-only by default: capture stops as soon as carState reports motion, unless --allow-moving.
  * Runs at reduced CPU priority (nice +10) and logs its own CPU/memory use every 10 s.

Install OUTSIDE /data/openpilot (e.g. /data/ram-diag/) so the openpilot git tree stays untouched, and run with
the device's own cereal:
  cd /data/openpilot && PYTHONPATH=/data/openpilot python3 /data/ram-diag/device_telemetry.py \
    --output-dir /data/ram-telemetry --minutes 2 --sample-hz 2 --max-mb 8 --keep-files 2

Local NDJSON contains sensitive vehicle and trip telemetry: keep it private.
"""
import argparse
import json
import os
import struct
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

# Ram DT identifiers as defined by the drive-build panda safety mode
# (opendbc_repo/opendbc/safety/modes/chrysler.h:193-203 at 1f7233cb; identical on this branch).
RAM_CAN_IDS = {
  0x31: "eps_motor_feedback",
  0x83: "brake_vehicle_state",
  0x79: "vehicle_speed",
  0x9D: "accelerator_input",
  0x99: "acc_cruise_state",
  0xA6: "lkas_command",
  0xB1: "cruise_buttons",
  0xFA: "lkas_hud",
  0x28A: "center_stack",
}
SERVICES = ("can", "sendcan", "carState", "carControl", "carOutput",
            "controlsState", "selfdriveState", "onroadEvents",
            "longitudinalPlan", "radarState", "drivingModelData")
# Q-log already handles complete trip history; sidecar focuses on key signals
# in a bounded diagnostic window without duplicating camera/video traffic.
FIELDS = {
  "carState": ("vEgo", "aEgo", "steeringAngleDeg", "steeringRateDeg",
               "steeringTorque", "steeringTorqueEps", "steeringPressed",
               "canValid", "canTimeout", "steerFaultTemporary",
               "steerFaultPermanent", "gasPressed", "brakePressed", "standstill", "gearShifter"),
  "carControl": ("enabled", "latActive", "longActive"),
  "selfdriveState": ("enabled", "active", "alertType"),
  "controlsState": ("desiredCurvature", "curvature", "longControlState"),
  "longitudinalPlan": ("aTarget", "hasLead", "shouldStop", "allowThrottle",
                       "allowBrake", "longitudinalPlanSource"),
  "drivingModelData": (),
}
ENUM_FIELDS = ("longControlState", "longitudinalPlanSource", "gearShifter")

NUM_READERS = 15            # msgq/msgq.h at the drive build
MOVING_SPEED_MS = 0.3       # parked guard threshold
HEALTH_INTERVAL_S = 10.0


def scalar(obj, name):
  try:
    value = getattr(obj, name)
  except Exception:
    return None
  if value is None or isinstance(value, (bool, int, float, str)):
    return value
  return str(value) if name in ENUM_FIELDS else None


def record_for_message(evt, service, raw_can=False):
  """Return low-overhead structured sample, or None for unneeded messages."""
  timestamp = int(evt.logMonoTime)
  payload = getattr(evt, service)
  row = {"monoTimeNs": timestamp, "service": service}
  if service in ("can", "sendcan"):
    packets = []
    for m in payload:
      address = int(m.address)
      if not raw_can and address not in RAM_CAN_IDS:
        continue
      # CAN frames only; do not decode bytes into guessed physical units.
      packets.append({"bus": int(m.src), "id": address,
                      "label": RAM_CAN_IDS.get(address, "other"),
                      "dataHex": bytes(m.dat).hex()})
    if not packets:
      return None
    row["packets"] = packets
  elif service == "onroadEvents":
    names = [str(x.name) for x in payload]
    if not names:
      return None
    row["events"] = names
  else:
    row.update({key: scalar(payload, key) for key in FIELDS.get(service, ())})
    if service == "carState":
      cs = getattr(payload, "cruiseState", None)
      for key in ("enabled", "available", "standstill", "speed"):
        row["cruise_" + key] = scalar(cs, key)
    elif service in ("carControl", "carOutput"):
      act = getattr(payload, "actuators" if service == "carControl" else "actuatorsOutput", None)
      for key in ("torque", "torqueOutputCan", "accel"):
        row["actuator_" + key] = scalar(act, key)
    elif service == "radarState":
      lead = getattr(payload, "leadOne", None)
      for key in ("status", "dRel", "vRel", "vLead", "radar"):
        row["lead_" + key] = scalar(lead, key)
    elif service == "drivingModelData":
      meta = getattr(payload, "meta", None)
      for key in ("laneChangeState", "laneChangeDirection"):
        val = getattr(meta, key, None)
        row[key] = str(val) if val is not None else None
  return row


class RotatingWriter:
  """Local-only bounded JSONL writer. Delete oldest files only in own directory."""

  def __init__(self, directory, max_bytes=16 * 1024 * 1024, keep=8):
    self.directory = Path(directory)
    if max_bytes < 4096 or keep < 1:
      raise ValueError("Invalid size/retention")
    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.directory.chmod(0o700)  # private location for trip/CAN telemetry
    self.max_bytes = max_bytes
    self.keep = keep
    self.index = 0
    self.f = None
    self.length = 0
    self.rotate()

  def rotate(self):
    if self.f:
      self.f.close()
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    self.index += 1
    path = self.directory / f"ram-telemetry-{name}-{self.index:04d}.jsonl"
    # owner-only file (0600); newline="\n" because the byte cap counts "\n" as one byte (Windows would write "\r\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    self.f = os.fdopen(fd, "w", encoding="utf8", newline="\n")
    self.length = 0
    files = sorted(self.directory.glob("ram-telemetry-*.jsonl"))
    for old in files[:-self.keep]:
      if old != path:
        old.unlink()

  def write(self, row):
    line = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
    size = len(line.encode())
    if size > self.max_bytes:
      raise ValueError("single event exceeds segment file cap")
    if self.length + size > self.max_bytes:
      self.rotate()
    self.f.write(line)
    self.length += size

  def close(self):
    if self.f:
      self.f.close()
      self.f = None


def msgq_path(service, shm_dir="/dev/shm", prefix=None):
  """Shared-memory file of a msgq service (msgq.cc msgq_new_queue: /dev/shm/msgq_[<prefix>/]<service>)."""
  base = f"msgq_{prefix}/" if prefix else "msgq_"
  return Path(shm_dir) / f"{base}{service}"


def msgq_reader_counts(services, shm_dir="/dev/shm", prefix=None):
  """Registered-reader count per service, read-only from the msgq header's first uint64 (little-endian).

  None = queue file does not exist yet (no publisher/subscriber has created it; subscribing is safe).
  """
  counts = {}
  for service in services:
    p = msgq_path(service, shm_dir, prefix)
    try:
      with p.open("rb") as f:
        head = f.read(8)
      counts[service] = struct.unpack("<Q", head)[0] if len(head) == 8 else None
    except FileNotFoundError:
      counts[service] = None
  return counts


def slot_problems(counts, num_readers=NUM_READERS):
  """Services where one more subscriber would exceed the reader table and evict all existing subscribers."""
  return {s: c for s, c in counts.items() if c is not None and c + 1 > num_readers}


def motion_stop_reason(row, allow_moving=False, threshold=MOVING_SPEED_MS):
  """Return a reason string if a carState row shows the truck moving and moving capture is not allowed."""
  if allow_moving or row.get("service") != "carState":
    return None
  v = row.get("vEgo")
  if v is not None and abs(float(v)) > threshold:
    return f"vehicle moving (vEgo={float(v):.2f} m/s) and --allow-moving not set"
  return None


def boot_ns():
  """Same clock as openpilot's logMonoTime (nanos since boot, CLOCK_BOOTTIME) where available."""
  clock = getattr(time, "CLOCK_BOOTTIME", None)
  return time.clock_gettime_ns(clock) if clock is not None else time.monotonic_ns()


def health_row(started_wall, started_cpu, counts):
  row = {"monoTimeNs": boot_ns(), "service": "sidecar_health",
         "wallElapsedS": round(time.monotonic() - started_wall, 2),
         "cpuPercentOneCore": None, "rssKiB": None, "loadAvg1m": None, "rows": dict(counts)}
  wall = time.monotonic() - started_wall
  if wall > 0:
    row["cpuPercentOneCore"] = round(100.0 * (time.process_time() - started_cpu) / wall, 1)
  try:
    for line in Path("/proc/self/status").read_text().splitlines():
      if line.startswith("VmRSS:"):
        row["rssKiB"] = int(line.split()[1])
  except OSError:
    pass
  try:
    row["loadAvg1m"] = round(os.getloadavg()[0], 2)
  except (AttributeError, OSError):
    pass
  return row


def run(args):
  # Deliberately lazy device-only dependency: pure fixtures run on normal Python.
  import cereal.messaging as messaging
  from cereal import log as cereal_log

  prefix = os.environ.get("OPENPILOT_PREFIX")
  counts_before = msgq_reader_counts(SERVICES, prefix=prefix)
  problems = slot_problems(counts_before)
  if problems:
    return {"aborted": "subscribing would evict all existing subscribers of: " + ", ".join(sorted(problems)) +
            ". Reboot the parked device (clears /dev/shm), let it go onroad, then retry.",
            "readerCounts": counts_before}
  try:
    os.nice(10)
  except (AttributeError, OSError):
    pass

  writer = RotatingWriter(args.output_dir, args.max_mb * 1024 * 1024, args.keep_files)
  poller = messaging.Poller()
  subs = {}
  for service in SERVICES:
    subs[messaging.sub_sock(service, poller)] = service
  counts = Counter()
  last_sample = {}
  interval_ns = int(1e9 / args.sample_hz)
  started_wall, started_cpu = time.monotonic(), time.process_time()
  next_health = started_wall + HEALTH_INTERVAL_S
  end = started_wall + args.minutes * 60
  stop_reason = "duration reached"
  writer.write({"monoTimeNs": boot_ns(), "service": "sidecar_start", "readerCountsBefore": counts_before,
                "sampleHz": args.sample_hz, "allowMoving": args.allow_moving, "allCanIds": args.all_can_ids})
  try:
    while time.monotonic() < end and stop_reason == "duration reached":
      for sock in poller.poll(100):
        service = subs[sock]
        while (raw := sock.receive(non_blocking=True)) is not None:   # drain so the reader never lags
          try:
            with cereal_log.Event.from_bytes(raw) as evt:
              if service not in ("can", "sendcan", "onroadEvents", "carState"):
                if evt.logMonoTime - last_sample.get(service, -interval_ns) < interval_ns:
                  continue
                last_sample[service] = evt.logMonoTime
              item = record_for_message(evt, service, args.all_can_ids)
          except Exception:
            counts["decode_errors"] += 1
            continue
          if item is None:
            continue
          reason = motion_stop_reason(item, args.allow_moving)
          if reason:
            stop_reason = reason
            break
          if service == "carState":
            if item["monoTimeNs"] - last_sample.get(service, -interval_ns) < interval_ns:
              continue
            last_sample[service] = item["monoTimeNs"]
          writer.write(item)
          counts[service] += 1
        if stop_reason != "duration reached":
          break
      if time.monotonic() >= next_health:
        writer.write(health_row(started_wall, started_cpu, counts))
        next_health += HEALTH_INTERVAL_S
  except KeyboardInterrupt:
    stop_reason = "interrupted by operator"
  finally:
    writer.write(health_row(started_wall, started_cpu, counts))
    writer.write({"monoTimeNs": boot_ns(), "service": "sidecar_stop", "reason": stop_reason,
                  "readerCountsAfter": msgq_reader_counts(SERVICES, prefix=prefix)})
    writer.close()
  return {"rows": dict(counts), "stopReason": stop_reason}


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--output-dir", type=Path, required=True)
  parser.add_argument("--minutes", type=int, default=20)
  parser.add_argument("--sample-hz", type=int, default=10)
  parser.add_argument("--max-mb", type=int, default=16)
  parser.add_argument("--keep-files", type=int, default=8)
  parser.add_argument("--all-can-ids", action="store_true",
                      help="more sensitive/high-volume; record all observed CAN packets")
  parser.add_argument("--allow-moving", action="store_true",
                      help="keep capturing when the vehicle moves (NOT for the parked pilot; separate approval)")
  parser.add_argument("--check-slots", action="store_true",
                      help="only print msgq reader counts per service and exit (read-only)")
  args = parser.parse_args()
  if args.check_slots:
    counts = msgq_reader_counts(SERVICES, prefix=os.environ.get("OPENPILOT_PREFIX"))
    print(json.dumps({"readerCounts": counts, "wouldEvict": slot_problems(counts), "numReaders": NUM_READERS},
                     sort_keys=True))
    return
  if not (1 <= args.minutes <= 120 and 1 <= args.sample_hz <= 20 and 1 <= args.max_mb <= 64
          and 1 <= args.keep_files <= 20):
    parser.error("Capture limits exceeded")
  print(json.dumps({"completed": run(args)}, sort_keys=True))


if __name__ == "__main__":
  main()
