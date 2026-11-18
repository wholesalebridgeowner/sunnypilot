"""Route-level summaries: provenance, CarParams, error logs, time anchors, persistence of startup errors."""
from __future__ import annotations

from collections import Counter

from . import constants as C
from .timeline import Index

NS = 1e9

# cloudlog events that are logged with error=True by design and are not, on their own, faults.
ROUTINE_ERROR_EVENTS = {
  "selfdrived.initialized": "always logged with error=True at startup (selfdrived.py:458-485); inspect 'timeout' and the lists",
}


def provenance(index: Index) -> dict:
  inits = index.by_service.get("initData", [])
  if not inits:
    return {"initData": None}
  first = inits[0]
  commits = sorted({r.get("gitCommit") for r in inits if r.get("gitCommit")})
  return {
    "version": first.get("version"),
    "gitCommit": commits,
    "gitBranch": first.get("gitBranch"),
    "dirty": first.get("dirty"),
    "deviceType": first.get("deviceType"),
    "matches_pinned_drive_build": commits == [C.DRIVE_BUILD_COMMIT],
    "control_params": first.get("params", {}),
  }


def carparams_summary(index: Index) -> dict:
  cps = index.by_service.get("carParams", [])
  out = {}
  if cps:
    cp = {k: v for k, v in cps[-1].items() if k not in ("t", "service", "valid", "segment")}
    cp["carFw"] = [fw for fw in cp.get("carFw", []) if fw.get("ecu") in ("eps", "fwdCamera", "fwdRadar", "abs", "engine")]
    out["carParams"] = cp
    checks = {}
    tune_laf = cp.get("lateralTuning.torque.latAccelFactor")
    checks["latAccelFactor_is_placeholder_2.0"] = None if tune_laf is None else abs(float(tune_laf) - C.OFFLINE_LAT_ACCEL_FACTOR) < 1e-6
    checks["steerLimitTimer_0.4"] = cp.get("steerLimitTimer") is not None and abs(float(cp["steerLimitTimer"]) - 0.4) < 1e-6
    checks["minEnableSpeed"] = cp.get("minEnableSpeed")
    checks["minSteerSpeed"] = cp.get("minSteerSpeed")
    checks["brand_in_torqued_allowlist"] = None if cp.get("brand") is None else cp.get("brand") in C.TORQUED_ALLOWED_BRANDS
    out["checks"] = checks
  sps = index.by_service.get("carParamsSP", [])
  if sps:
    out["carParamsSP"] = {k: v for k, v in sps[-1].items() if k not in ("t", "service", "valid", "segment")}
  ltp = index.by_service.get("liveTorqueParameters", [])
  if ltp:
    use = Counter(bool(r.get("useParams")) for r in ltp)
    valid = Counter(bool(r.get("liveValid")) for r in ltp)
    laf = [float(r["latAccelFactorFiltered"]) for r in ltp if r.get("latAccelFactorFiltered") is not None]
    out["liveTorqueParameters"] = {
      "samples": len(ltp), "useParams_counts": dict(use), "liveValid_counts": dict(valid),
      "latAccelFactorFiltered_first_last": [laf[0], laf[-1]] if laf else None,
      "note": "useParams False means these values were published but NOT applied to the controller",
    }
  return out


def time_anchors(index: Index) -> dict:
  """Map logMonoTime to wall clock. GPS time is preferred; initData wall time can be unsynced at boot."""
  anchors = {}
  for svc in ("gpsLocationExternal", "gpsLocation"):
    for r in index.by_service.get(svc, []):
      if r.get("hasFix") and r.get("unixTimestampMillis"):
        anchors["gps"] = {"service": svc, "logMonoTime": r["t"], "unix_ms": int(r["unixTimestampMillis"])}
        break
    if "gps" in anchors:
      break
  inits = index.by_service.get("initData", [])
  if inits and inits[0].get("wallTimeNanos"):
    anchors["initData"] = {"logMonoTime": inits[0]["t"], "unix_ms": int(inits[0]["wallTimeNanos"]) // 1_000_000}
  return anchors


def mono_to_unix_ms(t_ns: int, anchors: dict) -> int | None:
  a = anchors.get("gps") or anchors.get("initData")
  if not a:
    return None
  return a["unix_ms"] + (t_ns - a["logMonoTime"]) // 1_000_000


def error_log_summary(index: Index, route_start_ns: int, early_s: float = 60.0) -> dict:
  """Group errorLogMessage lines and show whether each kind persisted past the first `early_s` seconds."""
  groups: dict[str, dict] = {}
  for r in index.by_service.get("errorLogMessage", []):
    key = str(r.get("event") or r.get("msg") or "?")[:120]
    g = groups.setdefault(key, {"count": 0, "first_s": None, "last_s": None, "after_early_window": 0})
    s = (r["t"] - route_start_ns) / NS
    g["count"] += 1
    g["first_s"] = s if g["first_s"] is None else min(g["first_s"], s)
    g["last_s"] = s if g["last_s"] is None else max(g["last_s"], s)
    if s > early_s:
      g["after_early_window"] += 1
    if key in ROUTINE_ERROR_EVENTS:
      g["note"] = ROUTINE_ERROR_EVENTS[key]
      for k in ("timeout", "dt", "canValid", "invalid", "not_alive", "not_freq_ok"):
        if k in r:
          g.setdefault("details", {})[k] = r[k]
  for g in groups.values():
    g["first_s"] = round(g["first_s"], 2)
    g["last_s"] = round(g["last_s"], 2)
    g["persisted"] = g["after_early_window"] > 0
  return dict(sorted(groups.items(), key=lambda kv: -kv[1]["count"]))
