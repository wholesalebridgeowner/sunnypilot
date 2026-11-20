"""Allowlist and fixed safe ranges for Drive Advisor setting changes.

These constants are the safety boundary. Nothing received over the network
can widen them; the Comma agent imports them directly.
"""
from __future__ import annotations

import math

STEP_S = 0.05
MAX_OFFSET_S = 0.10
MIN_TOTAL_S = 0.10
MAX_TOTAL_S = 0.50
LIVE_KEYS = frozenset({"LagdToggle", "LagdToggleDelay"})
NIGHTLY_KEYS = LIVE_KEYS | {"NeuralNetworkLateralControl"}
BOOL_KEYS = frozenset({"LagdToggle", "NeuralNetworkLateralControl"})
MODES = ("live", "nightly")
_EPS = 1e-9


class GuardError(ValueError):
    """A requested change is outside the allowlist or the safe range."""


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trial_values(learned_delay_s: float, base_actuator_delay_s: float, step: int) -> dict:
    """Settings for a delay trial `step` x 0.05 s away from the learned delay."""
    if step not in (-2, -1, 1, 2):
        raise GuardError("step_out_of_range")
    if not (_finite(learned_delay_s) and _finite(base_actuator_delay_s)) or base_actuator_delay_s < 0:
        raise GuardError("invalid_delay_inputs")
    total = round(learned_delay_s + step * STEP_S, 3)
    change = {"LagdToggle": False, "LagdToggleDelay": round(total - base_actuator_delay_s, 3)}
    validate_change(change, mode="live", learned_delay_s=learned_delay_s,
                    base_actuator_delay_s=base_actuator_delay_s)
    return change


def validate_change(change: dict, *, mode: str, learned_delay_s: float,
                    base_actuator_delay_s: float) -> None:
    """Raise GuardError unless `change` is allowed in `mode`."""
    if mode not in MODES:
        raise GuardError("unknown_mode")
    if not isinstance(change, dict) or not change:
        raise GuardError("empty_change")
    allowed = LIVE_KEYS if mode == "live" else NIGHTLY_KEYS
    for key, value in change.items():
        if key not in allowed:
            raise GuardError(f"key_not_allowed:{key}")
        if key in BOOL_KEYS and not isinstance(value, bool):
            raise GuardError(f"bool_required:{key}")
    if mode == "live" and set(change) != LIVE_KEYS:
        raise GuardError("live_change_must_set_both_delay_keys")
    if "LagdToggleDelay" not in change:
        return
    software = change["LagdToggleDelay"]
    if not _finite(software) or software < 0:
        raise GuardError("invalid_software_delay")
    if change.get("LagdToggle", False) is not False:
        raise GuardError("fixed_delay_requires_learning_off")
    if not (_finite(learned_delay_s) and _finite(base_actuator_delay_s)):
        raise GuardError("invalid_delay_inputs")
    total = base_actuator_delay_s + software
    if not (MIN_TOTAL_S - _EPS <= total <= MAX_TOTAL_S + _EPS):
        raise GuardError("total_delay_out_of_range")
    if abs(total - learned_delay_s) > MAX_OFFSET_S + _EPS:
        raise GuardError("delay_too_far_from_learned")
    steps = (total - learned_delay_s) / STEP_S
    if abs(steps - round(steps)) > 1e-6:
        raise GuardError("delay_not_on_step")
