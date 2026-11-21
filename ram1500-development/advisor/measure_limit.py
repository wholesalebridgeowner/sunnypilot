"""Steering warnings and command-at-limit time.

Counts the steering-limit warnings ("Take Control / Turn Exceeds Steering
Limit": a prompt to help steer; steering stays on through it, it is not a
disengagement), and measures how long sunnypilot's steering command sat at its
limit, split by whether driver steering was detected. Losses of steering are
measured separately (measure_steering_loss.py). What is measured is the command that was sent. It is
not what the steering motor delivered, and it is not motor capacity.

Per window, all seconds as sums of the time each Sample stands for:

    limitS             command at its limit (any speed, driver steering or not)
    limitLaneChangeS   ... during a lane change
    limitDriverS       ... with driver steering within the last two seconds
    limitNoneS         ... with neither
    limitUnexpectedS   ... with neither, at or above the start-up speed, on a modest demand
    askS, askNoneS     the command asked for was at its limit (it is reached before the
                       command sent, which is rate-limited); with neither of the above
    warnS, warnEvents  the steering warning was up; warnings, merged over 10 s
    demandMaxNone      largest demand (speed^2 x |desired curvature|) with steering
                       switched on, outside lane changes and driver steering

A limit event collects the Samples of the "neither" kind that lie no more than
10 s apart. When it closes, 10 s after its last Sample or at the end of the
drive, one `limit_event` record is sent, unless the event lasted under 0.4 s.
Nothing is reset at a window boundary: a warning or an event that crosses one
counts once.
"""
from __future__ import annotations

from measure_api import Measure
from shared_rules import (ALERT_STEER_SATURATED, ASK_LIMIT, EVENT_MERGE_S, LATCH_SPEED_MS, LIMIT_EVENT_MIN_S,
                          SHARP_DEMAND_MS2)

_MERGE_NS = int(round(EVENT_MERGE_S * 1e9))
_FLOAT_SLACK = 1e-9         # float rounding must not put four Samples of 0.1 s under 0.4 s,
                            # nor the mean of a steady 14.5 m/s under 14.5
_SUMS = ("limitS", "limitDriverS", "limitLaneChangeS", "limitNoneS", "limitUnexpectedS", "askS", "askNoneS", "warnS")


def _rounded(value, digits):
    """Rounded, never "-0.0"; None stays None."""
    return None if value is None else round(value, digits) + 0.0


class _LimitEvent:
    """Running sums of one limit event; nothing grows with its length."""
    __slots__ = ("last_ns", "alert_since_last", "warned", "limit_s", "driver_torque_sum", "eps_over_cmd_sum",
                 "curve_s", "v_sum", "achieved_sum", "demand_max", "achieved_at_max", "v_at_max")

    def __init__(self):
        self.last_ns = 0
        self.alert_since_last = False       # the warning was up after the last Sample of the event so far
        self.warned = False
        self.limit_s = 0.0
        self.driver_torque_sum = 0.0        # x time, like every sum below
        self.eps_over_cmd_sum = 0.0
        self.curve_s = 0.0                  # time of the Samples with a known curvature
        self.v_sum = 0.0
        self.achieved_sum = 0.0
        self.demand_max = None
        self.achieved_at_max = None
        self.v_at_max = None

    def add(self, s, t_ns: int, demand, warning: bool) -> None:
        dt = s.dt
        self.last_ns = t_ns
        if warning or self.alert_since_last:
            self.warned = True              # the warning fell between the first and the last Sample
            self.alert_since_last = False
        self.limit_s += dt
        # Positive: the driver's torque acts in the direction of the command.
        self.driver_torque_sum += (s.steering_torque if s.torque_output > 0 else -s.steering_torque) * dt
        self.eps_over_cmd_sum += s.steering_torque_eps / s.torque_output * dt
        if demand is None or s.actual_curvature is None:
            return                          # unknown curvature: the time and the two torque means only
        desired = s.desired_curvature
        achieved = s.v_ego * s.v_ego * s.actual_curvature * ((desired > 0) - (desired < 0))
        self.curve_s += dt
        self.v_sum += s.v_ego * dt
        self.achieved_sum += achieved * dt
        if self.demand_max is None or demand > self.demand_max:
            self.demand_max, self.achieved_at_max, self.v_at_max = demand, achieved, s.v_ego

    def record(self) -> dict:
        if self.demand_max is None:         # no curvature was known: nothing to label the event by
            v_mean = achieved_mean = label = None
        else:
            v_mean = self.v_sum / self.curve_s
            achieved_mean = self.achieved_sum / self.curve_s
            if self.demand_max >= SHARP_DEMAND_MS2:
                label = "sharp"
            elif v_mean < LATCH_SPEED_MS - _FLOAT_SLACK:    # below the speed at which steering starts
                label = "slow"
            else:
                label = "unexpected"
        return {"limitS": round(self.limit_s, 1), "vMeanMs": _rounded(v_mean, 1),
                "vAtMaxMs": _rounded(self.v_at_max, 1), "demandMax": _rounded(self.demand_max, 2),
                "achievedAtMax": _rounded(self.achieved_at_max, 2), "achievedMean": _rounded(achieved_mean, 2),
                "driverTorqueMean": int(round(self.driver_torque_sum / self.limit_s)),
                "epsOverCmd": _rounded(self.eps_over_cmd_sum / self.limit_s, 2),
                "warned": self.warned, "label": label}


class SteeringLimit(Measure):
    name = "limit"
    owner_facing = True

    def __init__(self):
        self._win = dict.fromkeys(_SUMS, 0.0)
        self._warn_events = 0
        self._demand_max = None
        self._last_warning_ns = None        # kept for the whole drive, across windows
        self._event = None                  # the open limit event

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        dt, t_ns = s.dt, g.t_ns
        win = self._win

        event = self._event
        if event is not None and t_ns - event.last_ns > _MERGE_NS:
            self._close(ctx)
            event = None

        warning = s.alerts is not None and ALERT_STEER_SATURATED in s.alerts
        if warning:                         # whatever the steering state
            win["warnS"] += dt
            if self._last_warning_ns is None or t_ns - self._last_warning_ns > _MERGE_NS:
                self._warn_events += 1
            self._last_warning_ns = t_ns

        in_lane_change = s.lane_change_state in (1, 2, 3)
        no_driver = not in_lane_change and not g.driver_recent
        demand = None if s.desired_curvature is None else s.v_ego * s.v_ego * abs(s.desired_curvature)

        if g.lat_on:
            if no_driver and demand is not None and (self._demand_max is None or demand > self._demand_max):
                self._demand_max = demand
            if s.actuators_torque is not None and abs(s.actuators_torque) >= ASK_LIMIT:
                win["askS"] += dt
                if no_driver:
                    win["askNoneS"] += dt

        if g.at_limit:
            win["limitS"] += dt
            if in_lane_change:              # first match wins: lane change, driver, neither
                win["limitLaneChangeS"] += dt
            elif g.driver_recent:
                win["limitDriverS"] += dt
            else:
                win["limitNoneS"] += dt
                if demand is not None and demand < SHARP_DEMAND_MS2 and s.v_ego >= LATCH_SPEED_MS:
                    win["limitUnexpectedS"] += dt
                if event is None:
                    event = self._event = _LimitEvent()
                event.add(s, t_ns, demand, warning)
                return
        if warning and event is not None:
            event.alert_since_last = True

    def on_drive_end(self, ctx) -> None:
        if self._event is not None:
            self._close(ctx)

    def _close(self, ctx) -> None:
        event, self._event = self._event, None
        if event.limit_s < LIMIT_EVENT_MIN_S - _FLOAT_SLACK:
            return                          # too short for a record; its seconds stay in limitNoneS
        ctx.emit("limit_event", event.record())

    def window_fields(self) -> dict:
        fields = {key: round(value, 1) for key, value in self._win.items()}
        fields["warnEvents"] = self._warn_events
        fields["demandMaxNone"] = _rounded(self._demand_max, 2)
        self._win = dict.fromkeys(_SUMS, 0.0)
        self._warn_events = 0
        self._demand_max = None
        return fields
