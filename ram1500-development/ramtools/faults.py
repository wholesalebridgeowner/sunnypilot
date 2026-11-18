"""Context and classification for fault-type events (canError, controlsMismatch, ...).

Drive-build sources of the two end-of-route events (1f7233cb):
  controlsMismatch  selfdrived.py:321-322  safety model/param mismatch after 10 s, OR pandaState.safetyRxChecksInvalid,
                                           OR selfdrived enabled while panda controlsAllowed is false for >= 200 frames
  canError          selfdrived.py:347-348  radarState.radarErrors.canError, OR
                    selfdrived.py:355-358  carState.canValid false (canTimeout gives canBusMissing instead)

When the ignition goes off, the car's ECUs stop transmitting: panda RX checks go invalid and carState.canValid
drops, so both events are expected in the last seconds of a drive. Classification below separates that from
anything that happened while moving, and especially while lateral control was active.
"""
from __future__ import annotations

from .timeline import Index

NS = 1e9

FAULT_EVENTS = ("canError", "canBusMissing", "controlsMismatch", "commIssue", "commIssueAvgFreq", "usbError",
                "relayMalfunction", "steerTempUnavailable", "steerUnavailable", "processNotRunning",
                "selfdrivedLagging", "radarFault", "sensorDataInvalid", "steerTimeLimit")

MOVING_MS = 0.5
EDGE_S = 30.0


def _any(recs, key):
  vals = [r.get(key) for r in recs if r.get(key) is not None]
  return None if not vals else any(bool(v) for v in vals)


def _all(recs, key):
  vals = [r.get(key) for r in recs if r.get(key) is not None]
  return None if not vals else all(bool(v) for v in vals)


def fault_context(index: Index, start_ns: int, route_start_ns: int, route_end_ns: int, look_s: float = 1.0) -> dict:
  a, b = int(start_ns - look_s * NS), int(start_ns + look_s * NS)
  cs = index.between("carState", a, b)
  cc = index.between("carControl", a, b)
  ss = index.between("selfdriveState", a, b)
  ps = index.between("pandaStates", a, b)
  rs = index.between("radarState", a, b)
  speeds = [float(r["vEgo"]) for r in cs if r.get("vEgo") is not None]
  gears = sorted({str(r["gearShifter"]) for r in cs if r.get("gearShifter") is not None})
  prior = index.between("carState", int(start_ns - 10 * NS), start_ns)
  prior_speeds = [float(r["vEgo"]) for r in prior if r.get("vEgo") is not None]
  return {
    "route_s": round((start_ns - route_start_ns) / NS, 2),
    "s_before_route_end": round((route_end_ns - start_ns) / NS, 2),
    "vEgo_max_ms": round(max(speeds), 2) if speeds else None,
    "vEgo_max_prior_10s_ms": round(max(prior_speeds), 2) if prior_speeds else None,
    "gears": gears,
    "latActive_any": _any(cc, "latActive"),
    "cc_enabled_any": _any(cc, "enabled"),
    "selfdrive_enabled_any": _any(ss, "enabled"),
    "canValid_all": _all(cs, "canValid"),
    "canTimeout_any": _any(cs, "canTimeout"),
    "ignitionLine_all": _all(ps, "panda0.ignitionLine"),
    "ignitionCan_all": _all(ps, "panda0.ignitionCan"),
    "ignition_any": None if not ps else any(bool(r.get("panda0.ignitionLine")) or bool(r.get("panda0.ignitionCan")) for r in ps),
    "safetyRxChecksInvalid_any": _any(ps, "panda0.safetyRxChecksInvalid"),
    "controlsAllowed_any": _any(ps, "panda0.controlsAllowed"),
    "radar_canError_any": _any(rs, "radarErrors.canError"),
  }


def classify(ctx: dict) -> tuple[str, str]:
  """Return (classification, severity). Missing data never upgrades to 'benign'."""
  v = ctx.get("vEgo_max_ms")
  if v is None:
    return "unclassified_missing_speed", "unknown"
  engaged = bool(ctx.get("latActive_any") or ctx.get("cc_enabled_any") or ctx.get("selfdrive_enabled_any"))
  moving = v >= MOVING_MS
  if moving and engaged:
    return "in_motion_engaged", "high"
  if moving:
    return "in_motion_not_engaged", "medium"
  parked = any(g.lower().endswith("park") for g in ctx.get("gears", []))
  ignition_off = ctx.get("ignition_any") is False
  if ctx.get("s_before_route_end", 1e9) <= EDGE_S or ignition_off or parked:
    return "stationary_shutdown_or_parked", "low"
  if ctx.get("route_s", 1e9) <= EDGE_S:
    return "stationary_startup", "low"
  return "stationary_other", "low"


def cause_hints(name: str, ctx: dict) -> list[str]:
  hints = []
  if name == "canError":
    if ctx.get("radar_canError_any"):
      hints.append("radarState.radarErrors.canError set (radar path)")
    if ctx.get("canValid_all") is False:
      hints.append("carState.canValid false in window (car CAN parser path)")
    if ctx.get("canTimeout_any"):
      hints.append("carState.canTimeout true (would normally raise canBusMissing)")
  if name == "controlsMismatch":
    if ctx.get("safetyRxChecksInvalid_any"):
      hints.append("panda safetyRxChecksInvalid (expected RX messages missing/invalid, e.g. ECUs powering down)")
    if ctx.get("selfdrive_enabled_any") and ctx.get("controlsAllowed_any") is False:
      hints.append("selfdrived enabled while panda controlsAllowed false")
  if ctx.get("ignition_any") is False:
    hints.append("panda reports ignition off")
  if not hints:
    hints.append("no discriminating signal in qlog window; needs rlog")
  return hints


def classify_fault_episodes(index: Index, episodes: dict, route_start_ns: int, route_end_ns: int,
                            names=FAULT_EVENTS) -> list[dict]:
  out = []
  for name in names:
    for ep in episodes.get(name, []):
      ctx = fault_context(index, ep.start_ns, route_start_ns, route_end_ns)
      cls, sev = classify(ctx)
      out.append({"event": name, "start_ns": ep.start_ns, "duration_s": ep.duration_s, "samples": ep.samples,
                  "classification": cls, "severity": sev, "hints": cause_hints(name, ctx), **ctx})
  out.sort(key=lambda d: d["start_ns"])
  return out
