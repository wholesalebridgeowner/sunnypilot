"""Privacy-preserving, pinned-log lane-change *model-phase* comfort diagnostics.

This is an offline observational companion to comfort_episodes.py. The logged
lane-change state does NOT identify a physical lane boundary crossing; do not
turn this into an actuator/controller decision or a road-use safety claim.
Only aggregate statistics are returned (no event times, locations, CAN or VIN).
"""
from __future__ import annotations

from collections import Counter
from math import isfinite

NS = 1_000_000_000
PHASES = ("laneChangeStarting", "laneChangeFinishing")


def _number(value):
  if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
    return float(value)
  return None


def _percentile(values, q):
  data = sorted(x for value in values if (x := _number(value)) is not None)
  if not data:
    return None
  at = (len(data) - 1) * q
  lo = int(at)
  hi = min(lo + 1, len(data) - 1)
  return round(data[lo] + (data[hi] - data[lo]) * (at - lo), 3)


def _coalesce_equal_time(rows, fields):
  """One observation per timestamp; contradictory evidence fails closed.

  Replay order is not a sensor-authority rule. Repeated identical samples
  must not create zero-length gaps; conflicting samples must never be
  resolved by whichever record happened to be encountered first. Compare
  only the fixed model/vehicle fields used by this observer, not private
  unrelated telemetry. Do not propagate private record fields in sentinels.
  """
  result = []
  for rec in sorted(rows, key=lambda r: r["t"]):
    if result and rec["t"] == result[-1]["t"]:
      old = result[-1]
      if old.get("_ambiguous_equal_time"):
        continue
      if any(type(old.get(k)) is not type(rec.get(k)) or
             old.get(k) != rec.get(k) for k in fields):
        same_segment = (type(old.get("segment")) is int and
                        type(rec.get("segment")) is int and
                        old["segment"] == rec["segment"])
        result[-1] = {
          "t": rec["t"],
          "segment": old["segment"] if same_segment else None,
          "valid": False,
          "_ambiguous_equal_time": True,
        }
      continue
    result.append(rec)
  return result


def analyze(records, *, max_model_gap_s=0.75, max_car_gap_s=0.3,
            min_steering_samples=3, min_speed_mps=5.0):
  """Summarize completed model starting/finishing intervals with explicit gates.

  A phase is complete only if a later valid model observation exits the state.
  A model gap/invalid observation censors the phase instead of inventing its
  end. Car-state samples must be valid, fresh throughout the phase, with known
  driver steering status; we reject driver-overridden periods. No interpolation.
  """
  if (not 0 < max_model_gap_s <= 5 or not 0 < max_car_gap_s <= 2 or
      isinstance(min_steering_samples, bool) or not isinstance(min_steering_samples, int) or
      min_steering_samples < 2 or not 0 <= min_speed_mps <= 50):
    raise ValueError("invalid offline quality gates")

  # A conflicting same-time model record censors an interrupted maneuver;
  # a conflicting same-time carState cannot supply verified kinematics.
  # Ignore bool timestamps (bool is a Python int subclass).
  models = _coalesce_equal_time(
    (r for r in records if r.get("service") == "drivingModelData"
     and type(r.get("t")) is int),
    ("segment", "valid", "meta.laneChangeState", "meta.laneChangeDirection"))
  car = _coalesce_equal_time(
    (r for r in records if r.get("service") == "carState"
     and type(r.get("t")) is int),
    ("segment", "valid", "steeringRateDeg", "steeringPressed", "vEgo",
     "canValid", "steerFaultTemporary", "steerFaultPermanent"))
  phase_counts = {phase: Counter() for phase in PHASES}
  peak_rates = {phase: [] for phase in PHASES}
  rate_derivatives = {phase: [] for phase in PHASES}
  median_speeds = {phase: [] for phase in PHASES}
  durations = {phase: [] for phase in PHASES}
  direction_counts = {phase: Counter() for phase in PHASES}
  # Model state and carState data have independent logging intervals.
  # Never use a future car sample to fill a missing past one.
  open_phase = None
  suppressed_state = None  # ambiguous direction: reject the whole model phase
  # Following a stream interruption, even a *different* active phase may have
  # begun inside the gap. Require a valid neutral/preparation observation first.
  needs_state_anchor = False
  unverified_active_state = None
  # The first available active model sample may be the tail of a phase whose
  # entry predates the recording. Require a verified neutral/preparation anchor.
  first_valid_model_seen = False
  previous_t = None
  previous_segment = None
  car_index = 0

  def complete(phase, end_t):
    nonlocal car_index
    name, start = phase["state"], phase["start"]
    phase_counts[name]["completedTransitions"] += 1
    duration = (end_t - start) / NS
    if not 0 < duration <= 15:
      phase_counts[name]["invalidDuration"] += 1
      return
    while car_index < len(car) and car[car_index]["t"] < start:
      car_index += 1
    selected = []
    i = car_index
    while i < len(car) and car[i]["t"] < end_t:
      selected.append(car[i])
      i += 1
    if len(selected) < min_steering_samples:
      phase_counts[name]["insufficientCarState"] += 1
      return
    # A timestamp match alone is not evidence that a carState observation
    # belongs to this model-phase segment. Fail closed on ambiguous overlap.
    if any(type(r.get("segment")) is not int or
           r["segment"] != phase["segment"] for r in selected):
      phase_counts[name]["carStateSegmentMismatch"] += 1
      return
    if any(r.get("valid") is not True for r in selected):
      phase_counts[name]["invalidCarState"] += 1
      return
    # A model phase with invalid/unknown vehicle CAN or EPS steering faults
    # is not a verified comfort sample, even when steering-rate data exists.
    # Require the exact booleans (not truthy integers or absent fields).
    if any(r.get("canValid") is not True for r in selected):
      phase_counts[name]["invalidOrUnknownCan"] += 1
      return
    if any(r.get("steerFaultTemporary") is not False or
           r.get("steerFaultPermanent") is not False for r in selected):
      phase_counts[name]["epsFaultOrUnknown"] += 1
      return
    if any(r.get("steeringPressed") is not False for r in selected):
      phase_counts[name]["driverOverrideOrUnknown"] += 1
      return
    if (selected[0]["t"] - start) / NS > max_car_gap_s or (
        end_t - selected[-1]["t"]) / NS > max_car_gap_s:
      phase_counts[name]["incompleteCarCoverage"] += 1
      return
    if any(not 0 < (b["t"] - a["t"]) / NS <= max_car_gap_s
           for a, b in zip(selected, selected[1:])):
      phase_counts[name]["carStateGap"] += 1
      return
    rates = [_number(r.get("steeringRateDeg")) for r in selected]
    speeds = [_number(r.get("vEgo")) for r in selected]
    if any(v is None for v in rates + speeds):
      phase_counts[name]["missingKinematics"] += 1
      return
    if min(speeds) < min_speed_mps:
      phase_counts[name]["lowSpeedExcluded"] += 1
      return
    deriv = [abs((b-a) / ((right["t"] - left["t"]) / NS))
             for a, b, left, right in zip(rates, rates[1:], selected, selected[1:])]
    phase_counts[name]["qualifiedWindows"] += 1
    peak_rates[name].append(max(abs(v) for v in rates))
    rate_derivatives[name].append(_percentile(deriv, .9))
    median_speeds[name].append(_percentile(speeds, .5))
    durations[name].append(duration)
    direction_counts[name][phase["direction"]] += 1

  for rec in models:
    t = rec["t"]
    seg = rec.get("segment")
    segment = seg if type(seg) is int and seg >= 0 else None
    invalid_segment = segment is None
    boundary = previous_segment is not None and segment != previous_segment
    invalid_or_gap = (rec.get("valid") is not True or previous_t is not None and
                      not 0 < (t - previous_t) / NS <= max_model_gap_s)
    if invalid_segment or boundary or invalid_or_gap:
      # Once an observed phase is interrupted, its reappearance is NOT a
      # verified new entry. The same phase can continue across missing model
      # frames, invalid observations or a recording boundary. Hold that state
      # censored until a subsequent valid, different model state is observed.
      # Preserve an existing suppression across additional invalid frames.
      interrupted_state = open_phase["state"] if open_phase else suppressed_state
      if open_phase:
        reason = ("censoredBySegmentBoundaryOrUnverified"
                  if invalid_segment or boundary else "censoredByGapOrInvalidModel")
        phase_counts[open_phase["state"]][reason] += 1
      open_phase = None
      suppressed_state = interrupted_state
      needs_state_anchor = True
      unverified_active_state = None
      previous_t = None
      previous_segment = None
      if rec.get("valid") is not True or invalid_segment:
        continue
    previous_t = t
    previous_segment = segment
    state = str(rec.get("meta.laneChangeState") or "")
    direction = str(rec.get("meta.laneChangeDirection") or "unknown")
    if not first_valid_model_seen:
      first_valid_model_seen = True
      if state not in ("off", "preLaneChange"):
        needs_state_anchor = True
        if state in PHASES:
          phase_counts[state]["censoredAtStreamStart"] += 1
          unverified_active_state = state
    if needs_state_anchor:
      # An active state first observed after missing/invalid model chronology
      # cannot prove its entry, even if different from the interrupted phase.
      # Do not mistake unknown states for a known non-maneuver anchor.
      if state not in ("off", "preLaneChange"):
        if (state in PHASES and state != suppressed_state and
            state != unverified_active_state):
          phase_counts[state]["censoredByUnverifiedEntry"] += 1
        if state in PHASES:
          unverified_active_state = state
        continue
      needs_state_anchor = False
      unverified_active_state = None
      suppressed_state = None
    if suppressed_state == state:
      continue
    suppressed_state = None
    if open_phase and state == open_phase["state"] and direction != open_phase["direction"]:
      # A direction reversal (or missing direction) within the same model
      # state is not a second maneuver. Censor the entire phase until exit.
      phase_counts[state]["censoredByDirectionChangeOrUnknown"] += 1
      open_phase = None
      suppressed_state = state
      continue
    if open_phase and state != open_phase["state"]:
      complete(open_phase, t)
      open_phase = None
    if state in PHASES and open_phase is None:
      if direction not in ("left", "right"):
        phase_counts[state]["censoredByDirectionChangeOrUnknown"] += 1
        suppressed_state = state
      else:
        open_phase = {"state": state, "start": t, "segment": segment,
                      "direction": direction}

  if open_phase:
    phase_counts[open_phase["state"]]["censoredAtEnd"] += 1

  groups = {}
  for phase in PHASES:
    groups[phase] = {
      "counts": dict(phase_counts[phase]),
      "qualifiedByDirection": dict(direction_counts[phase]),
      "medianDurationSeconds": _percentile(durations[phase], .5),
      "p90PeakAbsSteeringRateDegPerSecond": _percentile(peak_rates[phase], .9),
      "p90SteeringRateDerivativeDegPerSecondSquared": _percentile(rate_derivatives[phase], .9),
      "medianQualifiedSpeedMps": _percentile(median_speeds[phase], .5),
    }
  return {
    "status": "ok" if models else "no_model_data",
    "modelSamples": len(models),
    "phaseWindows": groups,
    "physicalLaneBoundaryCrossing": "not_observable_from_logged_model_state",
    "interpretation": [
      "laneChangeStarting is a model entry/crossing-phase proxy, not evidence of an actual lane boundary crossing.",
      "laneChangeFinishing is a model recenter-phase proxy, not proof of settled vehicle position.",
      "Steering-rate derivative is steering-wheel angular acceleration, NOT chassis lateral jerk.",
      "Only complete, same-segment, healthy-CAN, EPS-fault-free and driver-not-steering windows enter comfort percentiles.",
      "Same-timestamp identical samples count once; conflicting samples censor rather than depend on input order.",
      "Unverified model segments, segment transitions, or mixed-segment carState samples censor a window.",
      "Unknown validity and direction changes within one model phase censor the entire phase until exit.",
      "After model gaps, invalid records or segment changes, a valid off/preLaneChange observation must anchor new phase entries.",
      "A phase already active at the first valid model observation is left-censored until an observed neutral/preparation anchor.",
      "This is offline observation only; do not tune or deploy a controller from these metrics alone.",
    ],
  }
