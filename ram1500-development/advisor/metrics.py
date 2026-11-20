"""Per-minute Drive Advisor metrics. Counts and numbers only: nothing that
identifies the driver, the place or the vehicle."""
from __future__ import annotations

from dataclasses import dataclass

STRAIGHT_CURVATURE = 0.002   # 1/m: radius 500 m or more counts as straight
CURVE_CURVATURE = 0.002
WEAVE_RATE_DEG_S = 2.0
WEAVE_MIN_SPEED_MS = 15.0
HARD_BRAKE_MPS2 = -3.0
STEER_WARNING = "steerSaturated"


@dataclass(frozen=True)
class Sample:
    t: float
    v_ego: float
    a_ego: float
    steering_rate_deg_s: float
    steering_pressed: bool
    lat_active: bool
    desired_curvature: float | None
    actual_curvature: float | None
    torque_at_limit: bool
    lead_present: bool
    lead_distance_m: float | None
    lane_change: bool
    alerts: frozenset = frozenset()


class MinuteSummary:
    def __init__(self, start_t: float):
        self.start_t = start_t
        self.samples = 0
        self.engaged = 0
        self.straight_engaged = 0
        self.reversals = 0
        self.curve_err_sum = 0.0
        self.curve_n = 0
        self.torque_limit = 0
        self.steer_warnings = 0
        self.lane_changes = 0
        self.overrides = 0
        self.hard_brakes = []
        self.speed_sum = 0.0
        self._rate_sign = 0
        self._prev = None

    def add(self, s: Sample) -> None:
        prev = self._prev
        self.samples += 1
        self.speed_sum += s.v_ego
        if STEER_WARNING in s.alerts and (prev is None or STEER_WARNING not in prev.alerts):
            self.steer_warnings += 1
        if s.lane_change and (prev is None or not prev.lane_change):
            self.lane_changes += 1
        if s.a_ego <= HARD_BRAKE_MPS2 and (prev is None or prev.a_ego > HARD_BRAKE_MPS2):
            self.hard_brakes.append({
                "leadPresent": s.lead_present,
                "leadDistanceM": None if s.lead_distance_m is None else round(s.lead_distance_m, 1),
                "speedMs": round(s.v_ego, 1),
            })
        if s.lat_active:
            self._add_engaged(s, prev)
        self._prev = s

    def _add_engaged(self, s: Sample, prev: Sample | None) -> None:
        self.engaged += 1
        if s.steering_pressed and (prev is None or not prev.steering_pressed):
            self.overrides += 1
        if s.torque_at_limit:
            self.torque_limit += 1
        dc = s.desired_curvature
        if dc is None or s.lane_change:
            return
        if abs(dc) < STRAIGHT_CURVATURE and s.v_ego >= WEAVE_MIN_SPEED_MS:
            self.straight_engaged += 1
            if abs(s.steering_rate_deg_s) >= WEAVE_RATE_DEG_S:
                sign = 1 if s.steering_rate_deg_s > 0 else -1
                if self._rate_sign and sign != self._rate_sign:
                    self.reversals += 1
                self._rate_sign = sign
        if abs(dc) >= CURVE_CURVATURE and s.actual_curvature is not None:
            self.curve_err_sum += abs(dc - s.actual_curvature) / abs(dc)
            self.curve_n += 1

    def finish(self, sample_hz: float) -> dict:
        per_minute = sample_hz * 60.0
        straight_minutes = self.straight_engaged / per_minute
        return {
            "startT": round(self.start_t, 3),
            "samples": self.samples,
            "engagedMinutes": round(self.engaged / per_minute, 2),
            "straightMinutes": round(straight_minutes, 2),
            "weaveReversals": self.reversals,
            "weavePerStraightMinute": (round(self.reversals / straight_minutes, 2)
                                       if straight_minutes >= 0.25 else None),
            "curveSamples": self.curve_n,
            "curveRelError": round(self.curve_err_sum / self.curve_n, 4) if self.curve_n else None,
            "torqueLimitFraction": round(self.torque_limit / self.engaged, 4) if self.engaged else None,
            "steerWarningEpisodes": self.steer_warnings,
            "laneChanges": self.lane_changes,
            "overrideEpisodes": self.overrides,
            "hardBrakes": self.hard_brakes,
            "speedMeanMs": round(self.speed_sum / self.samples, 1) if self.samples else None,
        }
