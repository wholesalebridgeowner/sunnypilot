"""Port-check adapters: the analysts' own gates and booking around the shipped
cores of the four log-only quality measures.

Test support only. Never imported by the agent, never installed on the Comma,
and not among the measures of any build.

Each quality measure was first worked out by an analyst with gates of its own,
and verified that way. The shipped measures use one shared set of gates
instead, so their numbers differ from the verified ones by design. To show
that the cores themselves were ported faithfully, each adapter here feeds the
SAME core with the analyst's acceptance and the analyst's booking; on the
private dataset it must then give the verified numbers again. The cores have
no test switch: the adapters only choose the acceptance flag and the booking
key, as the shipped measures do.

An adapter is a measure like any other, so it runs in the real pipeline and
sees the same Samples and model messages. Give the pipeline ready-made
objects and read their sums afterwards:

    adapter = AnalystSwings()
    pipeline = Pipeline(PipelineConfig(measures=(lambda: adapter,)))
    ...
    blocks = adapter.booked()          # {booking key: block of sums, not rounded}

Analyst gates in short (all on raw signals; "pressed" is the raw wheel sensor,
not the debounced driver flag):

    lane sway     steering switched on, 15 m/s, no lane change for 5.0 s, wheel not pressed and no
                  blinker for 3.0 s. Booked by the minute of the message's own time.
    wheel swings  steering switched on, no lane change, wheel not pressed, each for more than 2.0 s;
                  |desired curvature| under 0.002; 15 m/s. Booked by a key of the Sample's own time.
    lateral RMS   steering switched on, 20 m/s, not pressed, no lane change, no blinker, slow lateral
                  acceleration under 0.30 m/s^2; nothing for 5.0 s after a bad Sample, and the 2.0 s
                  before it are dropped where they lie in the same window. Windows of 60 s from the
                  first Sample, by the Sample's own time.
    curve sums    steering switched on, no lane change, not pressed, curvature known, no gap, each
                  for more than 2.05 s; 15 m/s.
"""
from __future__ import annotations

import math
from collections import deque

from measure_api import Measure
from measure_curve import CurveCore, LagCore
from measure_lane_sway import LaneSwayCore
from measure_lat_rms import LateralRmsCore
from measure_swings import SwingCore


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


_LANE_CHANGE = (1, 2, 3)            # model states that are a lane change (an unknown state is not one here)
_MINUTE_NS = _ns(60.0)
_HALF_SAMPLE_NS = _ns(0.05)         # the analysts counted Samples; half a Sample makes the same cut by time


def minute_of(t_ns: int) -> int:
    """Minute of a time stamp since boot: the analysts' booking for lane sway."""
    return t_ns // _MINUTE_NS


class AnalystLaneSway(Measure):
    name = "portLane"
    _LC_HOLD_NS = _ns(5.0)
    _RAW_HOLD_NS = _ns(3.0)
    _PAIR_NS = _ns(0.5)
    _LATE_NS = _ns(8.6)             # as shipped; the booking key does not depend on it
    V_MIN = 15.0

    def __init__(self, window_key=minute_of):
        self.core = LaneSwayCore()
        self._key = window_key
        self._last_lane_change_ns = self._last_pressed_ns = self._last_blinker_ns = None
        self._unscored = deque()

    def booked(self) -> dict:
        return self.core.flush()

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        if s.steering_pressed:
            self._last_pressed_ns = g.t_ns
        if s.blinker:
            self._last_blinker_ns = g.t_ns
        self._score(g.t_ns)

    def on_model(self, ctx, m, g) -> None:
        t_ns = int(round(m.t_m * 1e9))
        if m.lane_change_state in _LANE_CHANGE:
            self._last_lane_change_ns = t_ns
        s = g.sample
        paired = g.t_ns - t_ns <= self._PAIR_NS
        accept = (paired and s.lat_active is True and s.v_ego >= self.V_MIN
                  and _older(self._last_lane_change_ns, t_ns - self._LC_HOLD_NS)
                  and _older(self._last_pressed_ns, t_ns - self._RAW_HOLD_NS)
                  and _older(self._last_blinker_ns, t_ns - self._RAW_HOLD_NS))
        self.core.see(t_ns, (m.dt_m, m.left_y, m.right_y, m.left_prob, m.right_prob, m.slow), accept)
        self._unscored.append((t_ns, g, paired))

    def on_gap(self, ctx) -> None:
        self._score(None)
        self.core.new_run()

    def on_drive_end(self, ctx) -> None:
        self._score(None)

    def _score(self, now_ns) -> None:
        unscored = self._unscored
        while unscored and (now_ns is None or unscored[0][0] + self._LATE_NS <= now_ns):
            t_ns, g, paired = unscored.popleft()
            self.core.add(t_ns, g.sample.v_ego, paired and g.straight is True, self._key(t_ns))


def _older(last_ns, than_ns) -> bool:
    """True when nothing was flagged at or after `than_ns`."""
    return last_ns is None or last_ns < than_ns


class AnalystSwings(Measure):
    name = "portSwings"
    _HOLD_NS = _ns(2.0)
    STRAIGHT_CURV = 0.002
    V_MIN = 15.0

    def __init__(self, window_key=minute_of):
        self.core = SwingCore()
        self._key = window_key
        self._last_bad_ns = None
        self._flags = deque()           # the qualification of each Sample, until it is evaluated

    def booked(self) -> dict:
        return self.core.flush()

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        if s.gap:
            self._last_bad_ns = None
        bad = s.lat_active is not True or s.lane_change_state in _LANE_CHANGE or s.steering_pressed
        if bad:
            self._last_bad_ns = g.t_ns
        qualified = (not bad and (self._last_bad_ns is None or g.t_ns - self._last_bad_ns > self._HOLD_NS)
                     and s.desired_curvature is not None and abs(s.desired_curvature) < self.STRAIGHT_CURV
                     and s.v_ego >= self.V_MIN)
        self.core.see(g.t_ns, s.steering_angle_deg)
        self._flags.append(qualified)

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        self.core.add(g.t_ns, (s.steering_angle_deg, s.v_ego, s.dt), self._flags.popleft(), self._key(g.t_ns))

    def on_gap(self, ctx) -> None:
        self.core.new_run()


class AnalystLateralRms(Measure):
    name = "portAy"
    _HOLD_NS = _ns(5.0) + _HALF_SAMPLE_NS       # 50 Samples after a bad one are not accepted
    _LOOK_BACK_NS = _ns(2.0) + _HALF_SAMPLE_NS  # the 20 Samples before a bad one are dropped
    V_MIN = 20.0
    SLOW_LIMIT_MS2 = 0.30
    SLOW_GAIN = 1.0 - math.exp(-1.0 / 30.0)     # 0.03278: a 3 s smoothing at ten Samples a second

    def __init__(self):
        self.core = LateralRmsCore()
        self._first_ns = None
        self._last_bad_ns = None
        self._slow = None               # smoothed lateral acceleration of the run; None = no run
        self._held = deque()            # [time ns, values, accepted, window] of the last 2.0 s

    def booked(self) -> dict:
        return self.core.flush()

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        t_ns = g.t_ns
        if self._first_ns is None:
            self._first_ns = t_ns
            self._last_bad_ns = t_ns - _ns(0.1)         # nothing is accepted in the first 5.0 s
        window = (t_ns - self._first_ns) // _MINUTE_NS
        known = s.actual_curvature is not None and s.desired_curvature is not None
        if known:
            a = s.v_ego * s.v_ego * s.actual_curvature
            self._slow = a if self._slow is None else self._slow + self.SLOW_GAIN * (a - self._slow)
        else:
            self._slow = None
        bad = (not known or s.lat_active is not True or s.v_ego < self.V_MIN or s.steering_pressed
               or s.lane_change_state in _LANE_CHANGE or s.blinker or abs(self._slow) >= self.SLOW_LIMIT_MS2)
        held = self._held
        while held and held[0][0] + self._LOOK_BACK_NS <= t_ns:
            self._hand_over(held.popleft())
        if bad:
            self._last_bad_ns = t_ns
            for row in held:                            # everything still held is within the look-back
                if row[3] == window:
                    row[2] = False
        accepted = not bad and t_ns - self._last_bad_ns > self._HOLD_NS
        held.append([t_ns, (s.v_ego, s.actual_curvature, s.desired_curvature, s.dt), accepted, window])

    def _hand_over(self, row) -> None:
        t_ns, values, accepted, window = row
        self.core.add(t_ns, values, accepted, window)

    def _release(self) -> None:
        while self._held:
            self._hand_over(self._held.popleft())

    def on_gap(self, ctx) -> None:
        self._release()
        self.core.new_run()
        self._slow = None

    def on_drive_end(self, ctx) -> None:
        self._release()


class AnalystCurve(Measure):
    name = "portCurve"
    _HOLD_NS = _ns(2.0) + _HALF_SAMPLE_NS       # a clean run of more than 20 Samples
    V_MIN = 15.0

    def __init__(self, window_key=lambda t_ns: 0):
        self.core = CurveCore()
        self.lag_core = LagCore()
        self._key = window_key
        self._last_bad_ns = None

    def booked(self) -> dict:
        return self.core.flush()

    def lag_booked(self) -> dict:
        return self.lag_core.flush()

    def on_gap(self, ctx) -> None:
        self.core.new_run()
        self.lag_core.new_run()

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        t_ns = g.t_ns
        if self._last_bad_ns is None:
            self._last_bad_ns = t_ns - _ns(0.1)         # the run of clean Samples starts with the drive
        bad = (s.lat_active is not True or s.lane_change_state in _LANE_CHANGE or s.steering_pressed
               or s.desired_curvature is None or s.actual_curvature is None or s.gap)
        if bad:
            self._last_bad_ns = t_ns
        clean = not bad and t_ns - self._last_bad_ns > self._HOLD_NS
        usable = clean and s.v_ego >= self.V_MIN
        key = self._key(t_ns)
        self.core.add(t_ns, (s.v_ego, s.desired_curvature, s.actual_curvature, s.dt, g.at_limit, clean), usable, key)
        self.lag_core.add(t_ns, (s.v_ego, s.desired_curvature, s.actual_curvature, s.dt, g.at_limit), usable, key)
