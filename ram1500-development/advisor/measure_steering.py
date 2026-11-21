"""Steering-on time: how long the truck was driven, how long sunnypilot was
really steering, and how long it was switched on but held at zero below its
start-up speed.

Every number is a sum of the time each Sample stands for, never a count. The
flags come from the GateTracker (gates.py); nothing is worked out twice.

    latOnS         steering switched on
    steeringS      ... and really steering: "steering on" everywhere in the report
    forcedZeroS    ... but held at zero (the truck has not yet reached its start-up speed)
    fzFallbackS    switched on while no alert list was available, so the split
                   above was decided by the speed fallback
    pressedS       switched on with the wheel sensor reading "pressed" (raw)
    driverS        switched on with driver steering (short blips ignored)
    laneChangeS    switched on during a lane change
    movingS        speed above 0.5 m/s: driving time
    cruiseS        ... with the truck's cruise control on
    vMean          mean speed of the window; vMeanSteering: of its steering-on time

The three overlays (pressed, driver, lane change) are reported beside
steering-on time and never taken off it.
"""
from __future__ import annotations

from measure_api import Measure
from shared_rules import MOVING_ABOVE_MS

_SUMS = ("latOnS", "steeringS", "forcedZeroS", "fzFallbackS", "pressedS", "driverS", "laneChangeS", "movingS",
         "cruiseS")


class SteeringTime(Measure):
    name = "steering"
    owner_facing = True

    def __init__(self):
        self._win = dict.fromkeys(_SUMS, 0.0)
        self._win_s = 0.0               # time of the open window: what its mean speed is taken over
        self._v_sum = 0.0               # speed x time, for the mean speed
        self._v_steer_sum = 0.0         # ... over steering-on time only
        self._steering_s = 0.0          # whole drive
        self._moving_s = 0.0

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        dt, v = s.dt, s.v_ego
        win = self._win
        self._win_s += dt
        self._v_sum += v * dt
        if g.lat_on:
            win["latOnS"] += dt
            if s.alerts is None:
                win["fzFallbackS"] += dt
            if s.steering_pressed:
                win["pressedS"] += dt
            if g.driver:
                win["driverS"] += dt
            if s.lane_change_state in (1, 2, 3):
                win["laneChangeS"] += dt
            if g.forced_zero:
                win["forcedZeroS"] += dt
            else:
                win["steeringS"] += dt
                self._steering_s += dt
                self._v_steer_sum += v * dt
        if v > MOVING_ABOVE_MS:
            win["movingS"] += dt
            self._moving_s += dt
            if s.cruise_enabled:
                win["cruiseS"] += dt

    def window_fields(self) -> dict:
        win = self._win
        fields = {key: round(value, 1) for key, value in win.items()}
        fields["vMean"] = round(self._v_sum / self._win_s, 1) if self._win_s > 0 else None
        fields["vMeanSteering"] = round(self._v_steer_sum / win["steeringS"], 1) if win["steeringS"] > 0 else None
        self._win = dict.fromkeys(_SUMS, 0.0)
        self._win_s = self._v_sum = self._v_steer_sum = 0.0
        return fields

    def drive_totals(self) -> dict:
        return {"steeringS": round(self._steering_s, 1), "movingS": round(self._moving_s, 1)}
