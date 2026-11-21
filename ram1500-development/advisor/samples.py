"""Turn sunnypilot messages (live msgq or decoded logs) into Samples.

One Sample is built for every new carState that carries a speed. It joins the
newest message of each other stream, and a stream whose newest message is too
old (or too far ahead) is MISSING for that Sample, never "false". Model
messages have a second path: every new one becomes a ModelMsg exactly once.

Works on any objects with the cereal attribute names, so tests use
SimpleNamespace and the Comma agent uses the real decoded messages. Numbers
and flags may be plain Python values or numpy scalars. A flag is on only for
a real True; any other value reads as off. A value that is not a finite
number reads as unknown.
"""
from __future__ import annotations

import math
import numbers
from collections import deque
from dataclasses import dataclass

from shared_rules import (ALERTS_FRESH_S, DT_CAP_S, FIRST_DT_S, FRESH_S, FUTURE_TOL_S, MODEL_DT_CAP_S,
                          MODEL_GAP_FAST_S, MODEL_GAP_SLOW_S, MODEL_PERIOD_MAX_S, MODEL_PERIOD_MIN_S,
                          MODEL_SLOW_PERIOD_S, MODEL_SPEED_SPACINGS)

# Joined streams and the short names used in `Sample.missing` and in the stale-time counters.
JOINED = (("carControl", "cc"), ("controlsState", "ctl"), ("carOutput", "co"),
          ("drivingModelData", "dmd"), ("radarState", "rs"), ("onroadEvents", "ev"))
LANE_CHANGE_STATES = {"off": 0, "preLaneChange": 1, "laneChangeStarting": 2, "laneChangeFinishing": 3}
LANE_CHANGE_DIRECTIONS = {"none": 0, "left": 1, "right": 2}
GEAR_DRIVE = "drive"
MODEL_STREAM = "drivingModelData"
ALERT_STREAM = "onroadEvents"


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


def get(obj, *names):
    for name in names:
        if obj is None:
            return None
        try:
            obj = obj[name] if isinstance(obj, dict) else getattr(obj, name)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
    return obj


def number(value):
    """A finite number as a float, else None. Plain numbers and numpy scalars count; a bool does not."""
    if type(value) is not float:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            return None
        value = float(value)
    return value if math.isfinite(value) else None


def _true(value) -> bool:
    """True only for a real True: Python's own, or numpy's."""
    if value is True:
        return True
    kind = type(value)
    return kind.__module__ == "numpy" and kind.__name__ in ("bool", "bool_") and bool(value)


def _code(value, table):
    """Enum value (name, or the number itself) to its code; None when unknown."""
    if value is None:
        return None
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value) if value in table.values() else None
    return table.get(str(value))


@dataclass(slots=True)
class Sample:
    """What every measure sees of one carState. Time is the carState's own time stamp."""
    t: float                                  # s since device boot
    dt: float = FIRST_DT_S                    # time this Sample stands for: spacing to the previous one, capped
    gap: bool = False                         # the spacing was over the cap; the rest of it is unobserved
    raw_dt: float = FIRST_DT_S                # the spacing itself (nominal on the first Sample of a drive)
    v_ego: float = 0.0                        # m/s
    steering_angle_deg: float | None = 0.0    # None = carState carried no finite wheel angle
    steering_torque: float = 0.0              # driver torque, native units
    steering_torque_eps: float = 0.0          # motor torque, native units
    steering_pressed: bool = False            # raw
    brake_pressed: bool = False
    cruise_enabled: bool = False
    gear_drive: bool = False
    blinker: bool = False                     # left or right
    lat_active: bool | None = None            # None = carControl missing
    actuators_torque: float | None = None     # -1..1
    desired_curvature: float | None = None    # 1/m; None = controlsState missing
    actual_curvature: float | None = None
    torque_output: float | None = None        # steering command sent, native units; None = carOutput missing
    lead_present: bool = False
    lead_distance_m: float | None = None
    lane_change_state: int | None = None      # 0 off, 1 pre, 2 starting, 3 finishing; None = model stream missing
    alerts: frozenset | None = None           # alert names; None = no alert list for 5.0 s
    missing: tuple = ()                       # short names of the streams missing for this Sample


@dataclass(slots=True)
class ModelMsg:
    """One new drivingModelData message."""
    t_m: float                                # s since device boot
    dt_m: float = 0.0                         # time since the previous ModelMsg, capped (0 on the first)
    left_y: float | None = None               # lane lines at the truck, m (left is negative)
    right_y: float | None = None
    left_prob: float | None = None
    right_prob: float | None = None
    lane_change_state: int | None = None      # 0 off, 1 pre, 2 starting, 3 finishing
    lane_change_direction: int | None = None  # 0 none, 1 left, 2 right
    spacing: float | None = None              # uncapped time since the previous ModelMsg; None on the first
    slow: bool = False                        # the stream is slow (decimated log), judged with this message
    gap: bool = False                         # the spacing is over the gap limit of this stream speed


class StreamJoiner:
    """Keeps the newest message of each stream and builds Samples and ModelMsgs.

    Times are log time stamps in nanoseconds; intervals and freshness are
    worked out on those integers, so nothing depends on float rounding.
    """

    def __init__(self, fresh_model_s: float = FRESH_S, fresh_lead_s: float = FRESH_S):
        self._fresh_ns = {"carControl": _ns(FRESH_S), "controlsState": _ns(FRESH_S), "carOutput": _ns(FRESH_S),
                          "drivingModelData": _ns(fresh_model_s), "radarState": _ns(fresh_lead_s),
                          "onroadEvents": _ns(ALERTS_FRESH_S)}
        self._future_ns = _ns(FUTURE_TOL_S)
        self._cap_ns = _ns(DT_CAP_S)
        self._latest = {}
        self._alerts = None                # alert names of the held alert list; None = it could not be read
        self._last_car_state_ns = None
        self._prev_sample_ns = None
        self._model_new = None             # model message that came since the last call of models_after
        self._model_held = None            # model message newer than the last Sample
        self._model_done_ns = None         # time of the last ModelMsg handed out
        self._spacings = deque(maxlen=MODEL_SPEED_SPACINGS)

    # --- messages in --------------------------------------------------------
    def offer(self, stream: str, t_ns: int, msg) -> bool:
        """Keep `msg` if it is newer than what is held for its stream. True when it was kept."""
        held = self._latest.get(stream)
        if held is not None and t_ns <= held[0]:
            return False
        self._latest[stream] = (t_ns, msg)
        if stream == MODEL_STREAM:
            self._model_new = (t_ns, msg)
        elif stream == ALERT_STREAM:
            self._alerts = _alert_names(msg)       # read once, here: the list is used for up to 5.0 s
        return True

    def forget_before(self, floor_ns: int) -> None:
        """Drop every held message older than `floor_ns` (left over from before the drive)."""
        for stream in [s for s, held in self._latest.items() if held[0] < floor_ns]:
            del self._latest[stream]
        if self._model_new is not None and self._model_new[0] < floor_ns:
            self._model_new = None
        if self._model_held is not None and self._model_held[0] < floor_ns:
            self._model_held = None

    def latest(self, stream: str):
        """(t_ns, msg) of the newest message held for `stream`, or None."""
        return self._latest.get(stream)

    def _fresh(self, stream: str, t_ns: int):
        held = self._latest.get(stream)
        if held is None or not -self._future_ns <= t_ns - held[0] <= self._fresh_ns[stream]:
            return None
        return held[1]

    # --- Samples out --------------------------------------------------------
    def sample(self, t_ns: int, car_state) -> Sample | None:
        """The Sample of a new carState; None when it is not new or carries no finite speed."""
        if self._last_car_state_ns is not None and t_ns <= self._last_car_state_ns:
            return None
        self._last_car_state_ns = t_ns
        v_ego = number(get(car_state, "vEgo"))
        if v_ego is None:
            return None                       # no Sample: the time stays unobserved
        if self._prev_sample_ns is None:
            raw_dt, dt, gap = FIRST_DT_S, FIRST_DT_S, False
        else:
            spacing_ns = t_ns - self._prev_sample_ns
            raw_dt = spacing_ns / 1e9
            gap = spacing_ns > self._cap_ns
            dt = DT_CAP_S if gap else raw_dt
        self._prev_sample_ns = t_ns

        found = {stream: self._fresh(stream, t_ns) for stream, _ in JOINED}
        control, controls_state = found["carControl"], found["controlsState"]
        lead = get(found["radarState"], "leadOne")
        lead_present = _true(get(lead, "status"))
        model = found["drivingModelData"]
        alerts = None if found[ALERT_STREAM] is None else self._alerts
        if alerts is None:
            found[ALERT_STREAM] = None        # a list that cannot be read is missing, never "no alert"
        return Sample(
            t=t_ns / 1e9, dt=dt, gap=gap, raw_dt=raw_dt, v_ego=v_ego,
            steering_angle_deg=number(get(car_state, "steeringAngleDeg")),
            steering_torque=number(get(car_state, "steeringTorque")) or 0.0,
            steering_torque_eps=number(get(car_state, "steeringTorqueEps")) or 0.0,
            steering_pressed=_true(get(car_state, "steeringPressed")),
            brake_pressed=_true(get(car_state, "brakePressed")),
            cruise_enabled=_true(get(car_state, "cruiseState", "enabled")),
            gear_drive=str(get(car_state, "gearShifter")) == GEAR_DRIVE,
            blinker=_true(get(car_state, "leftBlinker")) or _true(get(car_state, "rightBlinker")),
            lat_active=None if control is None else _true(get(control, "latActive")),
            actuators_torque=number(get(control, "actuators", "torque")),
            desired_curvature=number(get(controls_state, "desiredCurvature")),
            actual_curvature=number(get(controls_state, "curvature")),
            torque_output=number(get(found["carOutput"], "actuatorsOutput", "torqueOutputCan")),
            lead_present=lead_present,
            lead_distance_m=number(get(lead, "dRel")) if lead_present else None,
            lane_change_state=_code(get(model, "meta", "laneChangeState"), LANE_CHANGE_STATES),
            alerts=alerts,
            missing=tuple(short for stream, short in JOINED if found[stream] is None),
        )

    def models_after(self, sample_ns: int | None) -> list:
        """The new model messages to process now, oldest first. Call once per batch:
        with the time of the Sample the batch gave, or None when it gave none.

        A message is handed out once, right after the first Sample at or after
        its own time. One that is newer than the Sample of its batch is held
        and goes out after the next Sample. At most one message is held: while
        no Sample comes, a newer message replaces it.
        """
        waiting = [m for m in (self._model_held, self._model_new) if m is not None]
        self._model_held = self._model_new = None
        out = []
        for m_ns, msg in waiting:
            if sample_ns is not None and m_ns <= sample_ns:
                out.append(self._model_msg(m_ns, msg))
            else:
                self._model_held = (m_ns, msg)
        return out

    def _model_msg(self, m_ns: int, msg) -> ModelMsg:
        spacing = None if self._model_done_ns is None else (m_ns - self._model_done_ns) / 1e9
        self._model_done_ns = m_ns
        slow = self._stream_is_slow(spacing)
        lines = get(msg, "laneLineMeta")
        return ModelMsg(
            t_m=m_ns / 1e9,
            dt_m=0.0 if spacing is None else min(spacing, MODEL_DT_CAP_S),
            left_y=number(get(lines, "leftY")), right_y=number(get(lines, "rightY")),
            left_prob=number(get(lines, "leftProb")), right_prob=number(get(lines, "rightProb")),
            lane_change_state=_code(get(msg, "meta", "laneChangeState"), LANE_CHANGE_STATES),
            lane_change_direction=_code(get(msg, "meta", "laneChangeDirection"), LANE_CHANGE_DIRECTIONS),
            spacing=spacing, slow=slow,
            gap=spacing is not None and spacing > (MODEL_GAP_SLOW_S if slow else MODEL_GAP_FAST_S),
        )

    def _stream_is_slow(self, spacing) -> bool:
        """Stream speed from the median of the newest spacings (upper middle for an even number)."""
        if spacing is not None:
            self._spacings.append(spacing)
        if not self._spacings:
            return False                      # a single message counts as a fast stream
        median = sorted(self._spacings)[len(self._spacings) // 2]
        return min(max(median, MODEL_PERIOD_MIN_S), MODEL_PERIOD_MAX_S) > MODEL_SLOW_PERIOD_S


def _alert_names(events) -> frozenset | None:
    """The alert names of an onroadEvents message, or None when it cannot be read.

    Accepted: any iterable of events (a list, a tuple, the list as a message
    decoder hands it over), or a message that holds such a list as
    `onroadEvents`. An event is an object or a dict with a `name`. Anything
    else gives None, which makes the alert list missing: a list that is not
    understood must never read as "no alert".
    """
    try:
        if not isinstance(events, (list, tuple)):
            inner = get(events, "onroadEvents")
            if inner is not None:
                events = inner
        if events is None or isinstance(events, (str, bytes, bytearray, dict)):
            return None
        names = []
        for event in events:
            name = get(event, "name")
            if name is None:
                return None
            names.append(str(name))
        return frozenset(names)
    except Exception:
        return None
