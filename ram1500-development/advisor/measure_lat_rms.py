"""Lateral-motion RMS: how much sideways acceleration the steering produces on
straight road in the 0.1 to 1.0 Hz band, with the same band of the planned
curvature beside it.

LOG-ONLY. It is recorded and stored; no report line, flag or rule reads it.
It is an acceleration, not a position, and "actual" is the curvature worked
out from the steering angle: this truck has no body-motion signal. The two
numbers stand side by side and nothing is concluded from their ratio.

Per Sample, 6.0 s after its time stamp:

    a, ad      speed^2 x actual curvature, speed^2 x desired curvature
    y, yd      each through a 0.10 Hz high-pass and a 1.0 Hz low-pass filter

The filters run on every Sample of a run, accepted or not, fed with the
Samples as they come (they are made for about ten a second; the service does
not score a window whose loop ran far from that). A run starts at the first
Sample after a missing curvature and at every gap, with the high-pass filter
settled on the first value, so a steady cornering force gives no transient.
Only Samples on straight road that the shared gates accept are summed.

The block of a window (left out when no Sample of it was accepted):

    n           accepted Samples
    s           their time, s
    sumSq       sum of y^2, (m/s^2)^2
    planSumSq   sum of yd^2
    vMean       their mean speed, m/s

The service turns these into two RMS values (quality_values.py).

The core takes its acceptance flag and its booking key from the caller and
decides neither.
"""
from __future__ import annotations

from measure_api import Measure, closing_block, significant

# Second-order sections for ten Samples a second: y = b0 x + b1 x1 + b2 x2 - a1 y1 - a2 y2.
HIGH_PASS = (0.95654323, -1.91308645, 0.95654323, -1.91119707, 0.91497583)      # 0.10 Hz: b0, b1, b2, a1, a2
LOW_PASS = (0.06745527, 0.13491055, 0.06745527, -1.14298050, 0.41280160)        # 1.0 Hz

_N, _S, _SQ, _PLAN_SQ, _V = range(5)


class _BandPass:
    """High-pass then low-pass, one value at a time."""
    __slots__ = ("hx1", "hx2", "hy1", "hy2", "lx1", "lx2", "ly1", "ly2")

    def __init__(self, first: float):
        # Settled on the first value: the high-pass has seen it for ever and reads zero.
        self.hx1 = self.hx2 = first
        self.hy1 = self.hy2 = 0.0
        self.lx1 = self.lx2 = self.ly1 = self.ly2 = 0.0

    def step(self, x: float) -> float:
        b0, b1, b2, a1, a2 = HIGH_PASS
        h = b0 * x + b1 * self.hx1 + b2 * self.hx2 - a1 * self.hy1 - a2 * self.hy2
        self.hx2, self.hx1 = self.hx1, x
        self.hy2, self.hy1 = self.hy1, h
        b0, b1, b2, a1, a2 = LOW_PASS
        y = b0 * h + b1 * self.lx1 + b2 * self.lx2 - a1 * self.ly1 - a2 * self.ly2
        self.lx2, self.lx1 = self.lx1, h
        self.ly2, self.ly1 = self.ly1, y
        return y


class LateralRmsCore:
    """The band filters and their sums on their own.

        add(t_ns, (v, actual_curvature, desired_curvature, dt), accept_flag, key)    every Sample, in time order
        new_run()                                                                    a gap: the filters start afresh
        flush()                                                                      {key: block} since the last flush

    A curvature of None ends the run too; that Sample is not summed whatever its flag says.
    """

    def __init__(self):
        self._actual = self._planned = None     # the filters of the run under way
        self._booked = {}

    def add(self, t_ns: int, values, accept_flag, window_key) -> None:
        v, actual, desired, dt = values
        if actual is None or desired is None:
            self._actual = self._planned = None
            return
        a = v * v * actual
        ad = v * v * desired
        if self._actual is None:
            self._actual, self._planned = _BandPass(a), _BandPass(ad)
        y = self._actual.step(a)
        yd = self._planned.step(ad)
        if not accept_flag:
            return
        sums = self._booked.get(window_key)
        if sums is None:
            sums = self._booked[window_key] = [0, 0.0, 0.0, 0.0, 0.0]
        sums[_N] += 1
        sums[_S] += dt
        sums[_SQ] += y * y
        sums[_PLAN_SQ] += yd * yd
        sums[_V] += v

    def new_run(self) -> None:
        self._actual = self._planned = None

    def flush(self) -> dict:
        booked, self._booked = self._booked, {}
        return {key: {"n": sums[_N], "s": sums[_S], "sumSq": sums[_SQ], "planSumSq": sums[_PLAN_SQ],
                      "vMean": sums[_V] / sums[_N]}
                for key, sums in booked.items()}


def rounded(block: dict) -> dict:
    """The block as it is sent."""
    return {"n": block["n"], "s": round(block["s"], 1), "sumSq": significant(block["sumSq"]),
            "planSumSq": significant(block["planSumSq"]), "vMean": round(block["vMean"], 1)}


class LateralRms(Measure):
    name = "ay"

    def __init__(self):
        self._core = LateralRmsCore()

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        self._core.add(g.t_ns, (s.v_ego, s.actual_curvature, s.desired_curvature, s.dt), g.straight_eligible, ctx.w)

    def on_gap(self, ctx) -> None:
        self._core.new_run()

    def window_fields(self) -> dict:
        block = closing_block(self._core.flush())
        return {} if block is None else {self.name: rounded(block)}
