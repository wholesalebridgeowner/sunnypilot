"""Turn sunnypilot messages (live msgq or decoded logs) into metrics Samples.

Works on any objects with the cereal attribute names, so tests use
SimpleNamespace and the Comma agent uses the real decoded messages.
"""
from __future__ import annotations

import math

from metrics import Sample

FRESH_NS = 500_000_000
FUTURE_TOLERANCE_NS = 100_000_000   # carControl/controlsState for the same cycle arrive a few ms after carState
SCHEDULE_TOLERANCE = 0.25    # accept a sample up to 25% of an interval early (log timing jitter)
LANE_CHANGE_OFF = {"off", "None", ""}
RAM_STEER_LIMIT = 350


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

    def ingest(self, service: str, t_ns: int, msg) -> Sample | None:
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
        return Sample(
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
