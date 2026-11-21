"""Build the measure-only nightly report from stored v1 and v2 drive records."""
from __future__ import annotations

import math
import statistics
from collections import Counter

HARD_BRAKE_DROP_MS = 3.0       # m/s lost within one second
CAUSE_WORDS = {"brake": "brake", "gas": "gas", "cruise_off": "cruise off", "gear": "gear", "fault": "fault",
               "blinker": "blinker", "driver": "driver steering", "none": "no cause found"}
WHO_WORDS = {"driver": "driver", "cruise": "truck's cruise", "other": "other"}
MEASURE_NAMES = {"steering", "limit", "brake", "laneChange", "delay", "steeringLoss", "lane", "swings", "ay", "curve", "lag"}


def _num(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 1e9 and math.isfinite(value):
        return value
    return None


def _count(value):
    return value if type(value) is int and 0 <= value < 1e9 else None


def _duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "under 1 min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def _drives(n):
    return f"{n} drive{'' if n == 1 else 's'}"


def _summaries(drive):
    """An indexed v2 window contributes once. V1 windows lack an index."""
    rows, seen = [], set()
    for row in drive.get("summaries") or []:
        if not isinstance(row, dict):
            continue
        w = _count(row.get("w"))
        if w is not None and w in seen:
            continue
        seen.add(w)
        rows.append(row)
    return rows


def _coverage(drive, rows):
    end = drive.get("end") or {}
    totals = end.get("totals") or {}
    expected = _count(end.get("windows"))
    indices = {_count(row.get("w")) for row in rows}
    gaps = set()
    if expected is None:
        gaps.add("window coverage unverified")
    elif (len(rows) != expected or len(indices) != expected or None in indices or
          (indices and (min(indices) != 0 or max(indices) != expected - 1))):
        gaps.add("missing summary windows")
    elif not rows:
        gaps.add("no observed windows")
    unobserved = [_num(totals.get("unobservedS"))] + [_num(r.get("unobservedS")) for r in rows]
    if any(v is not None and v > 0 for v in unobserved):
        gaps.add("unobserved time")
    elif expected is not None and (None in unobserved or any(v < 0 for v in unobserved)):
        gaps.add("observation time unverified")
    durations = [_num(r.get("windowS")) for r in rows]
    total_s = _num(totals.get("windowS"))
    if expected is not None and (total_s is None or total_s < 0 or None in durations or any(t < 0 for t in durations)):
        gaps.add("observation time unverified")
    elif expected is not None and len(rows) == expected and abs(sum(durations) - total_s) > (len(rows) + 1) * 0.05 + 1e-6:
        # The pipeline rounds both the individual window times and their total to 0.1 s.
        gaps.add("window times disagree")
    if end.get("listening") is False:
        gaps.add("listening off")
    elif expected is not None and end.get("listening") is not True:
        gaps.add("listening status unverified")
    if (_count(end.get("truncatedLaneChanges")) or 0) or any((_count(r.get("lcTruncated")) or 0) for r in rows):
        gaps.add("truncated lane changes")
    failed = set()
    for health in [end] + [r.get("health") or {} for r in rows]:
        if not isinstance(health, dict):
            continue
        if (_count(health.get("dropped")) or 0) > 0:
            gaps.add("dropped records")
        failed_names = health.get("failed") or []
        if isinstance(failed_names, list):
            for name in failed_names:
                failed.add(name if isinstance(name, str) and name in MEASURE_NAMES else "other")
        else:
            failed.add("other")
    if failed:
        gaps.add("measures stopped: " + ", ".join(sorted(failed)))
    return gaps


def _breakdown(records, field, words):
    labels = Counter()
    for row in records:
        token = row.get(field)
        labels[words.get(token, "other") if isinstance(token, str) else "other"] += 1
    return ", ".join(f"{name} {count}" for name, count in sorted(labels.items(), key=lambda x: (-x[1], x[0])))


def report_lines(drives: list, closing_line: str) -> list:
    n = len(drives)
    totals = [(d.get("end") or {}).get("totals") or {} for d in drives]
    window = [_num(t.get("windowS")) for t in totals]
    steering = [_num(t.get("steeringS")) for t in totals]
    if window and None not in window:
        line = f"{_drives(n)}, {_duration(sum(window))}"
        if None not in steering:
            line += f", steering on {_duration(sum(steering))}"
        lines = [line + "."]
    else:
        lines = [f"{_drives(n)} recorded."]

    bsm = [(d.get("ready") or {}).get("blindSpot") for d in drives]
    if any(type(b) is bool for b in bsm):
        off, unknown = sum(b is False for b in bsm), sum(type(b) is not bool for b in bsm)
        if off:
            line = f"Blind spot: off on {off} of {_drives(n)}"
            lines.append(line + (f"; not reported for {unknown}." if unknown else "."))
        elif unknown:
            lines.append(f"Blind spot: on; not reported for {unknown} of {_drives(n)}.")
        else:
            lines.append("Blind spot: on for every drive." if n > 1 else "Blind spot: on.")

    losses, changes, brakes = [], [], []
    warnings, old_changes, old_brakes = 0, 0, 0
    legacy_coverage = Counter()
    ended_partial, unfinished = 0, 0
    reasons = set()
    measured = Counter()
    for d in drives:
        rows = _summaries(d)
        gaps = _coverage(d, rows)
        ended = d.get("ended") is True or isinstance(d.get("end"), dict)
        events = {}
        for e in d.get("events") or []:
            if isinstance(e, dict) and isinstance(e.get("data"), dict) and isinstance(e.get("type"), str):
                events.setdefault(e["type"], []).append(e["data"])
        lane, brake = events.get("lane_change", []), events.get("brake", [])
        losses.extend(events.get("steering_loss", []))
        changes.extend(lane)
        brakes.extend(b for b in brake if (_num(b.get("peakDrop1s")) or 0.0) >= HARD_BRAKE_DROP_MS)
        # A summary can contain both keys. Its v2 warning count takes precedence.
        warn = [_count(r.get("warnEvents", r.get("steerWarningEpisodes"))) for r in rows]
        warnings += sum(value for value in warn if value is not None)
        legacy_lane = [_count(r.get("laneChanges")) for r in rows]
        legacy_brake = [len(r["hardBrakes"]) if isinstance(r.get("hardBrakes"), list) else None for r in rows]
        if rows and all("w" not in r and "warnEvents" not in r for r in rows):
            legacy_coverage["warnings"] += None not in warn
            legacy_coverage["lane"] += None not in legacy_lane
            legacy_coverage["brake"] += None not in legacy_brake
        if not lane:
            old_changes += sum(value for value in legacy_lane if value is not None)
        if not brake:
            old_brakes += sum(value for value in legacy_brake if value is not None)
        # The v2 window counters can positively identify missing detailed records.
        if sum((_count(r.get("lcCompleted")) or 0) + (_count(r.get("lcCancelled")) or 0) for r in rows) > len(lane):
            gaps.add("missing lane-change records")
        if sum((_count(r.get("brakeEpisodes")) or 0) for r in rows) > len(brake):
            gaps.add("missing brake records")
        if ended and not gaps:
            active = ((d.get("start") or {}).get("agent") or {}).get("measures") or []
            measured["warnings"] += bool(warn) and None not in warn
            measured["loss"] += "steeringLoss" in active
            measured["lane"] += ("laneChange" in active or all(_count(r.get("lcCompleted")) is not None for r in rows)
                                 or None not in legacy_lane)
            measured["brake"] += ("brake" in active or all(_count(r.get("brakeEpisodes")) is not None for r in rows)
                                  or None not in legacy_brake)
        if not ended:
            unfinished += 1
        elif gaps:
            ended_partial += 1
        reasons.update(gaps)

    if warnings or (n and measured["warnings"] == n):
        lines.append(f"Steering-limit warnings: {warnings}.")
    elif n and legacy_coverage["warnings"] == n:
        lines.append("Steering-limit warnings: 0 in received older summaries.")
    if losses:
        lines.append(f"Steering turned off while moving: {len(losses)} ({_breakdown(losses, 'cause', CAUSE_WORDS)}).")
    elif n and measured["loss"] == n:
        lines.append("Steering turned off while moving: none.")
    if changes or old_changes:
        details = []
        if changes:
            done = sum(c.get("outcome") == "completed" for c in changes)
            cancelled = sum(c.get("outcome") == "cancelled" for c in changes)
            details += [f"{done} completed", f"{cancelled} cancelled"]
        if old_changes:
            details.append(f"{old_changes} from older summaries")
        line = f"Lane changes: {len(changes) + old_changes} ({', '.join(details)})."
        peaks = [abs(_num(c.get("peakLatAccel"))) for c in changes
                 if c.get("outcome") == "completed" and _num(c.get("peakLatAccel")) is not None]
        if peaks:
            line += f" Sideways push: median {statistics.median(peaks):.1f}, highest {max(peaks):.1f} m/s²."
        lines.append(line)
    elif n and measured["lane"] == n:
        lines.append("Lane changes: none.")
    elif n and legacy_coverage["lane"] == n:
        lines.append("Lane changes: 0 in received older summaries.")
    if brakes or old_brakes:
        details = [_breakdown(brakes, "who", WHO_WORDS)] if brakes else []
        if old_brakes:
            details.append(f"{old_brakes} from older summaries")
        lines.append(f"Hard braking: {len(brakes) + old_brakes} ({', '.join(details)}).")
    elif n and measured["brake"] == n:
        lines.append("Hard braking: none.")
    elif n and legacy_coverage["brake"] == n:
        lines.append("Hard braking: 0 in received older summaries.")
    if ended_partial or unfinished:
        parts = []
        if ended_partial:
            parts.append(f"{ended_partial} ended drive{'' if ended_partial == 1 else 's'} with incomplete coverage")
        if unfinished:
            parts.append(f"{_drives(unfinished)} without an end record")
        line = "Recording: partial. " + ", ".join(parts) + "."
        if reasons:
            line += " Gaps: " + ", ".join(sorted(reasons)) + "."
        lines.append(line)
    elif n:
        lines.append("Recording: complete.")
    lines.append(closing_line)
    return lines
