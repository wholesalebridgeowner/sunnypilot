"""Time-aligned per-sample timelines from decoded records.

qlogs keep every 10th message of each 100 Hz service, and each service is decimated independently, so a
carState sample and the "matching" carControl sample can be up to ~100 ms apart. Every joined value is
therefore taken as-of (latest sample at or before the row time, within a tolerance) and the skew is
reported in a `<service>.dt_ms` column. Never compare two signals without looking at their skew.
"""
from __future__ import annotations

import bisect
import csv
import json
from pathlib import Path

NS = 1e9

# Units and meaning of the columns we export (drive-build semantics).
UNITS = {
  "carState.vEgo": "m/s",
  "carState.steeringAngleDeg": "deg (steering wheel)",
  "carState.steeringRateDeg": "deg/s",
  "carState.steeringTorque": "raw EPS_2.COLUMN_TORQUE units (driver torque; no Nm calibration in DBC)",
  "carState.steeringTorqueEps": "raw EPS_2.EPS_TORQUE_MOTOR units (same scale as the LKAS command; no Nm calibration)",
  "carControl.actuators.torque": "normalised command, 1.0 = STEER_MAX = 350 raw units",
  "carOutput.actuatorsOutput.torque": "normalised command after car-side limits (apply_torque / 350)",
  "carOutput.actuatorsOutput.torqueOutputCan": "raw LKAS_COMMAND.STEERING_TORQUE sent (units, |x| <= 350)",
  "controlsState.torqueState.output": "normalised torque (sign flipped vs carControl: left-positive TODO in source)",
  "controlsState.torqueState.desiredLateralAccel": "m/s^2",
  "controlsState.torqueState.actualLateralAccel": "m/s^2 (from steering angle via vehicle model, not an IMU)",
  "controlsState.torqueState.p": "m/s^2 in stock torque mode; normalised torque in NNLC mode",
  "controlsState.torqueState.i": "m/s^2 in stock torque mode; normalised torque in NNLC mode",
  "controlsState.torqueState.f": "m/s^2 in stock torque mode; normalised torque in NNLC mode",
  "controlsState.desiredCurvature": "1/m (after clip_curvature)",
  "controlsState.curvature": "1/m (from steering angle via vehicle model)",
  "liveParameters.roll": "rad",
  "liveTorqueParameters.latAccelFactorFiltered": "m/s^2 per normalised torque (published; applied only if useParams)",
}


class Index:
  """Per-service time index over sorted records."""

  def __init__(self, records: list[dict]):
    self.by_service: dict[str, list[dict]] = {}
    for r in records:
      self.by_service.setdefault(r["service"], []).append(r)
    self.times = {s: [r["t"] for r in rs] for s, rs in self.by_service.items()}

  def services(self) -> list[str]:
    return sorted(self.by_service)

  def asof(self, service: str, t: int, tolerance_s: float = 0.25) -> tuple[dict | None, float | None]:
    ts = self.times.get(service)
    if not ts:
      return None, None
    i = bisect.bisect_right(ts, t) - 1
    if i < 0:
      return None, None
    dt = (t - ts[i]) / NS
    if dt > tolerance_s:
      return None, None
    return self.by_service[service][i], dt

  def between(self, service: str, t0: int, t1: int) -> list[dict]:
    ts = self.times.get(service, [])
    a, b = bisect.bisect_left(ts, t0), bisect.bisect_right(ts, t1)
    return self.by_service.get(service, [])[a:b]

  def first_t(self) -> int | None:
    return min((ts[0] for ts in self.times.values() if ts), default=None)

  def last_t(self) -> int | None:
    return max((ts[-1] for ts in self.times.values() if ts), default=None)


JOIN_TOLERANCE_S = {
  "carState": 0.15, "carControl": 0.15, "carOutput": 0.15, "controlsState": 0.15, "selfdriveState": 0.15,
  "pandaStates": 0.25, "liveParameters": 0.5, "liveTorqueParameters": 0.5, "liveDelay": 0.5, "radarState": 0.5,
}


def joined_row(index: Index, t: int, services=tuple(JOIN_TOLERANCE_S)) -> dict:
  row = {"t_ns": t}
  for svc in services:
    rec, dt = index.asof(svc, t, JOIN_TOLERANCE_S.get(svc, 0.25))
    row[f"{svc}.dt_ms"] = None if dt is None else round(dt * 1000, 1)
    if rec is None:
      continue
    for k, v in rec.items():
      if k in ("t", "service", "segment"):
        continue
      if isinstance(v, (list, dict)):
        continue
      row[f"{svc}.{k}"] = v
  return row


def window_rows(index: Index, center_ns: int, before_s: float, after_s: float, base: str = "controlsState",
                t0_ns: int | None = None) -> list[dict]:
  """One row per `base` sample in [center-before, center+after], other services joined as-of."""
  rows = []
  for rec in index.between(base, int(center_ns - before_s * NS), int(center_ns + after_s * NS)):
    row = joined_row(index, rec["t"])
    row["rel_event_s"] = round((rec["t"] - center_ns) / NS, 3)
    if t0_ns is not None:
      row["route_s"] = round((rec["t"] - t0_ns) / NS, 3)
    row["segment"] = rec.get("segment")
    rows.append(row)
  return rows


def write_csv(rows: list[dict], path: Path) -> None:
  path = Path(path)
  cols: list[str] = []
  for r in rows:
    for k in r:
      if k not in cols:
        cols.append(k)
  lead = [c for c in ("route_s", "rel_event_s", "segment", "t_ns") if c in cols]
  cols = lead + [c for c in cols if c not in lead]
  with path.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in rows:
      w.writerow(r)
  path.with_suffix(".units.json").write_text(json.dumps({c: UNITS[c] for c in cols if c in UNITS}, indent=2) + "\n")
