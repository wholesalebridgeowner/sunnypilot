#!/usr/bin/env python3
"""Passive EPS status timeline for a supervised factory-parking capture (offline, read-only).

Purpose: find *where* in a private R-log the factory park assist held the steering, from the truck's own
status signals, instead of relying only on the driver's recollection of the time.

What it decodes (only documented status fields of the public opendbc Chrysler/Ram DT DBC, as pinned in the
drive build sunnypilot@1f7233cb, opendbc_repo/opendbc/dbc/chrysler_ram_dt_generated.dbc):
  EPS_2 (0x31, bus 0): LKAS_STATE, AUTO_PARK_HAS_CONTROL_2, TORQUE_OVERLAY_STATUS, LKAS_TEMPORARY_FAULT,
                       plus COLUMN_TORQUE / EPS_TORQUE_MOTOR for a decoder self-check against carState.
  EPS_3 (0xA4, bus 0): Activation_Status, Driver_Override, Hands_on_Wheel, DASM_FAULT.
The DBC marks these fields XXX (reverse-engineered, partly on other Stellantis models). Their meaning on this
truck is a hypothesis until (1) the self-check passes and (2) the states line up with the driver-reported
maneuver. The drive build itself reads only COLUMN_TORQUE, EPS_TORQUE_MOTOR (EPS_2) and DASM_FAULT (EPS_3) on Ram.

Independent of the DBC status bits it also lists "hands-off wheel motion" intervals: the steering wheel turning
at low speed while the driver's column torque stays below the steeringPressed threshold and Comma lateral
control is inactive. That is motion evidence, not proof of which ECU commanded it.

Never transmits, never touches the vehicle, never outputs payload bytes, absolute times, GPS, VIN or IDs.
Times are seconds from the first valid CAN-bearing message of the supplied files (same anchor as
park_assist_phase_windows.py / park_assist_motion_context.py).
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import io
import json
import math
from pathlib import Path

NS = 1_000_000_000
EPS_2, EPS_3 = 0x31, 0xA4
PT_BUS = 0
STEER_THRESHOLD = 120  # opendbc chrysler values.py at 1f7233cb; carState.steeringPressed uses |COLUMN_TORQUE| > this

# (name, start bit, size, little_endian, offset) copied from the pinned generated DBC.
EPS_2_SIGNALS = (
  ("LKAS_STATE", 23, 4, False, 0),
  ("COLUMN_TORQUE", 2, 11, False, -1024),
  ("TORQUE_OVERLAY_STATUS", 6, 4, False, 0),
  ("EPS_TORQUE_MOTOR", 34, 11, False, -1024),
  ("LKAS_TEMPORARY_FAULT", 38, 1, False, 0),
  ("AUTO_PARK_HAS_CONTROL_2", 51, 1, False, 0),
)
EPS_3_SIGNALS = (
  ("DASM_FAULT", 34, 1, False, 0),
  ("Activation_Status", 48, 3, True, 0),
  ("Driver_Override", 35, 1, False, 0),
  ("Hands_on_Wheel", 51, 1, False, 0),
)
STATUS_EPS_2 = ("LKAS_STATE", "AUTO_PARK_HAS_CONTROL_2", "TORQUE_OVERLAY_STATUS", "LKAS_TEMPORARY_FAULT")
STATUS_EPS_3 = ("Activation_Status", "Driver_Override", "Hands_on_Wheel", "DASM_FAULT")
TIMELINE_KEYS = ("LKAS_STATE", "AUTO_PARK_HAS_CONTROL_2", "Activation_Status")
REORDER_TOLERANCE_S = 2.0


def extract(data: bytes, start: int, size: int, little_endian: bool) -> int:
  """DBC bit extraction. Big-endian (@0) start bit is the MSB in DBC sawtooth numbering."""
  value = 0
  if little_endian:
    for i in range(size):
      bit = start + i
      value |= ((data[bit // 8] >> (bit % 8)) & 1) << i
    return value
  bit = start
  for _ in range(size):
    byte, pos = divmod(bit, 8)
    value = (value << 1) | ((data[byte] >> pos) & 1)
    bit = bit + 15 if pos == 0 else bit - 1
  return value


def decode(data: bytes, signals) -> dict[str, int] | None:
  if len(data) < 8:
    return None
  return {name: extract(data, start, size, le) + offset for name, start, size, le, offset in signals}


def _finite(v):
  return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def analyze(records, *, min_rate_deg_s=10.0, max_speed=3.0, merge_gap_s=0.5, min_interval_s=0.5):
  """records: (monoNs, kind, payload) in file order.

  kind "can": payload = list of (bus, address, bytes); "carState": dict(vEgo, steeringAngleDeg, steeringRateDeg,
  steeringTorque, steeringTorqueEps, gearShifter); "carControl": dict(latActive).
  """
  rows = []
  anchor = None
  file_max = None
  max_reorder = 0.0
  other_bus_eps = Counter()
  for t, kind, payload in records:
    t = int(t)
    if file_max is not None and t < file_max:
      reorder = (file_max - t) / NS
      if reorder > REORDER_TOLERANCE_S:
        raise ValueError("R-log input is not chronological beyond tolerance; check file order")
      max_reorder = max(max_reorder, reorder)
    file_max = t if file_max is None else max(file_max, t)
    if kind == "can":
      if anchor is None and payload:
        anchor = t
      for bus, address, data in payload:
        if address in (EPS_2, EPS_3):
          if bus == PT_BUS:
            rows.append((t, "eps2" if address == EPS_2 else "eps3", decode(bytes(data), EPS_2_SIGNALS if address == EPS_2 else EPS_3_SIGNALS)))
          else:
            other_bus_eps[f"bus{bus}:0x{address:X}"] += 1
    elif kind in ("carState", "carControl"):
      rows.append((t, kind, payload))
  if anchor is None:
    raise ValueError("No CAN frames in input")
  rows.sort(key=lambda r: r[0])

  # Decoder self-check: each carState vs the latest EPS_2 frame at or before it.
  last_eps2 = None
  compared = col_ok = eps_ok = 0
  for t, kind, p in rows:
    if kind == "eps2" and p is not None:
      last_eps2 = (t, p)
    elif kind == "carState" and last_eps2 is not None and t - last_eps2[0] <= 50_000_000:
      st, se = p.get("steeringTorque"), p.get("steeringTorqueEps")
      if _finite(st) and _finite(se):
        compared += 1
        col_ok += abs(st - last_eps2[1]["COLUMN_TORQUE"]) < 0.5
        eps_ok += abs(se - last_eps2[1]["EPS_TORQUE_MOTOR"]) < 0.5

  # Status value durations and a merged timeline of the key status tuple.
  counts = {k: Counter() for k in (*STATUS_EPS_2, *STATUS_EPS_3)}
  eps2_frames = eps3_frames = 0
  current = {}
  timeline = []
  lat_active = None
  ctx = None

  def new_ctx():
    return {"v": [], "angle": [], "drv": 0.0, "lat": Counter(), "gears": set()}

  def close(end_t):
    if timeline and timeline[-1]["endS"] is None:
      seg = timeline[-1]
      seg["endS"] = round((end_t - anchor) / NS, 2)
      c = seg.pop("_ctx")
      seg["vEgoMinMax"] = [round(min(c["v"]), 2), round(max(c["v"]), 2)] if c["v"] else None
      seg["steeringAngleRangeDeg"] = round(max(c["angle"]) - min(c["angle"]), 1) if c["angle"] else None
      seg["maxAbsDriverColumnTorque"] = round(c["drv"], 1)
      seg["commaLatActive"] = dict(c["lat"])
      seg["gears"] = sorted(c["gears"])

  for t, kind, p in rows:
    if kind == "eps2" and p is not None:
      eps2_frames += 1
      for k in STATUS_EPS_2:
        counts[k][p[k]] += 1
      current.update({k: p[k] for k in STATUS_EPS_2})
    elif kind == "eps3" and p is not None:
      eps3_frames += 1
      for k in STATUS_EPS_3:
        counts[k][p[k]] += 1
      current.update({k: p[k] for k in STATUS_EPS_3})
    elif kind == "carControl":
      lat_active = bool(p.get("latActive"))
    elif kind == "carState" and timeline and timeline[-1]["endS"] is None:
      c = timeline[-1]["_ctx"]
      if _finite(p.get("vEgo")):
        c["v"].append(p["vEgo"])
      if _finite(p.get("steeringAngleDeg")):
        c["angle"].append(p["steeringAngleDeg"])
      if _finite(p.get("steeringTorque")):
        c["drv"] = max(c["drv"], abs(p["steeringTorque"]))
      c["lat"][str(lat_active)] += 1
      if p.get("gearShifter") is not None:
        c["gears"].add(str(p["gearShifter"]))
    if kind in ("eps2", "eps3") and "LKAS_STATE" in current:
      # EPS_2 opens the timeline; EPS_3 fields join when present (None if the truck never sends 0xA4).
      key = {k: current.get(k) for k in TIMELINE_KEYS}
      start = round((t - anchor) / NS, 2)
      if timeline and timeline[-1]["endS"] is None and timeline[-1]["startS"] == start:
        timeline[-1].update(key)  # same instant (EPS_2 and EPS_3 in one CAN packet): no zero-length segment
      elif not timeline or any(timeline[-1][k] != v for k, v in key.items()):
        close(t)
        timeline.append({"startS": start, "endS": None, **key, "_ctx": new_ctx()})
  if rows:
    close(rows[-1][0])

  # Hands-off wheel motion at low speed with Comma lateral inactive (DBC-independent).
  motion = []
  lat_active = None
  for t, kind, p in rows:
    if kind == "carControl":
      lat_active = bool(p.get("latActive"))
    elif kind == "carState":
      v, rate, drv = p.get("vEgo"), p.get("steeringRateDeg"), p.get("steeringTorque")
      if not (_finite(v) and _finite(rate) and _finite(drv)):
        continue
      if v <= max_speed and abs(rate) >= min_rate_deg_s and abs(drv) <= STEER_THRESHOLD and lat_active is False:
        s = (t - anchor) / NS
        if motion and s - motion[-1][1] <= merge_gap_s:
          motion[-1][1] = s
          motion[-1][2] += 1
          motion[-1][3] = max(motion[-1][3], abs(rate))
        else:
          motion.append([s, s, 1, abs(rate)])
  motion = [{"startS": round(a, 2), "endS": round(b, 2), "samples": n, "maxAbsSteeringRateDegS": round(r, 1)}
            for a, b, n, r in motion if b - a >= min_interval_s]

  return {
    "timeReference": "seconds from first valid CAN-bearing message of the supplied files; NOT wall clock",
    "maxReorderSeconds": round(max_reorder, 3),
    "decoderSelfCheck": {
      "eps2Frames": eps2_frames, "eps3Frames": eps3_frames, "carStateCompared": compared,
      "columnTorqueMatchFraction": round(col_ok / compared, 4) if compared else None,
      "epsMotorTorqueMatchFraction": round(eps_ok / compared, 4) if compared else None,
      "pass": bool(compared) and col_ok / compared >= 0.98 and eps_ok / compared >= 0.98,
    },
    "statusValueFrameCounts": {k: {str(val): n for val, n in sorted(c.items())} for k, c in counts.items()},
    "statusTimeline": timeline,
    "handsOffWheelMotionIntervals": motion,
    "epsFramesOnOtherBuses": dict(other_bus_eps),
    "caveats": [
      "LKAS_STATE, AUTO_PARK_HAS_CONTROL_2, TORQUE_OVERLAY_STATUS and EPS_3 Activation_Status are reverse-engineered "
      "(DBC 'XXX'); their meaning on this truck is a hypothesis to test against the driver-reported maneuver.",
      "The decoder self-check only proves bit extraction for COLUMN_TORQUE/EPS_TORQUE_MOTOR, which share EPS_2 with "
      "the status fields; it does not prove what a status value means.",
      "Hands-off wheel motion shows the wheel turning without driver column torque above the steeringPressed threshold; "
      "it does not identify which ECU commanded it.",
      "Observed status values are not an authorization map and must not be replayed or spoofed.",
    ],
  }


def iter_records(paths, schema_path):
  """Yield (monoNs, kind, payload) in file order from pinned-schema R-logs; tolerate a truncated tail."""
  import capnp
  import zstandard
  schema_path = Path(schema_path)
  schema = capnp.load(str(schema_path), imports=[str(schema_path.parent)])
  for path in paths:
    raw = Path(path).read_bytes()
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as stream:
      data = stream.read()
    try:
      for e in schema.Event.read_multiple_bytes(data):
        if not e.valid:
          continue
        w = e.which()
        if w == "can":
          yield int(e.logMonoTime), "can", [(int(f.src), int(f.address), bytes(f.dat)) for f in e.can]
        elif w == "carState":
          cs = e.carState
          yield int(e.logMonoTime), "carState", {
            "vEgo": cs.vEgo, "steeringAngleDeg": cs.steeringAngleDeg, "steeringRateDeg": cs.steeringRateDeg,
            "steeringTorque": cs.steeringTorque, "steeringTorqueEps": cs.steeringTorqueEps,
            "gearShifter": str(cs.gearShifter)}
        elif w == "carControl":
          yield int(e.logMonoTime), "carControl", {"latActive": e.carControl.latActive}
    except capnp.KjException:
      continue  # truncated tail of an unfinished segment; earlier events were yielded


def main():
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--schema", type=Path, required=True, help="pinned drive-build cereal/log.capnp")
  ap.add_argument("--logs", type=Path, nargs="+", required=True, help="chronologically ordered private rlogs")
  ap.add_argument("--out", type=Path, required=True)
  ap.add_argument("--min-rate-deg-s", type=float, default=10.0)
  ap.add_argument("--max-speed", type=float, default=3.0)
  args = ap.parse_args()
  if args.out.resolve().is_relative_to(Path(__file__).resolve().parent.parent):
    ap.error("private-derived output must not be written inside the git repository")
  if not args.schema.is_file() or not all(p.is_file() for p in args.logs):
    ap.error("missing pinned schema or R-log")
  try:
    report = analyze(iter_records(args.logs, args.schema), min_rate_deg_s=args.min_rate_deg_s, max_speed=args.max_speed)
  except ValueError as exc:
    ap.error(str(exc))
  args.out.parent.mkdir(parents=True, exist_ok=True)
  args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
  print(json.dumps({"selfCheck": report["decoderSelfCheck"]["pass"], "timelineSegments": len(report["statusTimeline"]),
                    "handsOffMotionIntervals": len(report["handsOffWheelMotionIntervals"]),
                    "maxReorderSeconds": report["maxReorderSeconds"]}))


if __name__ == "__main__":
  main()
