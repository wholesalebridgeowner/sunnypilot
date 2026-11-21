"""Wheel swings: back-and-forth movements of the steering wheel on straight
road, scaled so that a count means the same at any speed.

LOG-ONLY. It is recorded and stored; no report line, flag or rule reads it. It
is not a weaving measure, and it is blind to anything slower than 10 to 16 s.

Per Sample, 6.0 s after its time stamp:

    trend      mean wheel angle of every Sample of the run within 5.05 s
               either side, by time stamp (qualified or not)
    residual   (wheel angle - trend) x (speed / 25 m/s)^2
    swing      the residual has come back by 1.0 from the furthest point it
               reached in the present direction. The first move only sets the
               direction. A full left-right-left cycle is two swings.

Only Samples on straight road that the shared gates accept take part; any
other Sample empties the swing state. A gap ends the run: the wheel angles
held and the swing state are dropped. Nothing is reset at a window boundary.

The block of a window (left out when no Sample of it took part):

    n          swings counted
    straightS  time of the Samples that took part, s
    vMean      their mean speed, m/s

The service turns these into swings per straight minute (quality_values.py).

The core takes its acceptance flag and its booking key from the caller and
decides neither. It has two entries because the trend looks ahead: `see`
shows it every wheel angle at once, `add` evaluates a Sample later.
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure, closing_block
from shared_rules import SWING_TREND_HALF_S

SWING_AMPLITUDE = 1.0           # scaled degrees the residual must come back by
SWING_REF_SPEED_MS = 25.0       # the residual is scaled by (speed / this)^2

_N, _S, _VS = range(3)


class CentredMean:
    """Mean of the values within `half_ns` either side of a time, ends included.

    `push` every value in time order, then ask `at` for times in order too:
    each call drops what lies before its window for good.
    """

    def __init__(self, half_ns: int):
        self._half_ns = half_ns
        self._ahead = deque()       # (time ns, value) not yet inside the window
        self._inside = deque()
        self._sum = 0.0

    @property
    def held(self) -> int:
        return len(self._ahead) + len(self._inside)

    def push(self, t_ns: int, value: float) -> None:
        self._ahead.append((t_ns, value))

    def at(self, t_ns: int):
        """Mean over the window around `t_ns`; None when it holds nothing."""
        lo, hi = t_ns - self._half_ns, t_ns + self._half_ns
        ahead, inside = self._ahead, self._inside
        while ahead and ahead[0][0] <= hi:
            item = ahead.popleft()
            inside.append(item)
            self._sum += item[1]
        while inside and inside[0][0] < lo:
            self._sum -= inside.popleft()[1]
        if not inside:
            self._sum = 0.0         # rounding left over from a long run goes with the last value
            return None
        return self._sum / len(inside)

    def clear(self) -> None:
        self._ahead.clear()
        self._inside.clear()
        self._sum = 0.0


class SwingCore:
    """The swing counter on its own. Times are log time stamps in nanoseconds.

        see(t_ns, angle)                                  every Sample of the run, at once, in time order
        add(t_ns, (angle, v, dt), accept_flag, key)       the same Samples, in time order, once every Sample
                                                          up to 5.05 s after them has been shown
        new_run()                                         a gap: forget the wheel angles and the swing state
        flush()                                           {key: block} booked since the last flush
    """

    def __init__(self):
        self._trend = CentredMean(int(round(SWING_TREND_HALF_S * 1e9)))
        self._empty = True          # no residual held: the next accepted Sample starts afresh
        self._dir = 0               # 0 no direction yet, +1 rising, -1 falling
        self._hi = self._lo = 0.0   # furthest residual reached
        self._booked = {}

    @property
    def held(self) -> int:
        """Wheel angles held for the trend."""
        return self._trend.held

    def see(self, t_ns: int, angle: float) -> None:
        self._trend.push(t_ns, angle)

    def add(self, t_ns: int, values, accept_flag, window_key) -> None:
        angle, v, dt = values
        trend = self._trend.at(t_ns)        # asked for every Sample: it moves the window along
        if not accept_flag:
            self._empty = True
            return
        scale = v / SWING_REF_SPEED_MS
        x = 0.0 if trend is None else (angle - trend) * scale * scale
        sums = self._booked.get(window_key)
        if sums is None:
            sums = self._booked[window_key] = [0, 0.0, 0.0]
        sums[_S] += dt
        sums[_VS] += v * dt
        if self._empty:
            self._hi = self._lo = x
            self._dir = 0
            self._empty = False
        elif self._dir == 0:
            if x > self._hi:
                self._hi = x
            if x < self._lo:
                self._lo = x
            if self._hi - x >= SWING_AMPLITUDE:
                self._dir = -1
                self._lo = x
            elif x - self._lo >= SWING_AMPLITUDE:
                self._dir = 1
                self._hi = x
        elif self._dir > 0:
            if x > self._hi:
                self._hi = x
            elif self._hi - x >= SWING_AMPLITUDE:
                sums[_N] += 1
                self._dir = -1
                self._lo = x
        else:
            if x < self._lo:
                self._lo = x
            elif x - self._lo >= SWING_AMPLITUDE:
                sums[_N] += 1
                self._dir = 1
                self._hi = x

    def new_run(self) -> None:
        self._trend.clear()
        self._empty = True

    def flush(self) -> dict:
        booked, self._booked = self._booked, {}
        return {key: {"n": sums[_N], "straightS": sums[_S], "vMean": sums[_VS] / sums[_S] if sums[_S] > 0 else None}
                for key, sums in booked.items()}


def rounded(block: dict) -> dict:
    """The block as it is sent."""
    v_mean = block["vMean"]
    return {"n": block["n"], "straightS": round(block["straightS"], 1),
            "vMean": None if v_mean is None else round(v_mean, 1)}


class WheelSwings(Measure):
    name = "swings"

    def __init__(self):
        self._core = SwingCore()

    def on_sample(self, ctx, g) -> None:
        self._core.see(g.t_ns, g.sample.steering_angle_deg)

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        self._core.add(g.t_ns, (s.steering_angle_deg, s.v_ego, s.dt), g.straight_eligible, ctx.w)

    def on_gap(self, ctx) -> None:
        self._core.new_run()

    def window_fields(self) -> dict:
        block = closing_block(self._core.flush())
        return {} if block is None else {self.name: rounded(block)}
