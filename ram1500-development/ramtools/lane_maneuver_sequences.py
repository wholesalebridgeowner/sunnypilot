"""Offline paired lane-change MODEL phase sequence audit, without vehicle actuation.

Only completed and continuous off/pre -> starting -> finishing -> off
observations qualify, with independently time-matched Comma lateral ownership.
The recorded phases are model intent, NOT a physical lane-line crossing, lane
position measurement, or evidence of driver comfort or steering improvement.
Never export segment numbers, timestamps, route identifiers, CAN or locations.
"""
from bisect import bisect_right
from collections import Counter
from math import isfinite
from statistics import median

from .lane_phase_quality import _coalesce_equal_time, _percentile

NS = 1_000_000_000
SOURCE = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
NEUTRAL = ("off", "preLaneChange")
START = "laneChangeStarting"
FINISH = "laneChangeFinishing"



def _joined_steering_envelope(cars, start, finish, end, segment, gap_ns, min_speed):
    """Qualified observed steering-wheel motion across BOTH model phases.

    Records must be source-pinned by the private caller. A model lane state is
    not proof of physical lane crossing, lateral acceleration or recentering.
    The internal result contains only derived scalar motion measures and is
    never a segment/time/position export.
    """
    rows = [r for r in cars if start <= r["t"] < end]
    entry = [r for r in rows if r["t"] < finish]
    finishing = [r for r in rows if r["t"] >= finish]
    if len(entry) < 2 or len(finishing) < 2:
        return None, "kinematicInsufficientTwoPhaseSamples"
    if (entry[0]["t"] - start > gap_ns or finish - entry[-1]["t"] > gap_ns or
        finishing[0]["t"] - finish > gap_ns or end - finishing[-1]["t"] > gap_ns or
        any(not 0 < b["t"] - a["t"] <= gap_ns for a, b in zip(rows, rows[1:]))):
        return None, "kinematicUnverifiedCoverage"
    if any(type(r.get("segment")) is not int or r["segment"] != segment or
           r.get("valid") is not True or r.get("canValid") is not True
           for r in rows):
        return None, "kinematicSegmentOrCanInvalid"
    if any(r.get("steerFaultTemporary") is not False or
           r.get("steerFaultPermanent") is not False or
           r.get("steeringPressed") is not False for r in rows):
        return None, "kinematicInterventionOrEpsFault"
    rates = []
    for r in rows:
        speed, rate = r.get("vEgo"), r.get("steeringRateDeg")
        if (type(speed) not in (float, int) or not isfinite(speed) or
            type(rate) not in (float, int) or not isfinite(rate)):
            return None, "kinematicMissingOrNonfinite"
        if speed < min_speed:
            return None, "kinematicLowSpeed"
        rates.append(float(rate))
    # The starting->finishing boundary belongs to the SAME observed maneuver.
    # Excluding this one adjacent pair previously hid abrupt steering-rate
    # changes at the phase transition. Coverage and finite-data gates above
    # apply to this pair just as they do to every in-phase pair.
    derivatives = [
        abs((b["steeringRateDeg"] - a["steeringRateDeg"]) /
            ((b["t"] - a["t"]) / NS))
        for a, b in zip(rows, rows[1:])
    ]
    last_start = entry[-1]
    first_finish = finishing[0]
    boundary_slope = abs(
        (first_finish["steeringRateDeg"] - last_start["steeringRateDeg"]) /
        ((first_finish["t"] - last_start["t"]) / NS))
    return (max(abs(v) for v in rates), max(derivatives), boundary_slope), None



def _post_exit_wheel_observation(cars, model_rows, exit_t, end_t, segment,
                                 car_gap_ns, model_gap_ns, min_speed):
    """Wheel-rate residual after reported model phase exit, NOT lane-center error.

    Fail closed unless a same-segment, continuous neutral MODEL interval and
    independently healthy carState samples cover the entire observation window.
    The calling code separately verifies fresh continuous Comma lateral ownership.
    """
    model = [r for r in model_rows if exit_t <= r["t"] <= end_t]
    if (not model or model[0]["t"] != exit_t or
        end_t - model[-1]["t"] > model_gap_ns or
        any(not 0 < b["t"] - a["t"] <= model_gap_ns
            for a, b in zip(model, model[1:]))):
        return None, "postExitModelCoverageUnverified"
    if any(r.get("valid") is not True or
           type(r.get("segment")) is not int or r["segment"] != segment or
           r.get("meta.laneChangeState") not in NEUTRAL for r in model):
        return None, "postExitModelNotNeutral"
    rows = [r for r in cars if exit_t <= r["t"] < end_t]
    if (len(rows) < 4 or rows[0]["t"] - exit_t > car_gap_ns or
        end_t - rows[-1]["t"] > car_gap_ns or
        any(not 0 < b["t"] - a["t"] <= car_gap_ns
            for a, b in zip(rows, rows[1:]))):
        return None, "postExitCarCoverageUnverified"
    if any(type(r.get("segment")) is not int or r["segment"] != segment or
           r.get("valid") is not True or r.get("canValid") is not True or
           r.get("steerFaultTemporary") is not False or
           r.get("steerFaultPermanent") is not False or
           r.get("steeringPressed") is not False for r in rows):
        return None, "postExitVehicleInvalidOrIntervention"
    rates = []
    for r in rows:
        speed, rate = r.get("vEgo"), r.get("steeringRateDeg")
        if (type(speed) not in (int, float) or not isfinite(speed) or
            type(rate) not in (int, float) or not isfinite(rate)):
            return None, "postExitKinematicsUnknown"
        if speed < min_speed:
            return None, "postExitLowSpeed"
        rates.append(abs(float(rate)))
    # Last quarter of this observational window, not a calibration threshold.
    tail = [rate for r, rate in zip(rows, rates)
            if r["t"] >= exit_t + (end_t - exit_t) * 3 // 4]
    if len(tail) < 2:
        return None, "postExitTailCoverageUnverified"
    return (max(rates), median(tail)), None


def _ms(value):
    return round(value / 1_000_000, 3)


def analyze(records, *, max_model_gap_s=0.75, max_control_age_s=0.15,
            max_car_gap_s=0.15, min_speed_mps=5.0, post_exit_seconds=1.0):
    """Aggregate only verified *model-state sequence* durations by direction.

    Input: pinned-schema privacy-minimized drivingModelData and carControl
    dictionaries with segment, t, valid, state/direction or latActive fields.
    This is not a physical lane-change acceptance test.
    """
    if not (type(max_model_gap_s) in (int, float) and
            0 < max_model_gap_s <= 2 and
            type(max_control_age_s) in (int, float) and
            0 < max_control_age_s <= 1 and
            type(max_car_gap_s) in (int, float) and
            0 < max_car_gap_s <= 0.5 and
            type(min_speed_mps) in (int, float) and
            0 <= min_speed_mps <= 50 and
            type(post_exit_seconds) in (float, int) and
            0 < post_exit_seconds <= 3):
        raise ValueError("invalid timing gates")
    records = list(records)
    relevant = [r for r in records if r.get("service") in ("drivingModelData", "carControl", "carState")]
    if any(type(r.get("t")) is not int or r["t"] < 0 for r in relevant):
        return {"status": "invalid_recording_clock", "observedCompleteSequences": 0,
                "qualifiedOwnedSequences": 0, "noVehicleWrite": True}
    models = _coalesce_equal_time(
        (r for r in relevant if r["service"] == "drivingModelData"),
        ("segment", "valid", "meta.laneChangeState", "meta.laneChangeDirection"))
    controls = _coalesce_equal_time(
        (r for r in relevant if r["service"] == "carControl"),
        ("segment", "valid", "latActive"))
    cars = _coalesce_equal_time(
        (r for r in relevant if r["service"] == "carState"),
        ("segment", "valid", "vEgo", "steeringRateDeg", "steeringPressed",
         "canValid", "steerFaultTemporary", "steerFaultPermanent"))
    control_times = [r["t"] for r in controls]
    max_model_gap_ns = round(max_model_gap_s * NS)
    max_control_age_ns = round(max_control_age_s * NS)
    max_car_gap_ns = round(max_car_gap_s * NS)
    counts = Counter()
    directions = Counter()
    starting_ms = []
    finishing_ms = []
    full_ms = []
    joined_rate_peaks = []
    joined_rate_derivative_peaks = []
    phase_transition_slope_peaks = []
    post_exit_peaks = []
    post_exit_tail_medians = []
    previous = None
    anchored = False
    pending = None
    unanchored = None

    def owned(start_t, finish_t, end_t, segment):
        """Past-only on three edges and continuous active updates in-between."""
        checks = (start_t, finish_t, end_t)
        selected_indices = []
        for t in checks:
            idx = bisect_right(control_times, t) - 1
            if idx < 0 or not 0 <= t - control_times[idx] <= max_control_age_ns:
                return False
            row = controls[idx]
            if (row.get("valid") is not True or
                type(row.get("segment")) is not int or
                row["segment"] != segment or row.get("latActive") is not True):
                return False
            selected_indices.append(idx)
        first, last = selected_indices[0], selected_indices[-1]
        window = controls[first:last + 1]
        if any(r.get("valid") is not True or r.get("segment") != segment or
               r.get("latActive") is not True for r in window):
            return False
        if any(not 0 < b["t"] - a["t"] <= max_control_age_ns
               for a, b in zip(window, window[1:])):
            return False
        return True

    for row in models:
        t = row["t"]
        segment = row.get("segment")
        valid = row.get("valid") is True and type(segment) is int and segment >= 0
        boundary = previous is not None and segment != previous[1]
        gap = previous is not None and not 0 < t - previous[0] <= max_model_gap_ns
        if not valid or boundary or gap:
            if pending is not None:
                counts["censoredByModelGapSegmentOrInvalid"] += 1
            pending = None
            anchored = False
            unanchored = None
            previous = None
            if not valid:
                counts["invalidModelSamples"] += 1
                continue
        previous = (t, segment)
        state = row.get("meta.laneChangeState")
        direction = row.get("meta.laneChangeDirection")
        if state in NEUTRAL:
            if pending is not None:
                if state == "off" and pending["finish"] is not None:
                    counts["observedCompleteSequences"] += 1
                    start_t = pending["start"]
                    finish_t = pending["finish"]
                    start_duration = finish_t - start_t
                    finish_duration = t - finish_t
                    if (pending["startSamples"] < 2 or pending["finishSamples"] < 2 or
                        not 0 < start_duration <= 15 * NS or
                        not 0 < finish_duration <= 15 * NS):
                        counts["tooShortOrUnverifiedPhaseExposure"] += 1
                    elif not owned(start_t, finish_t, t, segment):
                        counts["lateralOwnershipUnverified"] += 1
                    else:
                        counts["qualifiedOwnedSequences"] += 1
                        directions[pending["direction"]] += 1
                        starting_ms.append(start_duration / 1_000_000)
                        finishing_ms.append(finish_duration / 1_000_000)
                        full_ms.append((t - start_t) / 1_000_000)
                        envelope, reason = _joined_steering_envelope(
                            cars, start_t, finish_t, t, segment,
                            max_car_gap_ns, min_speed_mps)
                        if reason:
                            counts[reason] += 1
                        else:
                            counts["qualifiedHealthyKinematicSequences"] += 1
                            joined_rate_peaks.append(envelope[0])
                            joined_rate_derivative_peaks.append(envelope[1])
                            phase_transition_slope_peaks.append(envelope[2])
                        # Separate post-exit wheel-rate observation. This is
                        # never a physical recentering or overshoot measurement.
                        post_end = t + round(post_exit_seconds * NS)
                        if not owned(t, t, post_end, segment):
                            counts["postExitLateralOwnershipUnverified"] += 1
                        else:
                            post, post_reason = _post_exit_wheel_observation(
                                cars, models, t, post_end, segment,
                                max_car_gap_ns, max_model_gap_ns, min_speed_mps)
                            if post_reason:
                                counts[post_reason] += 1
                            else:
                                counts["qualifiedPostExitWheelObservations"] += 1
                                post_exit_peaks.append(post[0])
                                post_exit_tail_medians.append(post[1])
                else:
                    counts["incompleteModelSequence"] += 1
            pending = None
            anchored = True
            unanchored = None
            continue
        if state not in (START, FINISH) or direction not in ("left", "right"):
            if pending is not None:
                counts["unknownStateOrDirection"] += 1
            pending = None
            anchored = False
            unanchored = None
            continue
        if pending is None:
            if state == START and anchored:
                pending = {"start": t, "finish": None, "direction": direction,
                           "startSamples": 1, "finishSamples": 0}
                anchored = False
            elif unanchored != state:
                counts["unanchoredActiveModelState"] += 1
                unanchored = state
            continue
        if direction != pending["direction"]:
            counts["directionChangedInsideSequence"] += 1
            pending = None
            anchored = False
        elif state == START and pending["finish"] is None:
            pending["startSamples"] += 1
        elif state == FINISH:
            if pending["finish"] is None:
                pending["finish"] = t
            pending["finishSamples"] += 1
        else:
            counts["regressedFromFinishingToStarting"] += 1
            pending = None
            anchored = False
    if pending is not None:
        counts["censoredAtRecordingEnd"] += 1
    return {
        "status": "observational_model_sequence_only" if models else "no_model_observations",
        "sourceCommit": SOURCE,
        "modelSamples": len(models),
        "observedCompleteSequences": counts["observedCompleteSequences"],
        "qualifiedOwnedSequences": counts["qualifiedOwnedSequences"],
        "qualifiedHealthyKinematicSequences": counts["qualifiedHealthyKinematicSequences"],
        "qualifiedPostExitWheelObservations": counts["qualifiedPostExitWheelObservations"],
        "postExitWindowSeconds": post_exit_seconds,
        "p90PeakPostExitAbsSteeringRateDegPerSecond": _percentile(post_exit_peaks, .9),
        "medianPostExitTailAbsSteeringRateDegPerSecond": (
            round(median(post_exit_tail_medians), 3) if post_exit_tail_medians else None),
        "p90PeakAbsSteeringRateDegPerSecond": _percentile(joined_rate_peaks, .9),
        "p90PeakWheelRateDerivativeDegPerSecondSquared": _percentile(
            joined_rate_derivative_peaks, .9),
        "p90PeakPhaseTransitionWheelRateDerivativeDegPerSecondSquared": _percentile(
            phase_transition_slope_peaks, .9),
        "wheelRateDerivativeDefinition": "phase_boundary_inclusive_v2",
        "qualifiedByDirection": dict(directions),
        "medianStartingPhaseSeconds": round(median(starting_ms) / 1000, 3) if starting_ms else None,
        "medianFinishingPhaseSeconds": round(median(finishing_ms) / 1000, 3) if finishing_ms else None,
        "medianCompleteSequenceSeconds": round(median(full_ms) / 1000, 3) if full_ms else None,
        "exclusions": {k: v for k, v in sorted(counts.items())
                       if k not in ("observedCompleteSequences", "qualifiedOwnedSequences",
                                    "qualifiedHealthyKinematicSequences",
                                    "qualifiedPostExitWheelObservations")},
        "physicalLaneCrossingVerified": False,
        "physicalLaneCenterMeasured": False,
        "noVehicleWrite": True,
        "limitations": [
            "Model state transitions represent reported planner phases, not verified physical lane crossing.",
            "No lane centerline, lateral overshoot, road geometry or independent camera measurement.",
            "Only uninterrupted single-segment, same-direction active-lateral model sequences qualify.",
            "A valid entry, crossing-model phase and recenter-model phase can be observed without proving road comfort.",
            "Steering-rate derivative measures wheel angular acceleration, NOT chassis lateral jerk.",
            "Wheel-rate derivative v2 includes the adjacent starting-to-finishing phase boundary; older phase-internal-only statistics are not comparable.",
            "A post-exit neutral model interval measures residual wheel motion, NOT physical lane recentering or positional settle-out.",
            "Post-exit rates require continued Comma lateral activation and continuous same-segment model and healthy vehicle evidence.",
            "Healthy two-phase kinematics exclude invalid CAN, EPS faults, steering override, low speed, gaps and unknown data.",
            "A joined steering envelope is observational and does NOT establish physical lane crossing or recentering.",
            "This is not a controller replay or a validated calibration/release decision.",
        ],
    }
