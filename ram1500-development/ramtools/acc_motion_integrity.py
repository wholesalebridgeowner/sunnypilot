"""Read-only observed longitudinal-motion metrics with strict continuity gates.

Input: privacy-minimized, source-pinned ramtools.logio records. No CAN output,
vehicle access or actuator changes. OEM ACC gap/throttle is not reconstructed.
Output excludes times, locations, segment IDs, raw CAN, VIN and samples.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from math import isfinite
from statistics import median

NS = 1_000_000_000
MOTION_QUALIFICATION = "observed_factory_acc_motion_equal_clock_integrity_v2"
LABELS = ("radar_flagged_lead", "model_flagged_lead", "lead_source_unspecified",
          "no_confirmed_lead", "lead_unavailable")


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
        return float(value)
    return None


def _pct(values, q):
    xs = sorted(x for v in values if (x := _number(v)) is not None)
    if not xs:
        return None
    at = (len(xs) - 1) * q
    lower = int(at)
    return round(xs[lower] + (xs[min(lower + 1, len(xs) - 1)] - xs[lower]) * (at - lower), 3)


def _same_observation(left, right):
    """Treat identical decoded rows as duplicates, not independent samples."""
    return (left.keys() == right.keys() and
            all(type(left[key]) is type(right[key]) and left[key] == right[key]
                for key in left))


def _lead_label(t, segment, radar_index, max_radar_age_s):
    # Segment provenance is mandatory. A fresh radar frame from the previous
    # segment is NOT a verified observation within this segment.
    scoped = radar_index.get(segment)
    if scoped is None:
        return "lead_unavailable"
    radar, times = scoped
    i = bisect_right(times, t) - 1
    if i < 0 or (t - times[i]) / NS > max_radar_age_s:
        return "lead_unavailable"
    r = radar[i]
    if r is None or r.get("valid") is not True:
        return "lead_unavailable"
    if r.get("leadOne.status") is False:
        return "no_confirmed_lead"
    if r.get("leadOne.status") is not True:
        return "lead_unavailable"
    d = _number(r.get("leadOne.dRel"))
    if d is None or not 0 < d < 300:
        return "lead_unavailable"
    flag = r.get("leadOne.radar")
    return ("radar_flagged_lead" if flag is True else
            "model_flagged_lead" if flag is False else "lead_source_unspecified")


def _episode_stats(rows, *, smooth_window_s, min_smooth_samples):
    accels = [x["accel"] for x in rows]
    jerks = []
    for a, b in zip(rows, rows[1:]):
        delta = (b["t"] - a["t"]) / NS
        if delta > 0:
            jerks.append(abs((b["accel"] - a["accel"]) / delta))
    bins = defaultdict(list)
    start = rows[0]["t"]
    for row in rows:
        key = int((row["t"] - start) / NS / smooth_window_s)
        bins[key].append(row)
    smoothed = []
    for key, bucket in sorted(bins.items()):
        if len(bucket) >= min_smooth_samples:
            smoothed.append((key, median([r["t"] for r in bucket]),
                             median([r["accel"] for r in bucket])))
    smooth_jerks = []
    for a, b in zip(smoothed, smoothed[1:]):
        if b[0] != a[0] + 1:
            continue
        dt = (b[1] - a[1]) / NS
        if dt > 0:
            smooth_jerks.append(abs((b[2] - a[2]) / dt))
    return accels, jerks, smooth_jerks


def analyze(records, *, max_state_gap_s=0.25, max_radar_age_s=0.25,
            minimum_episode_s=2.0, min_episode_samples=10,
            smooth_window_s=0.5, min_smooth_samples=3):
    """Aggregate acceleration/jerk by OBSERVED lead context, not ACC commands.

    Invalid state, any pedal actuation, cruise-off, time gap, missing segment,
    gear change, or lead-context change breaks continuity. No interpolation.
    """
    params = (max_state_gap_s, max_radar_age_s, minimum_episode_s, smooth_window_s)
    if any(_number(v) is None for v in params):
        raise ValueError("numeric quality thresholds required")
    if not (0.01 <= max_state_gap_s <= 1 and 0.01 <= max_radar_age_s <= 2 and
            0 < minimum_episode_s <= 120 and 0.1 <= smooth_window_s <= 5):
        raise ValueError("quality thresholds out of bounds")
    if (type(min_episode_samples) is not int or not 3 <= min_episode_samples <= 100000 or
            type(min_smooth_samples) is not int or not 2 <= min_smooth_samples <= 10000):
        raise ValueError("sample thresholds out of bounds")
    raw_states = [r for r in records if r.get("service") == "carState"
                  and type(r.get("t")) is int and r["t"] >= 0]
    radar = [r for r in records if r.get("service") == "radarState"
             and type(r.get("t")) is int and r["t"] >= 0]
    # Equal-time vehicle states are one observation. Different values at the
    # same clock cannot be selected by record ordering: quarantine that clock
    # and break the candidate episode. Neither earlier nor later states fill it.
    state_by_clock = {}
    state_conflicts = set()
    duplicate_states = 0
    for r in raw_states:
        t = r["t"]
        if t in state_conflicts:
            continue
        if t not in state_by_clock:
            state_by_clock[t] = r
        elif _same_observation(state_by_clock[t], r):
            duplicate_states += 1
        else:
            state_by_clock[t] = None
            state_conflicts.add(t)
    states = [state_by_clock[t] for t in sorted(state_by_clock)]

    # Index radar within recording segments, not only time. A contradictory
    # pair with the same clock is unavailable until a genuinely newer frame;
    # an older previously-valid radar frame must not be resurrected.
    radar_by_segment = defaultdict(dict)
    radar_conflicts = set()
    duplicate_radar = 0
    unscoped_radar_rows = 0
    for r in radar:
        segment = r.get("segment")
        if type(segment) is not int or segment < 0:
            unscoped_radar_rows += 1
            continue
        t = r["t"]
        clock = (segment, t)
        if clock in radar_conflicts:
            continue
        bucket = radar_by_segment[segment]
        if t not in bucket:
            bucket[t] = r
        elif _same_observation(bucket[t], r):
            duplicate_radar += 1
        else:
            bucket[t] = None
            radar_conflicts.add(clock)
    radar_index = {}
    for segment, bucket in radar_by_segment.items():
        times = sorted(bucket)
        radar_index[segment] = ([bucket[t] for t in times], times)
    rejects = Counter()
    episodes = []
    current = []
    previous = None

    def close():
        nonlocal current
        if current:
            episodes.append(current)
            current = []

    for r in states:
        if r is None:
            rejects["ambiguous_carState_clock"] += 1
            close()
            previous = None
            continue
        t = r["t"]
        speed = _number(r.get("vEgo"))
        accel = _number(r.get("aEgo"))
        gear = str(r.get("gearShifter"))
        if r.get("valid") is not True or r.get("canValid") is not True:
            reason = "invalid_vehicle_or_can"
        elif r.get("cruiseState.enabled") is not True:
            reason = "cruise_not_confirmed"
        elif r.get("gasPressed") is not False or r.get("brakePressed") is not False:
            reason = "pedal_or_unknown"
        elif type(r.get("segment")) is not int or r["segment"] < 0:
            reason = "segment_unverified"
        elif gear != "drive":
            reason = "gear_not_confirmed_drive"
        elif speed is None or not 2 < speed < 65 or accel is None:
            reason = "invalid_kinematics"
        else:
            reason = None
        if reason:
            rejects[reason] += 1
            close()
            previous = None
            continue
        label = _lead_label(t, r["segment"], radar_index, max_radar_age_s)
        if previous is not None:
            dt = (t - previous["t"]) / NS
            if (dt <= 0 or dt > max_state_gap_s or
                r.get("segment") != previous["segment"] or label != previous["label"]):
                close()
        sample = {"t": t, "accel": accel, "label": label, "segment": r.get("segment")}
        current.append(sample)
        previous = sample
    close()
    accepted = [ep for ep in episodes if len(ep) >= min_episode_samples and
                (ep[-1]["t"] - ep[0]["t"]) / NS >= minimum_episode_s]
    totals = {label: {"episodes": 0, "durationSeconds": 0.0,
                      "accels": [], "jerks": [], "smoothJerks": [],
                      "jerkPairs": 0, "smoothedPairs": 0} for label in LABELS}
    for ep in accepted:
        entry = totals[ep[0]["label"]]
        a, j, sj = _episode_stats(ep, smooth_window_s=smooth_window_s,
                                  min_smooth_samples=min_smooth_samples)
        entry["episodes"] += 1
        entry["durationSeconds"] += (ep[-1]["t"] - ep[0]["t"]) / NS
        entry["accels"].extend(a)
        entry["jerks"].extend(j)
        entry["smoothJerks"].extend(sj)
        entry["jerkPairs"] += len(j)
        entry["smoothedPairs"] += len(sj)
    return {
        "status": "observed_longitudinal_not_throttle_or_acc_target",
        "measurementQualification": MOTION_QUALIFICATION,
        "source": "pinned_log_carState.aEgo_and_observational_radarState",
        "quality": {"carStateRows": len(raw_states), "radarStateRows": len(radar),
                    "uniqueCarStateClocks": len(states),
                    "duplicateCarStateRows": duplicate_states,
                    "ambiguousCarStateClocks": len(state_conflicts),
                    "duplicateRadarRows": duplicate_radar,
                    "ambiguousRadarClocks": len(radar_conflicts),
                    "unscopedRadarRows": unscoped_radar_rows,
                    "excludedRowsByReason": dict(rejects),
                    "continuousCandidateRuns": len(episodes),
                    "qualifiedEpisodes": len(accepted),
                    "minimumEpisodeSeconds": minimum_episode_s,
                    "maxStateGapSeconds": max_state_gap_s,
                    "maxRadarAgeSeconds": max_radar_age_s,
                    "smoothingWindowSeconds": smooth_window_s},
        "byObservedLeadContext": {label: {
            "episodeCount": stats["episodes"],
            "durationSeconds": round(stats["durationSeconds"], 3),
            "accelerationSamples": len(stats["accels"]),
            "medianAccelerationMps2": _pct(stats["accels"], .5),
            "p90AbsoluteAccelerationMps2": _pct([abs(x) for x in stats["accels"]], .9),
            "rawJerkPairs": stats["jerkPairs"],
            "p90AbsoluteRawJerkMps3": _pct(stats["jerks"], .9),
            "smoothedWindowJerkPairs": stats["smoothedPairs"],
            "p90AbsoluteWindowedJerkProxyMps3": _pct(stats["smoothJerks"], .9)
        } for label, stats in totals.items()},
        "limits": [
            "cruiseState.enabled is observed cruise status; exact OEM ACC mode and set gap are not independently decoded.",
            "radarState lead flags do not prove what the factory ACC ECU followed or which sensor it used.",
            "carState.aEgo is an estimated measured-motion signal, not throttle, commanded acceleration or brake actuation.",
            "Raw jerk is noisy; windowed median-bin derivative is an observational proxy, not a vehicle jerk rating.",
            "Disengagement, manual pedal use, invalid CAN/vehicle data and discontinuous segments are excluded, not joined.",
            "A qualifying episode is one correlated time interval, not an independent experiment or validated control target.",
            "Radar observations must match the carState segment. An absent, stale or unscoped radar sample is unavailable.",
            "Conflicting equal-clock carState observations break episodes; conflicting equal-clock radar frames remain unavailable until newer evidence.",
            "Identical equal-clock records are deduplicated; v1 and v2 results cannot be directly compared without identical-route reprocessing.",
            "Callers must supply a single recorded route; segment numbers are only unique within one route.",
            "No per-sample timestamps, segment identities, VIN, GPS or raw CAN are exported.",
        ],
    }
