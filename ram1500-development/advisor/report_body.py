"""The nightly report's lines, built from the stored drive records. Measures only: no advice, no buttons.

Each line appears only when the drives carry the data it needs, so drives recorded by an older agent
produce the short form (drive count and closing line). Inputs are the service's drive records:
`end.totals`, `ready.blindSpot`, `summaries[].warnEvents`, and the `steering_loss`, `lane_change` and
`brake` event records (schema_v2.DATA).
"""
from __future__ import annotations

import statistics

HARD_BRAKE_DROP_MS = 3.0        # m/s lost within one second; the grade measure_brake leaves to the service
CAUSE_WORDS = {"brake": "brake", "gas": "gas", "cruise_off": "cruise off", "gear": "gear", "fault": "fault",
               "blinker": "blinker", "driver": "driver steering", "none": "no cause found"}
WHO_WORDS = {"driver": "driver", "cruise": "truck's cruise", "other": "other"}


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "under 1 min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def _events(drives: list, kind: str) -> list:
    return [e.get("data") or {} for d in drives for e in (d.get("events") or []) if e.get("type") == kind]


def _breakdown(counts: dict, words: dict) -> str:
    parts = sorted(((n, words.get(k, k)) for k, n in counts.items() if n), key=lambda x: (-x[0], x[1]))
    return ", ".join(f"{w} {n}" for n, w in parts)


def report_lines(drives: list, closing_line: str) -> list:
    n = len(drives)
    plural = "" if n == 1 else "s"
    totals = [((d.get("end") or {}).get("totals") or {}) for d in drives]
    window = [_num(t.get("windowS")) for t in totals]
    steering = [_num(t.get("steeringS")) for t in totals]
    if window and all(w is not None for w in window):
        line = f"{n} drive{plural}, {_duration(sum(window))}"
        if all(s is not None for s in steering):
            line += f", steering on {_duration(sum(steering))}"
        lines = [line + "."]
    else:
        lines = [f"{n} drive{plural} recorded."]

    bsm = [(d.get("ready") or {}).get("blindSpot") for d in drives]
    if any(b is not None for b in bsm):
        off, unknown = sum(b is False for b in bsm), sum(b is None for b in bsm)
        if off:
            lines.append(f"Blind spot: off on {off} of {n} drive{plural}.")
        elif unknown:
            lines.append(f"Blind spot: on; not reported for {unknown} of {n} drive{plural}.")
        else:
            lines.append("Blind spot: on for every drive." if n > 1 else "Blind spot: on.")

    summaries = [s for d in drives for s in (d.get("summaries") or [])]
    warn = [_num(s.get("warnEvents")) for s in summaries if _num(s.get("warnEvents")) is not None]
    if warn:
        lines.append(f"Steering-limit warnings: {int(sum(warn))}.")

    have_events = bool(summaries) or any(d.get("events") for d in drives)
    losses = _events(drives, "steering_loss")
    if have_events:
        if losses:
            causes: dict = {}
            for loss in losses:
                causes[loss.get("cause") or "none"] = causes.get(loss.get("cause") or "none", 0) + 1
            lines.append(f"Steering turned off while moving: {len(losses)} ({_breakdown(causes, CAUSE_WORDS)}).")
        else:
            lines.append("Steering turned off while moving: none.")

    changes = _events(drives, "lane_change")
    if have_events:
        if changes:
            done = sum(c.get("outcome") == "completed" for c in changes)
            cancelled = sum(c.get("outcome") == "cancelled" for c in changes)
            line = f"Lane changes: {len(changes)} ({done} completed, {cancelled} cancelled)."
            peaks = [abs(_num(c.get("peakLatAccel"))) for c in changes
                     if c.get("outcome") == "completed" and _num(c.get("peakLatAccel")) is not None]
            if peaks:
                line += f" Sideways push: median {statistics.median(peaks):.1f}, highest {max(peaks):.1f} m/s²."
            lines.append(line)
        else:
            lines.append("Lane changes: none.")

    brakes = [b for b in _events(drives, "brake") if (_num(b.get("peakDrop1s")) or 0.0) >= HARD_BRAKE_DROP_MS]
    if have_events:
        if brakes:
            who: dict = {}
            for b in brakes:
                who[b.get("who") or "other"] = who.get(b.get("who") or "other", 0) + 1
            lines.append(f"Hard braking: {len(brakes)} ({_breakdown(who, WHO_WORDS)}).")
        else:
            lines.append("Hard braking: none.")

    ends = [d.get("end") for d in drives if d.get("end")]
    if ends:
        failed = sorted({m for e in ends for m in (e.get("failed") or [])})
        not_listening = sum(e.get("listening") is False for e in ends)
        gaps = []
        if not_listening:
            gaps.append(f"listening off on {not_listening} drive{'' if not_listening == 1 else 's'}")
        if failed:
            gaps.append("measures stopped: " + ", ".join(failed))
        missing_end = n - len(ends)
        if missing_end:
            gaps.append(f"{missing_end} drive{'' if missing_end == 1 else 's'} without an end record")
        lines.append("Recording: " + ("; ".join(gaps) if gaps else "complete") + ".")

    lines.append(closing_line)
    return lines
