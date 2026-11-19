"""Censor contradictory simultaneous carState telemetry for offline comfort analyses.

Input is already privacy-minimized in memory. No device/vehicle access, raw log
persistence or per-sample timestamps are exported. A conflict becomes a
synthetic invalid state at the SAME time, not a skipped row: causal as-of
queries must not resurrect an older, apparently healthy vehicle state.
"""
from __future__ import annotations

from itertools import groupby

# Exact type/value equality avoids conflating boolean True with numeric 1.
# These are the state fields consumed by either offline longitudinal/legacy
# lane-start-phase observer; an unknown field cannot make evidence stronger.
EVIDENCE_FIELDS = (
    "segment", "valid", "canValid", "cruiseState.enabled",
    "gasPressed", "brakePressed", "gearShifter",
    "vEgo", "aEgo", "steeringRateDeg",
)


def canonical_car_states(records):
    """Return (unique ordered states, privacy-safe timestamp counters).

    Only nonnegative integer nanosecond timestamps are eligible. Equal-time
    entries from *different* segments are also conflicting evidence: no
    accidental cross-segment selection based on insertion order.
    """
    source = sorted(
        (r for r in records if r.get("service") == "carState"
         and type(r.get("t")) is int and r["t"] >= 0),
        key=lambda r: r["t"],
    )
    output = []
    repeats = 0
    ambiguous = 0
    for timestamp, grouped in groupby(source, key=lambda r: r["t"]):
        group = list(grouped)
        repeats += len(group) - 1
        first = group[0]
        def agrees(other):
            return all(
                type(other.get(k)) is type(first.get(k))
                and other.get(k) == first.get(k)
                for k in EVIDENCE_FIELDS
            )
        if any(not agrees(other) for other in group[1:]):
            ambiguous += 1
            output.append({
                "service": "carState", "t": timestamp,
                "segment": None, "valid": False, "canValid": False,
            })
        else:
            output.append(first)
    return output, {
        "repeatedCarStateTimestampRows": repeats,
        "ambiguousCarStateTimestampGroups": ambiguous,
    }
