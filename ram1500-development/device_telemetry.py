#!/usr/bin/env python3
"""Read-only, opt-in Ram telemetry sidecar for a *parked* Comma device.

Subscribes to existing cereal messaging streams. Never publishes messages,
issues CAN commands, changes ECU parameters, or touches openpilot control code.
No automatic startup or upload. User must start explicitly after validation.

Safety guards (added in Claude review, 2026-11-17):
  * Subscriber-slot preflight. msgq allows NUM_READERS=15 subscribers per service and never frees a slot
    when a process exits; the 16th subscriber EVICTS EVERY subscriber of that service (msgq.cc,
    msgq_init_subscriber), including card/controlsd. The sidecar reads the reader count from the first
    8 bytes of the queue file (read-only; /dev/shm/<service> on the drive build, /dev/shm/msgq_<service> upstream)
    and refuses to subscribe if that would evict anyone. Measured on the truck's Comma 2026-11-17 (onroad):
    carState had 14 of 15 readers, so the sidecar fits ONCE per device boot.
  * Parked-only by default: capture stops as soon as carState reports motion, unless --allow-moving.
  * Runs at reduced CPU priority (nice +10), logs its own CPU and memory (private vs shared) every 10 s, and stops
    itself above --max-cpu-pct (two consecutive windows) or --max-private-mb.

Install OUTSIDE /data/openpilot (e.g. /data/ram-diag/) so the openpilot git tree stays untouched, and run with
the device's own cereal and Python environment:
  cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py \
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
  except Exception:  # AttributeError, or pycapnp KjException for fields missing from the device schema
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
    # Hot path (~100 messages/s, dozens of frames each): read only the address of every frame; touch src/dat only
    # for matches. Labels are written once in the sidecar_start row (RAM_CAN_IDS), not per frame.
    # CAN frames only; do not decode bytes into guessed physical units.
    ids = None if raw_can else RAM_CAN_IDS
    packets = []
    for m in payload:
      address = m.address
      if ids is None or address in ids:
        packets.append({"bus": m.src, "id": address, "dataHex": bytes(m.dat).hex()})
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


def msgq_path(service, shm_dir="/dev/shm", prefix=None, layout="drive_build"):
  """Shared-memory file of a msgq service.

  layout "drive_build": /dev/shm/[<prefix>/]<service> -- what the Comma running sunnypilot 1f7233cb uses
                        (verified on the device 2026-11-17: /dev/shm/carState, /dev/shm/can, ...).
  layout "upstream":    /dev/shm/msgq_[<prefix>/]<service> -- current commaai/msgq master.
  """
  if layout == "drive_build":
    return Path(shm_dir) / (f"{prefix}/{service}" if prefix else service)
  base = f"msgq_{prefix}/" if prefix else "msgq_"
  return Path(shm_dir) / f"{base}{service}"


def msgq_reader_counts(services, shm_dir="/dev/shm", prefix=None, with_paths=False):
  """Registered-reader count per service, read-only from the msgq header's first uint64 (little-endian).

  Both known file layouts are checked. None = no queue file in either layout (nothing has created the queue yet,
  so subscribing cannot evict anyone). A wrong layout would read as None and look safe, hence both are checked.
  """
  counts, paths = {}, {}
  for service in services:
    counts[service], paths[service] = None, None
    for layout in ("drive_build", "upstream"):
      p = msgq_path(service, shm_dir, prefix, layout)
      try:
        with p.open("rb") as f:
          head = f.read(8)
      except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        continue
      counts[service] = struct.unpack("<Q", head)[0] if len(head) == 8 else None
      paths[service] = str(p)
      break
  return (counts, paths) if with_paths else counts


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


PROC_STATUS_KEYS = {"VmRSS": "rssKiB", "RssAnon": "rssAnonKiB", "RssShmem": "rssShmemKiB", "RssFile": "rssFileKiB"}


def proc_memory_kib(status_text=None):
  """Resident memory split from /proc/self/status: shared msgq ring pages count as RssShmem, private heap as RssAnon.

  G3 run 2 saw VmRSS climb ~12 MB/min. Reading through 10 MB shared rings maps their pages into this process (shared,
  already-resident memory); a real leak would show in RssAnon. This split tells the two apart on the device.
  """
  out = dict.fromkeys(PROC_STATUS_KEYS.values())
  if status_text is None:
    try:
      status_text = Path("/proc/self/status").read_text()
    except OSError:
      return out
  for line in status_text.splitlines():
    name, _, rest = line.partition(":")
    key = PROC_STATUS_KEYS.get(name.strip())
    if key and rest.split():
      out[key] = int(rest.split()[0])
  return out


def health_row(started_wall, started_cpu, counts, window=None):
  """Overhead record. `window` = (wall_s, cpu_s) of the last interval, for the windowed CPU figure."""
  wall = time.monotonic() - started_wall
  row = {"monoTimeNs": boot_ns(), "service": "sidecar_health", "wallElapsedS": round(wall, 2),
         "cpuPercentOneCore": round(100.0 * (time.process_time() - started_cpu) / wall, 1) if wall > 0 else None,
         "windowCpuPercentOneCore": None, "loadAvg1m": None, "rows": dict(counts)}
  if window and window[0] > 0:
    row["windowCpuPercentOneCore"] = round(100.0 * window[1] / window[0], 1)
  row.update(proc_memory_kib())
  try:
    row["loadAvg1m"] = round(os.getloadavg()[0], 2)
  except (AttributeError, OSError):
    pass
  return row


def next_health_deadline(deadline, now, interval=HEALTH_INTERVAL_S):
  """Next health-row time after `now`. After a stall longer than one interval, skip the missed slots so one row
  covers the whole gap; stepping by a fixed interval would emit a burst of ~drain-length windows whose CPU figures
  are noise and could trip (or clear) the two-window CPU limit. Found in review on PR #19."""
  deadline += interval
  return deadline if deadline > now else now + interval


def limit_breach(row, max_cpu_pct, max_private_mb, prev_cpu_breach):
  """Return (reason or None, cpu_breach_this_window). CPU must exceed its limit in two consecutive windows."""
  cpu = row.get("windowCpuPercentOneCore")
  cpu_breach = cpu is not None and cpu > max_cpu_pct
  if cpu_breach and prev_cpu_breach:
    return f"self-limit: CPU {cpu}% of one core > {max_cpu_pct}% for two windows", cpu_breach
  anon = row.get("rssAnonKiB")
  if anon is not None and anon > max_private_mb * 1024:
    return f"self-limit: private memory {anon // 1024} MB > {max_private_mb} MB", cpu_breach
  return None, cpu_breach


class Capture:
  """Per-message processing shared by the device loop, the tests and the off-device benchmark."""

  FULL_RATE = ("can", "sendcan", "onroadEvents")

  def __init__(self, writer, cereal_log, sample_hz, all_can_ids, allow_moving):
    self.writer = writer
    self.log = cereal_log
    self.interval_ns = int(1e9 / sample_hz)
    self.all_can_ids = all_can_ids
    self.allow_moving = allow_moving
    self.last_sample = {}
    self.counts = Counter()
    self.stop_reason = None

  def handle(self, service, raw):
    try:
      with self.log.Event.from_bytes(raw) as evt:
        if service == "carState":
          # motion guard on EVERY carState (100 Hz) but reading one field; the full record only at the sample rate
          reason = motion_stop_reason({"service": "carState", "vEgo": evt.carState.vEgo}, self.allow_moving)
          if reason:
            self.stop_reason = reason
            return
        if service not in self.FULL_RATE:
          if evt.logMonoTime - self.last_sample.get(service, -self.interval_ns) < self.interval_ns:
            return
          self.last_sample[service] = evt.logMonoTime
        item = record_for_message(evt, service, self.all_can_ids)
    except Exception:  # decode problems are counted, never fatal
      self.counts["decode_errors"] += 1
      return
    if item is None:
      return
    self.writer.write(item)
    self.counts[service] += 1


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
  # No Poller: drain every socket on a fixed cycle instead of waking per message (G3 run 2 measured 21.6 % of one core
  # with per-message wake-ups). msgq keeps a 10 MB ring per service, so a 50 ms cycle loses nothing. This also avoids
  # the Poller returning new socket objects (the G3 run 1 KeyError).
  socks = [(messaging.sub_sock(service), service) for service in SERVICES]
  cap = Capture(writer, cereal_log, args.sample_hz, args.all_can_ids, args.allow_moving)
  counts = cap.counts
  started_wall, started_cpu = time.monotonic(), time.process_time()
  win_wall, win_cpu = started_wall, started_cpu
  cpu_breach = False
  next_health = started_wall + HEALTH_INTERVAL_S
  end = started_wall + args.minutes * 60
  drain_s = args.drain_ms / 1000.0
  stop_reason = "duration reached"
  writer.write({"monoTimeNs": boot_ns(), "service": "sidecar_start", "readerCountsBefore": counts_before,
                "sampleHz": args.sample_hz, "allowMoving": args.allow_moving, "allCanIds": args.all_can_ids,
                "drainMs": args.drain_ms, "maxCpuPct": args.max_cpu_pct, "maxPrivateMb": args.max_private_mb,
                "canLabels": {f"0x{k:X}": v for k, v in RAM_CAN_IDS.items()}})
  try:
    while time.monotonic() < end and stop_reason == "duration reached":
      time.sleep(drain_s)
      for sock, service in socks:
        while (raw := sock.receive(non_blocking=True)) is not None:   # drain so the reader never lags
          cap.handle(service, raw)
          if cap.stop_reason:
            stop_reason = cap.stop_reason
            break
        if stop_reason != "duration reached":
          break
      now = time.monotonic()
      if now >= next_health:
        cpu_now = time.process_time()
        row = health_row(started_wall, started_cpu, counts, window=(now - win_wall, cpu_now - win_cpu))
        writer.write(row)
        win_wall, win_cpu = now, cpu_now
        next_health = next_health_deadline(next_health, now)
        reason, cpu_breach = limit_breach(row, args.max_cpu_pct, args.max_private_mb, cpu_breach)
        if reason and stop_reason == "duration reached":
          stop_reason = reason
  except KeyboardInterrupt:
    stop_reason = "interrupted by operator"
  except Exception as e:
    stop_reason = f"error: {type(e).__name__}: {e}"[:200]
    raise
  finally:
    writer.write(health_row(started_wall, started_cpu, counts))
    writer.write({"monoTimeNs": boot_ns(), "service": "sidecar_stop", "reason": stop_reason,
                  "readerCountsAfter": msgq_reader_counts(SERVICES, prefix=prefix)})
    writer.close()
  return {"rows": dict(counts), "stopReason": stop_reason}


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--output-dir", type=Path, help="capture directory (required unless --check-slots)")
  parser.add_argument("--minutes", type=int, default=20)
  parser.add_argument("--sample-hz", type=int, default=10)
  parser.add_argument("--max-mb", type=int, default=16)
  parser.add_argument("--keep-files", type=int, default=8)
  parser.add_argument("--all-can-ids", action="store_true",
                      help="more sensitive/high-volume; record all observed CAN packets")
  parser.add_argument("--allow-moving", action="store_true",
                      help="keep capturing when the vehicle moves (NOT for the parked pilot; separate approval)")
  parser.add_argument("--max-cpu-pct", type=float, default=15.0,
                      help="stop if own CPU exceeds this %% of one core in two consecutive 10 s windows")
  parser.add_argument("--max-private-mb", type=int, default=150, help="stop if private (RssAnon) memory exceeds this")
  parser.add_argument("--drain-ms", type=int, default=50, help="socket drain cycle (msgq rings hold 10 MB each)")
  parser.add_argument("--check-slots", action="store_true",
                      help="only print msgq reader counts per service and exit (read-only)")
  args = parser.parse_args()
  if args.check_slots:
    counts, paths = msgq_reader_counts(SERVICES, prefix=os.environ.get("OPENPILOT_PREFIX"), with_paths=True)
    print(json.dumps({"readerCounts": counts, "queueFiles": paths, "wouldEvict": slot_problems(counts),
                      "numReaders": NUM_READERS}, sort_keys=True))
    return
  if args.output_dir is None:
    parser.error("--output-dir is required for a capture")
  if not (1 <= args.minutes <= 120 and 1 <= args.sample_hz <= 20 and 1 <= args.max_mb <= 64
          and 1 <= args.keep_files <= 20 and 1 <= args.max_cpu_pct <= 50 and 32 <= args.max_private_mb <= 512
          and 10 <= args.drain_ms <= 200):
    parser.error("Capture limits exceeded")
  print(json.dumps({"completed": run(args)}, sort_keys=True))


if __name__ == "__main__":
  main()
