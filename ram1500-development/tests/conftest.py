import os
import sys
from pathlib import Path

import pytest

DEV = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEV))

NS = 1_000_000_000
T0 = 5_000 * NS  # arbitrary boot-relative start


def rec(service, t_s, **fields):
  return {"t": int(T0 + t_s * NS), "service": service, "valid": True, "segment": int(t_s // 60), **fields}


def drive(duration_s, v=25.0, lat_active=True, sat_windows=(), clip_windows=(), eps_sat=330.0,
          end_shutdown=False, mode="stock", laf=2.0):
  """Synthetic 10 Hz qlog-like records.

  sat_windows: (t0, t1) where controller output is pinned at -1.0 (left-positive logging convention).
  clip_windows: (t0, t1) where desired curvature sits at the 3.0 m/s^2 lateral-accel clip, output unsaturated.
  """
  out = []
  n = int(duration_s * 10)
  for k in range(n):
    t = k / 10
    in_sat = any(a <= t <= b for a, b in sat_windows)
    in_clip = any(a <= t <= b for a, b in clip_windows)
    speed = v
    if end_shutdown and t > duration_s - 8:
      speed = max(0.0, v * (duration_s - 3 - t) / 5)
    active = lat_active and speed > 0.5
    cmd = 1.0 if in_sat else 0.2
    p, i = 0.1, 0.05
    if in_sat:
      f = 2.5
    else:
      f = 0.25
    s = p + i + f
    out_norm = 1.0 if in_sat else (s / laf if mode == "stock" else s)
    out_norm = min(out_norm, 1.0)
    des_lat = 3.0 if (in_clip or in_sat) else 0.4
    act_lat = 1.8 if (in_clip or in_sat) else 0.38
    out.append(rec("carState", t + 0.001, vEgo=speed, steeringAngleDeg=5.0, steeringTorque=10.0,
                   steeringTorqueEps=eps_sat if in_sat else 70.0, steeringPressed=False, gearShifter="drive",
                   canValid=not (end_shutdown and t > duration_s - 2), canTimeout=False, standstill=speed < 0.01))
    out.append(rec("carControl", t + 0.002, enabled=active, latActive=active, longActive=False,
                   **{"actuators.torque": cmd if active else 0.0, "actuators.curvature": des_lat / max(speed, 1) ** 2}))
    out.append(rec("carOutput", t + 0.003, **{"actuatorsOutput.torque": cmd if active else 0.0,
                                              "actuatorsOutput.torqueOutputCan": round(cmd * 350) if active else 0}))
    out.append(rec("controlsState", t + 0.004, curvature=act_lat / max(speed, 1) ** 2,
                   desiredCurvature=des_lat / max(speed, 1) ** 2, lateralControlState="torqueState",
                   **{"torqueState.active": active, "torqueState.output": -out_norm if active else 0.0,
                      "torqueState.p": p, "torqueState.i": i, "torqueState.d": 0.0, "torqueState.f": f,
                      "torqueState.saturated": in_sat, "torqueState.desiredLateralAccel": des_lat,
                      "torqueState.actualLateralAccel": act_lat}))
    out.append(rec("selfdriveState", t + 0.005, enabled=active, active=active))
    ign = not (end_shutdown and t > duration_s - 2)
    out.append(rec("pandaStates", t + 0.006, **{"panda0.ignitionLine": ign, "panda0.ignitionCan": ign,
                                                "panda0.controlsAllowed": active, "panda0.safetyRxChecksInvalid": not ign}))
    if k % 25 == 0:
      out.append(rec("liveParameters", t + 0.007, roll=0.0, valid=True))
  out.sort(key=lambda r: r["t"])
  return out


def events(records, timeline):
  """timeline: list of (t_s, [names]) onroadEvents messages."""
  ev = [rec("onroadEvents", t, names=list(names)) for t, names in timeline]
  return sorted(records + ev, key=lambda r: r["t"])


@pytest.fixture
def schema_dir():
  d = os.environ.get("RAM1500_SCHEMA_DIR")
  if not d:
    pytest.skip("set RAM1500_SCHEMA_DIR to a pinned schema dir (investigate_route.py --fetch-schema creates one)")
  pytest.importorskip("capnp")
  pytest.importorskip("zstandard")
  from ramtools.schema import schema_is_pinned
  if not schema_is_pinned(Path(d)):
    pytest.skip(f"{d} is not the pinned drive-build schema")
  return Path(d)
