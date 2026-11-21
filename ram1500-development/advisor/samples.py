"""Turn sunnypilot messages (live msgq or decoded logs) into Samples.

One Sample is built for every new carState that carries a speed. It joins the
newest message of each other stream, and a stream whose newest message is too
old (or too far ahead) is MISSING for that Sample, never "false". Model
messages have a second path: every new one becomes a ModelMsg exactly once.

Works on any objects with the cereal attribute names, so tests use
SimpleNamespace and the Comma agent uses the real decoded messages.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import metrics as _v1
from shared_rules import (ALERTS_FRESH_S, DT_CAP_S, FIRST_DT_S, FRESH_S, FUTURE_TOL_S, MODEL_DT_CAP_S,
                          MODEL_GAP_FAST_S, MODEL_GAP_SLOW_S, MODEL_PERIOD_MAX_S, MODEL_PERIOD_MIN_S,
                          MODEL_SLOW_PERIOD_S, MODEL_SPEED_SPACINGS)

# Joined streams and the short names used in `Sample.missing` and in the stale-time counters.
JOINED = (("carControl", "cc"), ("controlsState", "ctl"), ("carOutput", "co"),
          ("drivingModelData", "dmd"), ("radarState", "rs"), ("onroadEvents", "ev"))
LANE_CHANGE_STATES = {"off": 0, "preLaneChange": 1, "laneChangeStarting": 2, "laneChangeFinishing": 3}
LANE_CHANGE_DIRECTIONS = {"none": 0, "left": 1, "right": 2}
GEAR_DRIVE = "drive"


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


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _code(value, table):
    """Enum value (name, or the number itself) to its code; None when unknown."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in table.values() else None
    return table.get(str(value))


@dataclass(slots=True)
class Sample:
    """What every measure sees of one carState. Time is the carState's own time stamp."""
    t: float                                  # s since device boot
    dt: float = FIRST_DT_S                    # time this Sample stands for: spacing to the previous one, capped
    gap: bool = False                         # the spacing was over the cap; the rest of it is unobserved
    raw_dt: float = FIRST_DT_S                # the spacing itself (nominal on the first Sample of a drive)
    v_ego: float = 0.0                        # m/s
    steering_angle_deg: float = 0.0
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
        self._last_car_state_ns = None
        self._prev_sample_ns = None
        self._model_done_ns = None
        self._spacings = deque(maxlen=MODEL_SPEED_SPACINGS)

    # --- messages in --------------------------------------------------------
    def offer(self, stream: str, t_ns: int, msg) -> bool:
        """Keep `msg` if it is newer than what is held for its stream. True when it was kept."""
        held = self._latest.get(stream)
        if held is not None and t_ns <= held[0]:
            return False
        self._latest[stream] = (t_ns, msg)
        return True

    def forget_before(self, floor_ns: int) -> None:
        """Drop every held message older than `floor_ns` (left over from before the drive)."""
        for stream in [s for s, held in self._latest.items() if held[0] < floor_ns]:
            del self._latest[stream]

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
        v_ego = _number(get(car_state, "vEgo"))
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
        lead_present = get(lead, "status") is True
        model = found["drivingModelData"]
        events = found["onroadEvents"]
        return Sample(
            t=t_ns / 1e9, dt=dt, gap=gap, raw_dt=raw_dt, v_ego=v_ego,
            steering_angle_deg=_number(get(car_state, "steeringAngleDeg")) or 0.0,
            steering_torque=_number(get(car_state, "steeringTorque")) or 0.0,
            steering_torque_eps=_number(get(car_state, "steeringTorqueEps")) or 0.0,
            steering_pressed=get(car_state, "steeringPressed") is True,
            brake_pressed=get(car_state, "brakePressed") is True,
            cruise_enabled=get(car_state, "cruiseState", "enabled") is True,
            gear_drive=str(get(car_state, "gearShifter")) == GEAR_DRIVE,
            blinker=get(car_state, "leftBlinker") is True or get(car_state, "rightBlinker") is True,
            lat_active=None if control is None else get(control, "latActive") is True,
            actuators_torque=_number(get(control, "actuators", "torque")),
            desired_curvature=_number(get(controls_state, "desiredCurvature")),
            actual_curvature=_number(get(controls_state, "curvature")),
            torque_output=_number(get(found["carOutput"], "actuatorsOutput", "torqueOutputCan")),
            lead_present=lead_present,
            lead_distance_m=_number(get(lead, "dRel")) if lead_present else None,
            lane_change_state=_code(get(model, "meta", "laneChangeState"), LANE_CHANGE_STATES),
            alerts=None if events is None else _alert_names(events),
            missing=tuple(short for stream, short in JOINED if found[stream] is None),
        )

    def model_due(self, t_ns: int) -> ModelMsg | None:
        """The new model message to process after the Sample at `t_ns`, if there is one.

        A message is handed out once, after the first Sample at or after its
        own time. One that is newer than the Sample stays held for the next
        Sample; a newer message replaces a held one.
        """
        held = self._latest.get("drivingModelData")
        if held is None or held[0] > t_ns:
            return None
        m_ns, msg = held
        if self._model_done_ns is not None and m_ns <= self._model_done_ns:
            return None
        spacing = None if self._model_done_ns is None else (m_ns - self._model_done_ns) / 1e9
        self._model_done_ns = m_ns
        slow = self._stream_is_slow(spacing)
        lines = get(msg, "laneLineMeta")
        return ModelMsg(
            t_m=m_ns / 1e9,
            dt_m=0.0 if spacing is None else min(spacing, MODEL_DT_CAP_S),
            left_y=_number(get(lines, "leftY")), right_y=_number(get(lines, "rightY")),
            left_prob=_number(get(lines, "leftProb")), right_prob=_number(get(lines, "rightProb")),
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


def _alert_names(events) -> frozenset:
    if not isinstance(events, (list, tuple)):
        events = get(events, "onroadEvents") or []
    return frozenset(str(get(e, "name")) for e in events if get(e, "name") is not None)


# --- version 1 (still used by the version 1 agent and replay tool) -----------

FRESH_NS = 500_000_000
FUTURE_TOLERANCE_NS = 100_000_000   # carControl/controlsState for the same cycle arrive a few ms after carState
SCHEDULE_TOLERANCE = 0.25    # accept a sample up to 25% of an interval early (log timing jitter)
LANE_CHANGE_OFF = {"off", "None", ""}
RAM_STEER_LIMIT = 350


class SampleBuilder:
    def __init__(self, sample_hz: float = 10.0, steer_limit: int = RAM_STEER_LIMIT):
        self.interval_ns = int(1e9 / sample_hz)
        self.steer_limit = steer_limit
        self.latest = {}
        self.alerts = frozenset()
        self.next_due_ns = None

    def _fresh(self, service: str, t_ns: int):
        item = self.latest.get(service)
        if item is None or not -FUTURE_TOLERANCE_NS <= t_ns - item[0] <= FRESH_NS:
            return None
        return item[1]

    def ingest(self, service: str, t_ns: int, msg) -> _v1.Sample | None:
        if service == "onroadEvents":
            events = msg if isinstance(msg, (list, tuple)) else (get(msg, "onroadEvents") or [])
            self.alerts = frozenset(str(get(e, "name")) for e in events if get(e, "name") is not None)
            return None
        if service != "carState":
            self.latest[service] = (t_ns, msg)
            return None
        # Fixed schedule instead of "time since last sample": log timestamps
        # jitter by about a millisecond, and a strict gap test drops a third of
        # 10 Hz Q-log samples. After a gap the schedule restarts from now.
        if self.next_due_ns is not None and t_ns < self.next_due_ns - int(self.interval_ns * SCHEDULE_TOLERANCE):
            return None
        if self.next_due_ns is None:
            self.next_due_ns = t_ns + self.interval_ns
        else:
            self.next_due_ns = max(self.next_due_ns + self.interval_ns, t_ns + self.interval_ns)
        control = self._fresh("carControl", t_ns)
        controls_state = self._fresh("controlsState", t_ns)
        torque = _number(get(self._fresh("carOutput", t_ns), "actuatorsOutput", "torqueOutputCan"))
        lead = get(self._fresh("radarState", t_ns), "leadOne")
        lead_present = get(lead, "status") is True
        lane_state = str(get(self._fresh("drivingModelData", t_ns), "meta", "laneChangeState"))
        return _v1.Sample(
            t=t_ns / 1e9,
            v_ego=_number(get(msg, "vEgo")) or 0.0,
            a_ego=_number(get(msg, "aEgo")) or 0.0,
            steering_rate_deg_s=_number(get(msg, "steeringRateDeg")) or 0.0,
            steering_pressed=get(msg, "steeringPressed") is True,
            lat_active=get(control, "latActive") is True,
            desired_curvature=_number(get(controls_state, "desiredCurvature")),
            actual_curvature=_number(get(controls_state, "curvature")),
            torque_at_limit=torque is not None and abs(torque) >= 0.98 * self.steer_limit,
            lead_present=lead_present,
            lead_distance_m=_number(get(lead, "dRel")) if lead_present else None,
            lane_change=lane_state not in LANE_CHANGE_OFF,
            alerts=self.alerts,
        )
