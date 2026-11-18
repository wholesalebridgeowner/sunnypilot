import random

import pytest
from conftest import NS, T0, drive, events
from ramtools import constants as C
from ramtools.episodes import build_episodes, event_messages
from ramtools.saturation import (analyze_saturation_episode, classify_row, infer_controller_mode, limiter_step,
                                 measured_window, route_saturation_stats)
from ramtools.timeline import Index


def reference_limiter(apply_torque, apply_torque_last, meas, up, down, err, smax):
  # verbatim logic of opendbc/car/lateral.py:50-75 at the drive commit (numpy-free)
  max_lim = min(max(meas + err, err), smax)
  min_lim = max(min(meas - err, -err), -smax)
  val = min(max(apply_torque, min_lim), max_lim)
  if apply_torque_last > 0:
    val = min(max(val, max(apply_torque_last - down, -up)), apply_torque_last + up)
  else:
    val = min(max(val, apply_torque_last - up), min(apply_torque_last + down, up))
  return int(round(val))


def test_limiter_matches_reference_fuzz():
  rng = random.Random(7)
  for _ in range(20000):
    cmd = rng.uniform(-1.2, 1.2)
    last = rng.randint(-350, 350)
    meas = rng.uniform(-500, 500)
    want = reference_limiter(int(round(cmd * 350)), last, meas, 6, 6, 80, 350)
    assert limiter_step(cmd, last, meas) == want


PARITY_SCRIPT = r"""
import random, sys
sys.path.insert(0, sys.argv[1])
try:
  from opendbc.car import lateral
except Exception:
  sys.exit(3)
from ramtools.saturation import limiter_step
class P:
  STEER_DELTA_UP, STEER_DELTA_DOWN, STEER_ERROR_MAX, STEER_MAX = 6, 6, 80, 350
rng = random.Random(11)
for _ in range(5000):
  cmd, last, meas = rng.uniform(-1.2, 1.2), rng.randint(-350, 350), rng.uniform(-500, 500)
  a = limiter_step(cmd, last, meas)
  b = lateral.apply_meas_steer_torque_limits(int(round(cmd * 350)), last, meas, P)
  if a != b:
    print(cmd, last, meas, a, b)
    sys.exit(1)
"""


def test_limiter_matches_opendbc_if_importable():
  # Separate process: opendbc loads this branch's car.capnp, whose schema ids collide with the pinned drive-build
  # schema; loading both in one process makes pycapnp abort.
  import os
  import subprocess
  import sys
  from conftest import DEV
  env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(DEV.parent / "opendbc_repo"), os.environ.get("PYTHONPATH", "")])}
  r = subprocess.run([sys.executable, "-c", PARITY_SCRIPT, str(DEV)], env=env, capture_output=True, text=True, timeout=120)
  if r.returncode == 3:
    pytest.skip("opendbc (this branch's opendbc_repo submodule + pycapnp) not importable")
  assert r.returncode == 0, r.stdout + r.stderr


def test_full_torque_needs_eps_feedback():
  # From rest the command ramps 6 units per 50 Hz frame and can never exceed measured + 80.
  apply, frames = 0, 0
  while apply < 350 and frames < 1000:
    apply = limiter_step(1.0, apply, eps_meas=apply)  # EPS tracks the command perfectly
    frames += 1
  assert apply == 350 and frames == 59  # ceil(350/6) frames = 1.18 s minimum rise time
  # with a weak EPS response the ceiling becomes meas + 80, and the command walks DOWN to it at 6 units/frame
  assert measured_window(150) == (-80, 230)
  assert limiter_step(1.0, 300, eps_meas=150) == 294
  apply = 300
  for _ in range(20):
    apply = limiter_step(1.0, apply, eps_meas=150)
  assert apply == 230


def test_classify_row_flags():
  row = {"controlsState.torqueState.output": -1.0, "carState.vEgo": 25.0, "carControl.actuators.torque": 1.0,
         "carOutput.actuatorsOutput.torque": 1.0, "carState.steeringTorqueEps": 300.0,
         "controlsState.desiredCurvature": 3.0 / 625, "liveParameters.roll": 0.0}
  f = classify_row(row)
  assert f["torque_saturated"] and f["speed_above_sat_min"] and not f["limited_by_safety_proxy"]
  assert not f["cmd_outside_measured_window"] and f["at_lat_accel_clip"]
  row["carState.steeringTorqueEps"] = 100.0
  assert classify_row(row)["cmd_outside_measured_window"]
  assert classify_row({})["torque_saturated"] is None


def _sat_index(**kw):
  # 1 Hz onroadEvents ticks; the alert appears at 170.9 (change), is repeated by the 171.0 tick, clears at 171.3
  ev = [(float(t), []) for t in range(200) if t != 171] + [(170.9, ["steerSaturated"]), (171.0, ["steerSaturated"]),
                                                            (171.3, [])]
  recs = events(drive(200, **kw), ev)
  return recs, Index(recs)


def test_mechanism_a_torque_authority():
  recs, idx = _sat_index(sat_windows=[(170.4, 171.3)], eps_sat=320.0)
  ep = build_episodes(event_messages(recs))["steerSaturated"][0]
  res = analyze_saturation_episode(idx, ep.start_ns, ep.end_ns)
  assert res["mechanism"] == "A_torque_authority" and res["confidence"] == "high"
  assert res["evidence"]["abs_can_torque_sent_max_raw"] == 350
  assert "inside the +/-80 window" in res["eps_note"]


def test_mechanism_a_with_weak_eps_is_flagged_not_explained():
  recs, idx = _sat_index(sat_windows=[(170.4, 171.3)], eps_sat=120.0)
  ep = build_episodes(event_messages(recs))["steerSaturated"][0]
  res = analyze_saturation_episode(idx, ep.start_ns, ep.end_ns)
  assert res["mechanism"] == "A_torque_authority"
  assert "BELOW the window" in res["eps_note"]


def test_mechanism_b_curvature_clip():
  recs, idx = _sat_index(clip_windows=[(170.4, 171.3)])
  ep = build_episodes(event_messages(recs))["steerSaturated"][0]
  res = analyze_saturation_episode(idx, ep.start_ns, ep.end_ns)
  assert res["mechanism"] == "B_curvature_lat_accel_clip"


def test_indeterminate_and_insufficient():
  recs, idx = _sat_index()
  ep = build_episodes(event_messages(recs))["steerSaturated"][0]
  assert analyze_saturation_episode(idx, ep.start_ns, ep.end_ns)["mechanism"] == "indeterminate_at_qlog_rate"
  empty = Index([])
  assert analyze_saturation_episode(empty, T0, T0 + NS)["mechanism"] == "insufficient_data"


@pytest.mark.parametrize("mode,expected", [("stock", "stock_torque"), ("nnlc", "nnlc_torque_space")])
def test_controller_mode_inference(mode, expected):
  recs = drive(30, mode=mode)
  rows = [{f"controlsState.{k}": v for k, v in r.items()} for r in recs if r["service"] == "controlsState"]
  res = infer_controller_mode(rows)
  assert res["mode"] == expected, res


def test_route_stats_count_near_saturation_runs_without_alerts():
  recs = drive(120, sat_windows=[(20.0, 20.5), (50.0, 51.0), (80.0, 80.1)])
  stats = route_saturation_stats(Index(recs))
  assert len(stats["near_saturation_runs"]) == 2  # the 0.1 s blip is below the 0.3 s floor
  assert stats["fraction_active_at_or_above_0.9"] > 0
  assert "20-30 m/s" in stats["lat_accel_tracking_error_by_speed"]


def test_constants_are_drive_build_values():
  assert (C.STEER_MAX, C.STEER_DELTA_UP, C.STEER_DELTA_DOWN, C.STEER_ERROR_MAX) == (350, 6, 6, 80)
  assert (C.SAFETY_MAX_TORQUE, C.SAFETY_MAX_RATE_UP, C.SAFETY_MAX_TORQUE_ERROR) == (350, 6, 80)
  assert C.STEER_LIMIT_TIMER_S == 0.4 and C.OFFLINE_LAT_ACCEL_FACTOR == 2.0
  assert "chrysler" not in C.TORQUED_ALLOWED_BRANDS
