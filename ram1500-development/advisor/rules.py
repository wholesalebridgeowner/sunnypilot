"""Drive Advisor suggestion rules. First version: simple thresholds, tuned in
rollout step 1 against the owner's stored drives."""
from __future__ import annotations

MIN_ENGAGED_MINUTES = 5.0
LOOKBACK_MINUTES = 10
WEAVE_HIGH_PER_MIN = 6.0
CURVE_ERR_HIGH = 0.25
KEEP_MIN_IMPROVEMENT = 0.15
COMPARABLE_SPEED_MS = 5.0
NNLC_WARNINGS_PER_ENGAGED_HOUR = 2.0
CLOSE_LEAD_M = 15.0
CLOSE_HARD_BRAKES_FOR_ADVICE = 2
MAX_LIVE_TRIALS_PER_DRIVE = 2
TARGETS = {
    "weaving_on_straight_road": ("weavePerStraightMinute", "straightMinutes"),
    "late_in_curves": ("curveRelError", "curveSamples"),
}
BRAKING_ADVICE = ("Several hard stops happened with a car close ahead. A longer following-distance "
                  "setting on the steering wheel gives the truck's cruise control more room.")


def _weighted(rows: list, key: str, weight_key: str) -> float | None:
    num = den = 0.0
    for row in rows:
        value, weight = row.get(key), row.get(weight_key)
        if value is None or not weight:
            continue
        num += value * weight
        den += weight
    return num / den if den else None


def _engaged(rows: list) -> float:
    return sum(row.get("engagedMinutes") or 0.0 for row in rows)


def _warnings_per_engaged_minute(rows: list) -> float | None:
    engaged = _engaged(rows)
    return sum(row.get("steerWarningEpisodes", 0) for row in rows) / engaged if engaged else None


def live_suggestion(summaries: list, *, trials_this_drive: int, trial_active: bool) -> dict | None:
    if trial_active or trials_this_drive >= MAX_LIVE_TRIALS_PER_DRIVE:
        return None
    recent = summaries[-LOOKBACK_MINUTES:]
    if _engaged(recent) < MIN_ENGAGED_MINUTES:
        return None
    weave = _weighted(recent, *TARGETS["weaving_on_straight_road"])
    curve = _weighted(recent, *TARGETS["late_in_curves"])
    weave_high = weave is not None and weave >= WEAVE_HIGH_PER_MIN
    curve_high = curve is not None and curve >= CURVE_ERR_HIGH
    if weave_high == curve_high:
        return None
    if weave_high:
        return {"kind": "live_delay_trial", "step": -1, "reason": "weaving_on_straight_road",
                "metric": round(weave, 2)}
    return {"kind": "live_delay_trial", "step": 1, "reason": "late_in_curves", "metric": round(curve, 3)}


def _judge_trial(trial: dict, trial_rows: list, baseline_rows: list) -> dict:
    reason = trial.get("reason")
    if reason not in TARGETS:
        return {"reason": reason, "keep": False, "why": "unknown_trial_reason"}
    if _engaged(trial_rows) < MIN_ENGAGED_MINUTES:
        return {"reason": reason, "keep": False, "why": "not_enough_trial_driving"}
    speeds = [r["speedMeanMs"] for r in trial_rows if r.get("speedMeanMs") is not None]
    mean_speed = sum(speeds) / len(speeds) if speeds else None
    comparable = [r for r in baseline_rows if r.get("engagedMinutes") and mean_speed is not None
                  and r.get("speedMeanMs") is not None
                  and abs(r["speedMeanMs"] - mean_speed) <= COMPARABLE_SPEED_MS]
    key, weight_key = TARGETS[reason]
    base, tried = _weighted(comparable, key, weight_key), _weighted(trial_rows, key, weight_key)
    if not base or tried is None:
        return {"reason": reason, "keep": False, "why": "no_comparable_driving"}
    improvement = (base - tried) / base
    base_warn = _warnings_per_engaged_minute(comparable) or 0.0
    trial_warn = _warnings_per_engaged_minute(trial_rows) or 0.0
    keep = improvement >= KEEP_MIN_IMPROVEMENT and trial_warn <= base_warn + 1e-9
    return {"reason": reason, "keep": keep, "improvement": round(improvement, 3),
            "why": "improved" if keep else ("more_warnings" if trial_warn > base_warn else "not_enough_improvement")}


def nightly_recommendation(drives: list, *, nnlc_on: bool) -> dict:
    rows = [r for d in drives for r in d["summaries"]]
    engaged = _engaged(rows)
    warnings = sum(r.get("steerWarningEpisodes", 0) for r in rows)
    brakes = [b for r in rows for b in r.get("hardBrakes", [])]
    close = [b for b in brakes if b.get("leadPresent") and b.get("leadDistanceM") is not None
             and b["leadDistanceM"] <= CLOSE_LEAD_M]
    report = {
        "drives": len(drives),
        "engagedMinutes": round(engaged, 1),
        "steerWarnings": warnings,
        "steerWarningsPerEngagedHour": round(warnings / engaged * 60, 2) if engaged else None,
        "laneChanges": sum(r.get("laneChanges", 0) for r in rows),
        "overrideEpisodes": sum(r.get("overrideEpisodes", 0) for r in rows),
        "hardBrakes": len(brakes),
        "closeLeadHardBrakes": len(close),
        "brakingAdvice": BRAKING_ADVICE if len(close) >= CLOSE_HARD_BRAKES_FOR_ADVICE else None,
        "trials": [],
        "suggestion": None,
    }
    baseline = [r for d in drives if not d.get("trial") for r in d["summaries"]]
    for drive in drives:
        if not drive.get("trial"):
            continue
        verdict = _judge_trial(drive["trial"], drive["summaries"], baseline)
        report["trials"].append(verdict)
        if verdict["keep"] and report["suggestion"] is None:
            report["suggestion"] = {"kind": "keep_trial", "change": drive["trial"]["change"],
                                    "reason": f"{verdict['reason']}_improved"}
    rate = report["steerWarningsPerEngagedHour"]
    if report["suggestion"] is None and not nnlc_on and rate is not None and rate >= NNLC_WARNINGS_PER_ENGAGED_HOUR:
        report["suggestion"] = {"kind": "nightly_setting", "change": {"NeuralNetworkLateralControl": True},
                                "reason": "frequent_steering_warnings"}
    return report
