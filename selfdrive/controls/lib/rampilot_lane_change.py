"""Limit the rate of change of requested curvature during a lane change.

controlsd calls this smoother at 100 Hz, before stock curvature clipping, using
the current model action and the previously applied curvature request. Smoothing
starts when the model reports laneChangeStarting or laneChangeFinishing at or
above LC_MIN_SPEED. It continues for LC_TAIL_S after the flag clears. Dropping
below LC_MIN_SPEED immediately clears the tail and passes through the request.
Disabling lateral control also resets the smoother.

The 0.75 m/s³ rate is the candidate selected for an initial supervised test. Replaying recorded model
inputs measures changes to steering requests. The effect on the truck's path,
lane completion time, driver comfort and warnings remains unmeasured.
"""
from __future__ import annotations

LC_MAX_LAT_JERK = 0.75   # m/s^3 while a lane change is active (stock limit everywhere: 5.0)
LC_TAIL_S = 1.0         # the gentler limit stays this long after the lane change ends
LC_MIN_SPEED = 9.0      # m/s (about 20 mph); below this the stock behaviour is unchanged
MIN_SPEED = 1.0         # same floor as drive_helpers.MIN_SPEED
DT_CTRL = 0.01          # controlsd runs at 100 Hz


class LaneChangeSmoother:
    def __init__(self, max_lat_jerk: float = LC_MAX_LAT_JERK, tail_s: float = LC_TAIL_S,
                 min_speed: float = LC_MIN_SPEED):
        self.max_lat_jerk = max_lat_jerk
        self.tail_s = tail_s
        self.min_speed = min_speed
        self._tail = 0.0

    def reset(self) -> None:
        self._tail = 0.0

    @property
    def active(self) -> bool:
        return self._tail > 0.0

    def update(self, in_lane_change: bool, v_ego: float, prev_curvature: float, new_curvature: float,
               dt: float = DT_CTRL) -> float:
        """The request for this cycle: `new_curvature`, rate-limited while a lane change is active or in its tail."""
        if not (v_ego >= self.min_speed):
            self.reset()
            return new_curvature
        if in_lane_change:
            self._tail = self.tail_s
        elif self._tail > 0.0:
            self._tail = max(0.0, self._tail - dt)
        else:
            return new_curvature
        step = self.max_lat_jerk / max(v_ego, MIN_SPEED) ** 2 * dt
        return min(max(new_curvature, prev_curvature - step), prev_curvature + step)
