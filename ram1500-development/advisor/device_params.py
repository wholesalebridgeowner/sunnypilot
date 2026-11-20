"""Comma settings adapter and steering-delay readers.

Device imports are lazy so the module is testable off the Comma. Params at the
installed build are typed: BOOL settings read as bool, FLOAT as float, and
put() converts by the setting's declared type.
"""
from __future__ import annotations

import math


class DeviceParams:
    def __init__(self, params=None):
        if params is None:
            from openpilot.common.params import Params
            params = Params()
        self.p = params

    def get(self, key):
        return self.p.get(key, return_default=True)

    def put(self, key, value) -> None:
        self.p.put(key, value)

    def onroad(self) -> bool:
        return bool(self.p.get_bool("IsOnroad"))


def _plausible_delay(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if 0.0 < value < 1.0 else None


def learned_delay_s(params: DeviceParams) -> float | None:
    """The truck's learned steering delay. Only trusted while live learning is on;
    otherwise None, which makes the guard refuse live trials (fail closed)."""
    if params.get("LagdToggle") is not True:
        return None
    return _plausible_delay(params.get("LagdValueCache"))


def _decode_car_params(raw: bytes):
    from cereal import car, messaging
    return messaging.log_from_bytes(raw, car.CarParams)


def base_actuator_delay_s(params: DeviceParams, decode=_decode_car_params) -> float | None:
    """CarParams.steerActuatorDelay; CarParams is cleared at onroad transitions,
    so fall back to the persistent copy."""
    for key in ("CarParams", "CarParamsPersistent"):
        raw = params.get(key)
        if not raw:
            continue
        try:
            value = decode(raw).steerActuatorDelay
        except Exception:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0.0 <= value < 1.0:
            return float(value)
        return None
    return None
