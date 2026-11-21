"""Comma settings adapter and the read-only readers of the settings snapshot.

Device imports are lazy so the module is testable off the Comma. Params at the
installed build are typed: BOOL settings read as bool, FLOAT as float, INT as
int, JSON as the parsed object, and put() converts by the setting's declared
type.

Reading rule of the snapshot: a setting that was never set reads as its
declared default, which is the value sunnypilot itself uses. A read that
raises, or gives a value of the wrong type, gives None and never a default.
"""
from __future__ import annotations

import math
import numbers


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


def _is_float(value) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)


_KINDS = {
    bool: (lambda value: isinstance(value, bool), bool),
    float: (_is_float, float),
    int: (lambda value: isinstance(value, numbers.Integral) and not isinstance(value, bool), int),
    str: (lambda value: isinstance(value, str), str),
}


def read_setting(params: DeviceParams, key: str, kind):
    """The setting as a `kind` (bool, float, int or str), or None when the read raises or the type is wrong."""
    accepts, convert = _KINDS[kind]
    try:
        value = params.get(key)
    except Exception:
        return None
    return convert(value) if accepts(value) else None


def cache_delay_s(params: DeviceParams) -> float | None:
    """The cached delay total. For diagnosis only, never the learned delay: after a restored trial, or after
    learning was switched off and on while parked, it holds a stale fixed value."""
    return read_setting(params, "LagdValueCache", float)


def _stored_bytes(params: DeviceParams, key: str):
    try:
        raw = params.get(key)
    except Exception:
        return None
    return raw if isinstance(raw, (bytes, bytearray)) and raw else None


def _decode_live_delay(raw: bytes):
    from cereal import messaging
    return messaging.log_from_bytes(raw).liveDelay


def persisted_live_delay(params: DeviceParams, decode=_decode_live_delay):
    """The delay learner's own persisted message (it stores one a minute), or None when there is none
    or it cannot be read."""
    raw = _stored_bytes(params, "LiveDelay")
    if raw is None:
        return None
    try:
        return decode(raw)
    except Exception:
        return None


def _decode_car_params_sp(raw: bytes):
    from cereal import custom, messaging
    return messaging.log_from_bytes(raw, custom.CarParamsSP)


def nnlc_match(params: DeviceParams, decode=_decode_car_params_sp) -> tuple:
    """(name, fuzzy) of the neural-network steering model the build matched to the truck.

    The build fills these in whether the switch is on or off. CarParamsSP is cleared at onroad
    transitions, so fall back to the persistent copy. Each value is None when it cannot be read.
    """
    for key in ("CarParamsSP", "CarParamsSPPersistent"):
        raw = _stored_bytes(params, key)
        if raw is None:
            continue
        try:
            match = decode(raw).neuralNetworkLateralControl
            name, fuzzy = match.model.name, match.fuzzyFingerprint
        except Exception:
            return None, None
        return (name if isinstance(name, str) else None), (fuzzy if isinstance(fuzzy, bool) else None)
    return None, None


def _decode_car_params(raw: bytes):
    from cereal import car, messaging
    return messaging.log_from_bytes(raw, car.CarParams)


def base_actuator_delay_s(params: DeviceParams, decode=_decode_car_params) -> float | None:
    """CarParams.steerActuatorDelay; CarParams is cleared at onroad transitions,
    so fall back to the persistent copy."""
    for key in ("CarParams", "CarParamsPersistent"):
        try:
            raw = params.get(key)
        except Exception:
            return None
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
