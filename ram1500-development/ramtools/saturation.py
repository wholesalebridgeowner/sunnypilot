"""Steering-saturation forensics against the drive-build (1f7233cb) logic.

How `steerSaturated` is produced in that build:
  per 100 Hz frame, latcontrol.py:24-31 increments a timer when
      (|output torque| >= 1.0 - 1e-3  OR  curvature_limited)  AND vEgo > 10 m/s
      AND NOT steer_limited_by_safety AND NOT steeringPressed
  and decrements it otherwise; `saturated` is true once the timer reaches steerLimitTimer (0.4 s).
  selfdrived.py:402-415 raises the alert when saturated AND lateral active AND no steering press in 2 s
  AND |model desired lat accel| > 1.0 AND desired/achieved > 1.2.
  steer_limited_by_safety (controlsd.py:195) = |carControl torque - carOutput torque| > 0.01.

Mechanisms this module separates, using only qlog-available signals:
  A  torque authority: controller output pinned at +/-1.0 (= 350 raw units, the cap shared by the car
     controller and panda safety), car output matching it
  B  curvature limit: desired curvature at the 3.0 m/s^2 (+ roll) lateral-accel clip (the jerk clip is not
     observable at 10 Hz)
  ?  indeterminate: neither visible in the 10 Hz samples
"""
from __future__ import annotations

import math
import statistics

from . import constants as C
from .timeline import Index, joined_row

NS = 1e9


def limiter_step(cmd_norm: float, apply_last: int, eps_meas: float, steer_max: int = C.STEER_MAX,
                 delta_up: int = C.STEER_DELTA_UP, delta_down: int = C.STEER_DELTA_DOWN,
                 error_max: int = C.STEER_ERROR_MAX) -> int:
  """Offline copy of the drive-build car-side torque limiter for RAM_DT (one 50 Hz steering frame).

  Mirrors opendbc/car/lateral.py:50-75 (apply_dist_to_meas_limits / apply_meas_steer_torque_limits) and
  carcontroller.py:78-79. Used only to interpret logs; it is not, and must not become, a control path.
  """
  val = int(round(cmd_norm * steer_max))
  max_lim = min(max(eps_meas + error_max, error_max), steer_max)
  min_lim = max(min(eps_meas - error_max, -error_max), -steer_max)
  val = min(max(val, min_lim), max_lim)
  if apply_last > 0:
    val = min(max(val, max(apply_last - delta_down, -delta_up)), apply_last + delta_up)
  else:
    val = min(max(val, apply_last - delta_up), min(apply_last + delta_down, delta_up))
  return int(round(val))


def measured_window(eps_meas: float, steer_max: int = C.STEER_MAX, error_max: int = C.STEER_ERROR_MAX) -> tuple[float, float]:
  return (max(min(eps_meas - error_max, -error_max), -steer_max), min(max(eps_meas + error_max, error_max), steer_max))


def _f(row, key):
  v = row.get(key)
  return None if v is None else float(v)


def classify_row(row: dict, lat_accel_factor: float = C.OFFLINE_LAT_ACCEL_FACTOR) -> dict:
  out = {}
  output = _f(row, "controlsState.torqueState.output")
  v = _f(row, "carState.vEgo")
  cmd = _f(row, "carControl.actuators.torque")
  co = _f(row, "carOutput.actuatorsOutput.torque")
  eps = _f(row, "carState.steeringTorqueEps")
  dcurv = _f(row, "controlsState.desiredCurvature")
  roll = _f(row, "liveParameters.roll") or 0.0
  out["torque_saturated"] = None if output is None else abs(output) >= 1.0 - 1e-3
  out["speed_above_sat_min"] = None if v is None else v > C.SAT_CHECK_MIN_SPEED_MS
  out["steering_pressed"] = row.get("carState.steeringPressed")
  out["limited_by_safety_proxy"] = None if (cmd is None or co is None) else abs(cmd - co) > C.STEER_LIMITED_BY_SAFETY_EPS
  if cmd is not None and eps is not None:
    lo, hi = measured_window(eps)
    want = round(cmd * C.STEER_MAX)
    out["cmd_outside_measured_window"] = want < lo or want > hi
  else:
    out["cmd_outside_measured_window"] = None
  if dcurv is not None and v is not None:
    lat = dcurv * v * v
    cap_hi = C.MAX_LATERAL_ACCEL_NO_ROLL + roll * C.GRAVITY
    cap_lo = -C.MAX_LATERAL_ACCEL_NO_ROLL + roll * C.GRAVITY
    out["desired_lat_accel"] = lat
    out["at_lat_accel_clip"] = lat >= cap_hi - 0.05 or lat <= cap_lo + 0.05
  else:
    out["desired_lat_accel"] = None
    out["at_lat_accel_clip"] = None
  return out


def infer_controller_mode(rows: list[dict], lat_accel_factor: float = C.OFFLINE_LAT_ACCEL_FACTOR) -> dict:
  """Stock torque vs NNLC, from the logged PID terms.

  Stock (latcontrol_torque.py:77-81): output_torque = clip(p+i+f, +/-LAF) / LAF, logged output = -output_torque
     => -output / (p+i+f) ~= 1/LAF (0.5 with the offline LAF of 2.0).
  NNLC (nnlc.py:66-70,87-92): PID limits are +/-1.0 in torque space => ratio ~= 1.0.
  """
  ratios = []
  for r in rows:
    if not r.get("controlsState.torqueState.active"):
      continue
    out = _f(r, "controlsState.torqueState.output")
    terms = [_f(r, f"controlsState.torqueState.{k}") for k in ("p", "i", "d", "f")]
    if out is None or any(t is None for t in terms):
      continue
    s = sum(terms)
    if abs(out) >= 0.95 or abs(s) < 0.2:
      continue
    ratios.append(-out / s)
  if len(ratios) < 10:
    return {"mode": "unknown", "n": len(ratios), "median_ratio": statistics.median(ratios) if ratios else None}
  med = statistics.median(ratios)
  stock = 1.0 / lat_accel_factor
  if abs(med - stock) <= 0.15 * stock:
    mode = "stock_torque"
  elif abs(med - 1.0) <= 0.15:
    mode = "nnlc_torque_space"
  else:
    mode = "unknown"
  return {"mode": mode, "n": len(ratios), "median_ratio": round(med, 4), "expected_stock_ratio": stock}


def analyze_saturation_episode(index: Index, start_ns: int, end_ns: int | None, pre_core_s: float = 1.0,
                               post_core_s: float = 0.3) -> dict:
  """Classify one steerSaturated episode from the 10 Hz qlog samples around it."""
  end = end_ns if end_ns is not None else start_ns
  # the timer needs >= 0.4 s of saturation BEFORE the alert, so look back further than the alert itself
  t0, t1 = int(start_ns - (C.STEER_LIMIT_TIMER_S + pre_core_s) * NS), int(end + post_core_s * NS)
  rows = [joined_row(index, r["t"]) for r in index.between("controlsState", t0, t1)]
  flags = [classify_row(r) for r in rows]

  def count(key):
    return sum(1 for f in flags if f.get(key) is True)

  def known(key):
    return sum(1 for f in flags if f.get(key) is not None)

  def vals(key):
    return [float(r[key]) for r in rows if r.get(key) is not None]

  eps = vals("carState.steeringTorqueEps")
  col = vals("carState.steeringTorque")
  outs = vals("controlsState.torqueState.output")
  co_can = vals("carOutput.actuatorsOutput.torqueOutputCan")
  speeds = vals("carState.vEgo")
  des = vals("controlsState.torqueState.desiredLateralAccel")
  act = vals("controlsState.torqueState.actualLateralAccel")

  evidence = {
    "core_window_s": [round((t0 - start_ns) / NS, 2), round((t1 - start_ns) / NS, 2)],
    "controlsState_samples": len(rows),
    "torque_saturated_samples": count("torque_saturated"),
    "lat_accel_clip_samples": count("at_lat_accel_clip"),
    "limited_by_safety_proxy_samples": count("limited_by_safety_proxy"),
    "limited_by_safety_known_samples": known("limited_by_safety_proxy"),
    "cmd_outside_measured_window_samples": count("cmd_outside_measured_window"),
    "steering_pressed_samples": count("steering_pressed"),
    "speed_ms_min_max": [round(min(speeds), 2), round(max(speeds), 2)] if speeds else None,
    "abs_output_max": round(max(abs(x) for x in outs), 4) if outs else None,
    "abs_eps_motor_torque_max_raw": round(max(abs(x) for x in eps), 1) if eps else None,
    "abs_column_torque_max_raw": round(max(abs(x) for x in col), 1) if col else None,
    "abs_can_torque_sent_max_raw": round(max(abs(x) for x in co_can), 1) if co_can else None,
    "desired_lat_accel_abs_max": round(max(abs(x) for x in des), 3) if des else None,
    "actual_lat_accel_abs_at_desired_peak": None,
  }
  if des and act and len(des) == len(act):
    i = max(range(len(des)), key=lambda k: abs(des[k]))
    evidence["actual_lat_accel_abs_at_desired_peak"] = round(abs(act[i]), 3)

  if not rows or not outs:
    mechanism, confidence = "insufficient_data", "none"
  elif evidence["torque_saturated_samples"] > 0:
    mechanism = "A_torque_authority"
    confidence = "high" if evidence["torque_saturated_samples"] >= 2 else "medium"
  elif evidence["lat_accel_clip_samples"] > 0:
    mechanism, confidence = "B_curvature_lat_accel_clip", "medium"
  else:
    mechanism, confidence = "indeterminate_at_qlog_rate", "low"

  eps_note = None
  if mechanism == "A_torque_authority" and eps:
    sat_eps = [abs(float(r["carState.steeringTorqueEps"])) for r, f in zip(rows, flags, strict=False)
               if f.get("torque_saturated") and r.get("carState.steeringTorqueEps") is not None]
    if sat_eps:
      med = statistics.median(sat_eps)
      evidence["median_abs_eps_while_saturated_raw"] = round(med, 1)
      eps_note = ("EPS motor torque was inside the +/-80 window of the 350 cap: the command was being delivered"
                  if med >= C.STEER_MAX - C.STEER_ERROR_MAX - 5 else
                  "EPS motor torque was BELOW the window needed to pass a 350 command; check sample skew and the "
                  "limited_by_safety proxy before concluding anything")
  return {"mechanism": mechanism, "confidence": confidence, "evidence": evidence, "eps_note": eps_note}


def route_saturation_stats(index: Index, near_thresh: float = 0.95, min_run_s: float = 0.3) -> dict:
  """How often the controller ran at or near its cap -- the single alert says nothing about this."""
  rows = index.by_service.get("controlsState", [])
  active = [r for r in rows if r.get("torqueState.active")]
  outs = [abs(float(r["torqueState.output"])) for r in active if r.get("torqueState.output") is not None]
  bins = [0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.999, 1.01]
  hist = {f"{a:.3g}-{b:.3g}": sum(1 for x in outs if a <= x < b) for a, b in zip(bins, bins[1:], strict=False)}
  runs, cur = [], []
  for r in active:
    o = r.get("torqueState.output")
    if o is not None and abs(float(o)) >= near_thresh:
      if cur and (r["t"] - cur[-1]["t"]) / NS > 0.25:   # sample gap breaks a run
        runs.append(cur)
        cur = []
      cur.append(r)
    elif cur:
      runs.append(cur)
      cur = []
  if cur:
    runs.append(cur)
  near = [{"start_ns": run[0]["t"], "end_ns": run[-1]["t"], "duration_s": round((run[-1]["t"] - run[0]["t"]) / NS + 0.1, 2),
           "samples": len(run)} for run in runs if (run[-1]["t"] - run[0]["t"]) / NS + 0.1 >= min_run_s]

  bands = [(0, 10), (10, 20), (20, 30), (30, 99)]
  track = {}
  for lo, hi in bands:
    errs = []
    for r in active:
      row_v = None
      cs, _ = index.asof("carState", r["t"], 0.15)
      if cs is not None and cs.get("vEgo") is not None:
        row_v = float(cs["vEgo"])
      d, a = r.get("torqueState.desiredLateralAccel"), r.get("torqueState.actualLateralAccel")
      if row_v is None or d is None or a is None or not (lo <= row_v < hi):
        continue
      errs.append(abs(float(d) - float(a)))
    if errs:
      errs.sort()
      track[f"{lo}-{hi} m/s"] = {"n": len(errs), "p50": round(errs[len(errs) // 2], 3),
                                 "p95": round(errs[min(len(errs) - 1, math.floor(0.95 * len(errs)))], 3)}
  return {
    "active_samples": len(active),
    "abs_output_histogram": hist,
    "fraction_active_at_or_above_0.9": round(sum(1 for x in outs if x >= 0.9) / len(outs), 4) if outs else None,
    "near_saturation_runs": near,
    "lat_accel_tracking_error_by_speed": track,
  }
