"""Decode comma qlog/rlog files into small, privacy-minimised Python records.

A record is a plain dict: {"t": logMonoTime ns, "service": name, "valid": bool, "segment": int|None, ...fields}.
Only whitelisted fields are extracted. Deliberately never extracted: VIN, dongle id, GPS position,
camera/model data, device context blobs inside error logs, and any param not on PARAM_WHITELIST.
"""
from __future__ import annotations

import bz2
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

SEGMENT_RE = re.compile(r"(?P<route>[0-9a-f]{8}--[0-9a-f]{10})--(?P<seg>\d+)")

# Control-relevant params only. Anything else in initData.params is dropped.
PARAM_WHITELIST = re.compile(
  r"^(NeuralNetworkLateralControl|EnforceTorqueControl|TorqueParamsOverride\w*|CustomTorqueParams\w*|LiveTorque\w*|" +
  r"Mads\w*|ExperimentalMode|AlphaLongitudinalEnabled|DisengageOnAccelerator|LagdToggle\w*|LiveDelay\w*|" +
  r"BlinkerPause\w*|LaneTurn\w*|IsLdwEnabled|OpenpilotEnabledToggle|AutoLaneChange\w*)$")
PARAM_MAX_BYTES = 32

ERROR_LOG_KEEP = ("event", "level", "filename", "lineno", "funcname", "timeout", "dt", "canValid",
                  "invalid", "not_alive", "not_freq_ok", "not_running")


@dataclass
class FileReadResult:
  path: str
  segment: int | None
  messages: int = 0
  truncated: bool = False
  error: str | None = None
  services: dict = field(default_factory=dict)


def segment_of(path: Path | str) -> tuple[str | None, int | None]:
  """Route name and segment number from a comma path such as .../000003cc--27a46b2116--2/qlog.zst."""
  matches = list(SEGMENT_RE.finditer(str(path).replace("\\", "/")))
  if not matches:
    return None, None
  m = matches[-1]  # the innermost (last) route--segment token in the path
  return m.group("route"), int(m.group("seg"))


def read_log_bytes(path: Path) -> tuple[bytes, str | None]:
  """Return (decompressed bytes, error). A truncated stream returns the bytes recovered before the error."""
  raw = Path(path).read_bytes()
  name = str(path)
  if name.endswith(".zst"):
    import io

    import zstandard
    out = io.BytesIO()
    try:
      with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw), read_across_frames=True) as r:
        while chunk := r.read(1 << 20):
          out.write(chunk)
    except zstandard.ZstdError as e:
      return out.getvalue(), f"zstd: {e}"
    return out.getvalue(), None
  if name.endswith(".bz2"):
    try:
      return bz2.decompress(raw), None
    except (OSError, EOFError) as e:
      return b"", f"bz2: {e}"
  return raw, None


def _get(obj, dotted: str):
  cur = obj
  for part in dotted.split("."):
    try:
      cur = getattr(cur, part)
    except Exception:
      return None
  return _plain(cur)


def _plain(v):
  if v is None or isinstance(v, (bool, int, float, str)):
    return v
  if isinstance(v, bytes):
    return v.decode("ascii", "replace")
  s = str(v)
  return s


# service -> list of dotted field paths copied verbatim into the record
FIELDS: dict[str, list[str]] = {
  "carState": ["vEgo", "vEgoRaw", "aEgo", "standstill", "steeringAngleDeg", "steeringRateDeg", "steeringTorque",
               "steeringTorqueEps", "steeringPressed", "steerFaultTemporary", "steerFaultPermanent", "gearShifter",
               "canValid", "canTimeout", "canErrorCounter", "leftBlinker", "rightBlinker", "gasPressed", "brakePressed",
               "cruiseState.enabled", "cruiseState.available", "cruiseState.standstill", "cruiseState.speed"],
  "carControl": ["enabled", "latActive", "longActive", "actuators.accel", "actuators.torque", "actuators.curvature",
                 "actuators.steeringAngleDeg", "currentCurvature"],
  "carOutput": ["actuatorsOutput.torque", "actuatorsOutput.torqueOutputCan"],
  "controlsState": ["curvature", "desiredCurvature"],
  "selfdriveState": ["enabled", "active", "state", "alertType", "alertStatus", "engageable"],
  "liveTorqueParameters": ["useParams", "liveValid", "latAccelFactorFiltered", "latAccelOffsetFiltered",
                           "frictionCoefficientFiltered", "latAccelFactorRaw", "frictionCoefficientRaw",
                           "totalBucketPoints", "calPerc", "decay", "maxResets", "version"],
  "liveParameters": ["valid", "roll", "angleOffsetDeg", "angleOffsetAverageDeg", "steerRatio", "stiffnessFactor"],
  "liveDelay": ["lateralDelay", "lateralDelayEstimate", "validBlocks", "status"],
  "radarState": ["leadOne.status", "leadOne.dRel", "leadOne.vRel", "leadOne.vLead", "leadOne.modelProb", "leadOne.radar", "radarErrors.canError", "radarErrors.radarFault", "radarErrors.wrongConfig",
                 "radarErrors.radarUnavailableTemporary"],
  "longitudinalPlan": ["hasLead", "aTarget", "shouldStop", "allowThrottle", "allowBrake", "longitudinalPlanSource"],
  "drivingModelData": ["meta.laneChangeState", "meta.laneChangeDirection", "laneLineMeta.leftProb", "laneLineMeta.rightProb"],
}
TORQUE_STATE_FIELDS = ["active", "error", "p", "i", "d", "f", "output", "saturated", "actualLateralAccel",
                       "desiredLateralAccel"]
PANDA_FIELDS = ["ignitionLine", "ignitionCan", "controlsAllowed", "safetyRxChecksInvalid", "safetyTxBlocked",
                "safetyRxInvalid", "safetyModel", "safetyParam", "alternativeExperience", "harnessStatus", "pandaType"]
CARPARAMS_FIELDS = ["carFingerprint", "brand", "minSteerSpeed", "minEnableSpeed", "steerActuatorDelay", "steerLimitTimer",
                    "steerControlType", "steerAtStandstill", "steerRatio", "wheelbase", "mass", "centerToFront",
                    "tireStiffnessFactor", "radarUnavailable", "openpilotLongitudinalControl", "pcmCruise",
                    "dashcamOnly", "flags", "alternativeExperience", "enableBsm",
                    "lateralTuning.torque.kp", "lateralTuning.torque.ki", "lateralTuning.torque.kf",
                    "lateralTuning.torque.friction", "lateralTuning.torque.latAccelFactor",
                    "lateralTuning.torque.latAccelOffset", "lateralTuning.torque.steeringAngleDeadzoneDeg"]


def event_to_record(evt, segment: int | None = None) -> dict | None:
  """Convert a pycapnp log.Event reader into a minimal record (None for services we don't keep)."""
  which = evt.which()
  rec = {"t": int(evt.logMonoTime), "service": which, "valid": bool(evt.valid), "segment": segment}
  msg = getattr(evt, which)
  if which in FIELDS:
    for f in FIELDS[which]:
      rec[f] = _get(msg, f)
    if which == "controlsState":
      lcs = msg.lateralControlState
      kind = lcs.which()
      rec["lateralControlState"] = kind
      if kind == "torqueState":
        for f in TORQUE_STATE_FIELDS:
          rec["torqueState." + f] = _get(lcs.torqueState, f)
  elif which == "onroadEvents":
    # Installed log.capnp uses a flat List(OnroadEvent).
    rec["names"] = [str(e.name) for e in msg]
  elif which == "onroadEventsSP":
    # Installed custom.capnp uses a struct with nested events List.
    # Treating this struct as a list falsely reports intact R-logs truncated.
    rec["names"] = [str(e.name) for e in msg.events]
  elif which == "pandaStates":
    rec["n_pandas"] = len(msg)
    if len(msg):
      for f in PANDA_FIELDS:
        rec["panda0." + f] = _get(msg[0], f)
      rec["panda0.faults"] = [str(x) for x in msg[0].faults]
  elif which == "carParams":
    for f in CARPARAMS_FIELDS:
      rec[f] = _get(msg, f)
    rec["lateralTuning"] = msg.lateralTuning.which()
    rec["safetyConfigs"] = [{"safetyModel": str(s.safetyModel), "safetyParam": int(s.safetyParam)} for s in msg.safetyConfigs]
    rec["carFw"] = [{"ecu": str(fw.ecu), "fwVersion": bytes(fw.fwVersion).decode("ascii", "replace"),
                     "address": int(fw.address)} for fw in msg.carFw]
  elif which == "carParamsSP":
    rec["flags"] = _get(msg, "flags")
    rec["safetyParam"] = _get(msg, "safetyParam")
    rec["nnlcModelName"] = _get(msg, "neuralNetworkLateralControl.model.name")
    rec["nnlcFuzzyFingerprint"] = _get(msg, "neuralNetworkLateralControl.fuzzyFingerprint")
  elif which == "initData":
    rec["wallTimeNanos"] = _get(msg, "wallTimeNanos")
    for f in ("version", "gitCommit", "gitBranch", "dirty", "deviceType"):
      rec[f] = _get(msg, f)
    params = {}
    try:
      for entry in msg.params.entries:
        key = str(entry.key)
        if PARAM_WHITELIST.match(key) and len(entry.value) <= PARAM_MAX_BYTES:
          params[key] = bytes(entry.value).decode("ascii", "replace")
    except Exception:
      pass
    rec["params"] = params
  elif which in ("gpsLocationExternal", "gpsLocation"):
    # time anchor only: position is deliberately NOT extracted
    rec["unixTimestampMillis"] = _get(msg, "unixTimestampMillis")
    rec["hasFix"] = _get(msg, "hasFix")
  elif which == "errorLogMessage":
    rec.update(parse_error_log(str(msg)))
  else:
    return None
  return rec


def parse_error_log(text: str) -> dict:
  """Keep only non-identifying keys of a cloudlog JSON line (drops 'ctx', which holds device identity)."""
  out = {"raw_len": len(text)}
  try:
    j = json.loads(text)
  except ValueError:
    out["msg"] = text[:200]
    return out
  msg = j.get("msg")
  if isinstance(msg, dict):
    for k in ERROR_LOG_KEEP:
      if k in msg:
        out[k] = msg[k]
  elif msg is not None:
    out["msg"] = str(msg)[:200]
  for k in ("level", "filename", "lineno", "funcname"):
    if k in j and k not in out:
      out[k] = j[k]
  return out


def iter_file_records(path: Path, log_schema, result: FileReadResult | None = None) -> Iterator[dict]:
  """Yield records from one log file; never raises on a truncated/corrupt tail (recorded in `result`)."""
  _, seg = segment_of(path)
  data, err = read_log_bytes(path)
  if result is not None:
    result.truncated = err is not None
    result.error = err
  try:
    for evt in log_schema.Event.read_multiple_bytes(data):
      if result is not None:
        result.messages += 1
        w = evt.which()
        result.services[w] = result.services.get(w, 0) + 1
      rec = event_to_record(evt, seg)
      if rec is not None:
        yield rec
  except Exception as e:
    if result is not None:
      result.truncated = True
      result.error = (result.error + "; " if result.error else "") + f"capnp: {type(e).__name__}: {e}"[:300]


def load_records(paths: Iterable[Path], log_schema) -> tuple[list[dict], list[FileReadResult]]:
  """Decode every file, then sort all records by logMonoTime (stable)."""
  records, results = [], []
  for p in paths:
    res = FileReadResult(path=str(p), segment=segment_of(p)[1])
    records.extend(iter_file_records(Path(p), log_schema, res))
    results.append(res)
  records.sort(key=lambda r: r["t"])
  return records, results
