"""What the service makes of the sums the agent sends for the log-only quality
measures: lane sway, wheel swings, lateral-motion RMS, curve sums and the
response lag.

The agent sends sums and seconds, never finished values, so that they can be
pooled over any span later. `pool` adds blocks up; each value function takes
one block (a window's, or a pooled one) as it is sent and returns None when
the block holds less than the stated minimum of data.

LOG-ONLY: these values are stored. Nothing here is a threshold, a flag or a
verdict, and no report line reads them. Not installed on the Comma.
"""
from __future__ import annotations

import math

from shared_rules import (AY_MIN_S, AY_PERIOD_MAX_S, AY_PERIOD_MIN_S, LAG_STEP_S, LANE_MIN_SCORED_S,
                          SWING_MIN_STRAIGHT_S)

# How the fields of each block add up. Every field not named here is a plain sum.
_MEAN_BY = {"lane": {"vMean": "n"}, "swings": {"vMean": "straightS"}, "ay": {"vMean": "n"}}
_LOWEST = {"lane": ("widthMin", "probMin")}
_HIGHEST = {"lane": ("widthMax",)}


def pool(name: str, blocks):
    """The blocks of one kind (`name`: lane, swings, ay, curve, lag) added up; None when there is none."""
    blocks = [b for b in blocks if b]
    if not blocks:
        return None
    means, lowest, highest = _MEAN_BY.get(name, {}), _LOWEST.get(name, ()), _HIGHEST.get(name, ())
    out = {}
    for key in blocks[0]:
        values = [b[key] for b in blocks]
        if key in means:
            pairs = [(b[key], b[means[key]]) for b in blocks if b[key] is not None]
            weight = sum(w for _, w in pairs)
            out[key] = sum(v * w for v, w in pairs) / weight if weight > 0 else None
        elif key in lowest or key in highest:
            known = [v for v in values if v is not None]
            out[key] = (min(known) if key in lowest else max(known)) if known else None
        elif isinstance(values[0], (list, tuple)):
            out[key] = [sum(column) for column in zip(*values)]
        else:
            out[key] = sum(values)
    return out


def lane_sway_sd(block, min_scored_s: float = LANE_MIN_SCORED_S):
    """Lane sway in metres: RMS of the scored residuals; None under the minimum of scored time."""
    if not block or block["n"] <= 0 or block["scoredS"] < min_scored_s:
        return None
    return math.sqrt(block["sumSq"] / block["n"])


def swing_rate(block, min_straight_s: float = SWING_MIN_STRAIGHT_S):
    """Wheel swings per straight minute; None under the minimum of straight time."""
    if not block or block["straightS"] <= 0 or block["straightS"] < min_straight_s:
        return None
    return block["n"] / (block["straightS"] / 60.0)


def lat_accel_rms(block, loop_period_s=None, min_s: float = AY_MIN_S):
    """(actual, planned) lateral-motion RMS in m/s^2; None under the minimum of accepted time.

    `loop_period_s` is the window's own `windowS / n`. The filters behind the
    sums are made for about ten Samples a second, so a window whose loop ran
    outside 0.095 to 0.125 s gives no value. Leave it out for a pooled block.
    """
    if not block or block["n"] <= 0 or block["s"] < min_s:
        return None
    if loop_period_s is not None and not AY_PERIOD_MIN_S <= loop_period_s <= AY_PERIOD_MAX_S:
        return None
    return math.sqrt(block["sumSq"] / block["n"]), math.sqrt(block["planSumSq"] / block["n"])


def curve_tracking(block) -> dict:
    """Mean distance of actual cornering below and above what was commanded, m/s^2, over a span.

    `below`, `above`: on curve Samples. `below04`, `above04`: against the
    demand 0.4 s earlier, which takes the curve entry out. `lowBelow`,
    `lowAbove`: the low-speed line. Each is None when its seconds are zero.
    """
    def per_second(total, seconds):
        return total / seconds if seconds > 0 else None
    if not block:
        return dict.fromkeys(("below", "above", "below04", "above04", "lowBelow", "lowAbove"))
    return {"below": per_second(block["belowSum"], block["s"]), "above": per_second(block["aboveSum"], block["s"]),
            "below04": per_second(block["below04Sum"], block["s04"]),
            "above04": per_second(block["above04Sum"], block["s04"]),
            "lowBelow": per_second(block["lowBelowSum"], block["lowS"]),
            "lowAbove": per_second(block["lowAboveSum"], block["lowS"])}


def lag_fit(block):
    """(lag in s, gain) of the steering response from lag sums; None when they cannot give one.

    For each whole step k: the error left when the actual cornering is
    explained by the demand k steps earlier. The lag is the step with the
    least error, refined by a parabola through its two neighbours.
    """
    if not block or block["n"] <= 0:
        return None
    syy, sxy, sxx = block["syy"], block["sxy"], block["sxx"]
    if len(sxy) != len(sxx) or not sxx or min(sxx) <= 0:
        return None
    sse = [syy - xy * xy / xx for xy, xx in zip(sxy, sxx)]
    k = min(range(len(sse)), key=sse.__getitem__)
    offset = 0.0
    if 0 < k < len(sse) - 1:
        bend = sse[k - 1] - 2 * sse[k] + sse[k + 1]
        if bend != 0:
            offset = 0.5 * (sse[k - 1] - sse[k + 1]) / bend
    return (k + offset) * LAG_STEP_S, sxy[k] / sxx[k]
