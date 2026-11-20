"""Offline paired lane-change MODEL phase sequence audit, without vehicle actuation.

Only completed and continuous off/pre -> starting -> finishing -> off
observations qualify, with independently time-matched Comma lateral ownership.
The recorded phases are model intent, NOT a physical lane-line crossing, lane
position measurement, or evidence of driver comfort or steering improvement.
Never export segment numbers, timestamps, route identifiers, CAN or locations.
"""
from bisect import bisect_right
from collections import Counter
from statistics import median

from .lane_phase_quality import _coalesce_equal_time

NS = 1_000_000_000
SOURCE = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
NEUTRAL = ("off", "preLaneChange")
START = "laneChangeStarting"
FINISH = "laneChangeFinishing"


def _ms(value):
    return round(value / 1_000_000, 3)


def analyze(records, *, max_model_gap_s=0.75, max_control_age_s=0.15):
    """Aggregate only verified *model-state sequence* durations by direction.

    Input: pinned-schema privacy-minimized drivingModelData and carControl
    dictionaries with segment, t, valid, state/direction or latActive fields.
    This is not a physical lane-change acceptance test.
    """
    if not (type(max_model_gap_s) in (int, float) and
            0 < max_model_gap_s <= 2 and
            type(max_control_age_s) in (int, float) and
            0 < max_control_age_s <= 1):
        raise ValueError("invalid timing gates")
    records = list(records)
    relevant = [r for r in records if r.get("service") in ("drivingModelData", "carControl")]
    if any(type(r.get("t")) is not int or r["t"] < 0 for r in relevant):
        return {"status": "invalid_recording_clock", "observedCompleteSequences": 0,
                "qualifiedOwnedSequences": 0, "noVehicleWrite": True}
    models = _coalesce_equal_time(
        (r for r in relevant if r["service"] == "drivingModelData"),
        ("segment", "valid", "meta.laneChangeState", "meta.laneChangeDirection"))
    controls = _coalesce_equal_time(
        (r for r in relevant if r["service"] == "carControl"),
        ("segment", "valid", "latActive"))
    control_times = [r["t"] for r in controls]
    max_model_gap_ns = round(max_model_gap_s * NS)
    max_control_age_ns = round(max_control_age_s * NS)
    counts = Counter()
    directions = Counter()
    starting_ms = []
    finishing_ms = []
    full_ms = []
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
        "qualifiedByDirection": dict(directions),
        "medianStartingPhaseSeconds": round(median(starting_ms) / 1000, 3) if starting_ms else None,
        "medianFinishingPhaseSeconds": round(median(finishing_ms) / 1000, 3) if finishing_ms else None,
        "medianCompleteSequenceSeconds": round(median(full_ms) / 1000, 3) if full_ms else None,
        "exclusions": {k: v for k, v in sorted(counts.items())
                       if k not in ("observedCompleteSequences", "qualifiedOwnedSequences")},
        "physicalLaneCrossingVerified": False,
        "physicalLaneCenterMeasured": False,
        "noVehicleWrite": True,
        "limitations": [
            "Model state transitions represent reported planner phases, not verified physical lane crossing.",
            "No lane centerline, lateral overshoot, road geometry or independent camera measurement.",
            "Only uninterrupted single-segment, same-direction active-lateral model sequences qualify.",
            "A valid entry, crossing-model phase and recenter-model phase can be observed without proving road comfort.",
            "This is not a controller replay or a validated calibration/release decision.",
        ],
    }
