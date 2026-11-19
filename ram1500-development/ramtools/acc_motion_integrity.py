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
    if r.get("valid") is not True:
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
    states = sorted((r for r in records if r.get("service") == "carState"
                     and type(r.get("t")) is int and r["t"] >= 0), key=lambda r: r["t"])
    radar = sorted((r for r in records if r.get("service") == "radarState"
                    and type(r.get("t")) is int and r["t"] >= 0), key=lambda r: r["t"])
    # Index within segments before as-of joins: cross-segment radar reuse can
    # manufacture apparently continuous lead context at a log boundary.
    radar_by_segment = defaultdict(list)
    unscoped_radar_rows = 0
    for r in radar:
        segment = r.get("segment")
        if type(segment) is int and segment >= 0:
            radar_by_segment[segment].append(r)
        else:
            unscoped_radar_rows += 1
    # A causal as-of join must not let the ordering of duplicate R-log
    # observations determine the lead label. Exact duplicates coalesce;
    # conflicting observations at the same timestamp become unavailable.
    # This is a source-integrity gate, NOT OEM radar/ACC attribution.
    radar_index = {}
    repeated_radar_rows = 0
    ambiguous_radar_timestamps = 0
    evidence_fields = ("valid", "leadOne.status", "leadOne.dRel",
                       "leadOne.vRel", "leadOne.radar")
    for seg, rows in radar_by_segment.items():
        unique = []
        i = 0
        while i < len(rows):
            j = i + 1
            while j < len(rows) and rows[j]["t"] == rows[i]["t"]:
                j += 1
            group = rows[i:j]
            if len(group) > 1:
                repeated_radar_rows += len(group) - 1
                first = group[0]
                consistent = all(
                    all(type(r.get(field)) is type(first.get(field)) and
                        r.get(field) == first.get(field)
                        for field in evidence_fields)
                    for r in group[1:])
                if not consistent:
                    ambiguous_radar_timestamps += 1
                    # Preserve the timestamp as invalid rather than falling
                    # back to an older, potentially stale lead observation.
                    unique.append({"t": first["t"], "valid": False})
                else:
                    unique.append(first)
            else:
                unique.append(group[0])
            i = j
        radar_index[seg] = (unique, [r["t"] for r in unique])
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
        "source": "pinned_log_carState.aEgo_and_observational_radarState",
        "quality": {"carStateRows": len(states), "radarStateRows": len(radar),
                    "unscopedRadarRows": unscoped_radar_rows,
                    "repeatedRadarTimestampRows": repeated_radar_rows,
                    "ambiguousRadarTimestampGroups": ambiguous_radar_timestamps,
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
            "Equal-time conflicting radar evidence is unavailable regardless of record order; exact duplicates are coalesced.",
            "Callers must supply a single recorded route; segment numbers are only unique within one route.",
            "No per-sample timestamps, segment identities, VIN, GPS or raw CAN are exported.",
        ],
    }
