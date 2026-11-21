"""The steering delay the truck has learned, as its own learner reports it.

The learner (liveDelay, 4 messages a second) is the only source. The cached
total in the settings is never read as the learned delay: after a restored
trial, or after learning was switched off and on while parked, it holds a
stale fixed value.

Each summary carries the block

    delay    valueS   newest learned delay (s, 4 decimals)
             minS, maxS   smallest and largest seen in the window
             status   the learner's own status of the newest message
             blocks   its number of valid blocks

from the first message of the drive on. A window without a new message carries
the newest value. The value is sent whatever its status; what is trusted as
"the learned delay" is decided in `delay_record`, the object of drive_start
and drive_end:

    learnedS         the delay, only when the status is `estimated` and the value is plausible
    estimateS, estimateStdS, validBlocks, status      as the learner gives them
    cacheS           the cached total, for diagnosis only
    baseActuatorS    the car's own steering actuator delay
    outOfBand        learnedS lies outside the band every recorded drive stayed in
"""
from __future__ import annotations

import numbers

from measure_api import Measure
from samples import get, number
from shared_rules import DELAY_BAND_MAX_S, DELAY_BAND_MIN_S, DELAY_TRUST_MAX_S, DELAY_TRUST_MIN_S

STATUSES = ("unestimated", "estimated", "invalid")      # in the order of the learner's own numbering
ESTIMATED = "estimated"


def delay_status(value):
    """The learner's status as one of the three tokens, or None."""
    if value is None or isinstance(value, (bool, float, bytes)):
        return None
    if isinstance(value, numbers.Integral):
        return STATUSES[value] if 0 <= value < len(STATUSES) else None
    name = str(value)
    return name if name in STATUSES else None


def _seconds(value):
    value = number(value)
    return None if value is None else round(value, 4)


def _whole(value):
    if isinstance(value, bool) or not isinstance(value, numbers.Integral) or value < 0:
        return None
    return int(value)


def delay_record(msg, cache_s=None, base_actuator_s=None) -> dict:
    """The delay object of drive_start and drive_end, from a liveDelay message (or None when there is none)."""
    status = delay_status(get(msg, "status"))
    value = number(get(msg, "lateralDelay"))
    trusted = status == ESTIMATED and value is not None and DELAY_TRUST_MIN_S < value < DELAY_TRUST_MAX_S
    return {
        "learnedS": round(value, 4) if trusted else None,
        "estimateS": _seconds(get(msg, "lateralDelayEstimate")),
        "estimateStdS": _seconds(get(msg, "lateralDelayEstimateStd")),
        "validBlocks": _whole(get(msg, "validBlocks")),
        "status": status,
        "cacheS": _seconds(cache_s),
        "baseActuatorS": _seconds(base_actuator_s),
        "outOfBand": bool(trusted and not DELAY_BAND_MIN_S <= value <= DELAY_BAND_MAX_S),
    }


class LearnedDelay(Measure):
    name = "delay"
    owner_facing = True
    streams = ("liveDelay",)

    def __init__(self):
        self._msg = None                # newest message of the drive
        self._value = None              # its learned delay, when it carries a finite one
        self._min = self._max = None    # smallest and largest seen while the open window was open

    def on_stream(self, ctx, stream, t, msg) -> None:
        self._msg = msg
        self._value = value = number(get(msg, "lateralDelay"))
        if value is not None:
            if self._min is None or value < self._min:
                self._min = value
            if self._max is None or value > self._max:
                self._max = value

    def window_fields(self) -> dict:
        msg = self._msg
        if msg is None:
            return {}                   # no message yet: the block is left out
        value = self._value
        low = value if self._min is None else self._min
        high = value if self._max is None else self._max
        self._min = self._max = None
        return {"delay": {"valueS": _seconds(value), "minS": _seconds(low), "maxS": _seconds(high),
                          "status": delay_status(get(msg, "status")), "blocks": _whole(get(msg, "validBlocks"))}}

    def drive_end_fields(self) -> dict:
        return {"delay": delay_record(self._msg)}
