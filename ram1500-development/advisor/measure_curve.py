"""Curve sums and lag sums: how far actual cornering sat below or above what
was commanded in curves, and the sums from which a response lag can be fitted
later over many drives.

LOG-ONLY. They are recorded and stored; no report line, flag or rule reads
them. "Below commanded" is not "running wide": part of it is the curve entry.
The lag is a steering-angle response, not the quantity the delay learner
works on.

Per Sample, 6.0 s after its time stamp, with x = speed^2 x desired curvature
(the demand) and y = speed^2 x actual curvature, m/s^2:

  curve block (left out when its three times, as sent, are all zero and no curve began)
    s, belowSum, aboveSum      curve Samples (accepted by the shared gates, |x| at least 0.5): their
                               time, and the time-weighted distance of y below and above x
    events                     curves: a curve Sample more than 3.15 s after the one before it
    s04, below04Sum,           the same against the demand 0.4 s earlier, chosen on that earlier
    above04Sum                 demand: the steady shortfall without the curve entry
    lowS, lowBelowSum,         the same as the first line for clean Samples from 5 m/s up to the
    lowAboveSum, lowLimitS     speed floor, and the part of that time with the command at its limit
                               (a command that was not seen adds nothing to it)

  lag block (left out when it holds no Sample)
    s, n                       lag Samples: 0.98 s of accepted Samples in a row with the command seen and
                               off its limit and no more than 0.15 s between them, within 3.05 s of a curve
    syy, sxy[k], sxx[k]        sums of y^2, y x_k and x_k^2, where x_k is the demand 0.1 k s earlier,
                               k = 0 to 10

The earlier demand is read by time stamp: interpolated between the two Samples
around the wanted time, so an uneven loop does not shift it. Interpolating
also smooths the noise the demand carries, at some steps back more than at
others, so a fitted lag still moves a little with the loop period: lags from
loops of different periods are not to be compared to the hundredth of a second.
A gap drops the history. Nothing else is reset, at a gap or at a window
boundary.

The service turns the sums into values over a span (quality_values.py).

The cores take their acceptance flag and their booking key from the caller
and decide neither.
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure, closing_block, significant
from shared_rules import (CURVE_DEMAND_MS2, CURVE_EVENT_GAP_S, CURVE_LOW_MIN_MS, LAG_STEP_S, LAG_STEPS, LAG_TRAIL_S,
                          V_MIN_QUALITY_MS)


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


_HISTORY_NS = _ns(1.3)                  # how far back the earlier demand is kept
_EDGE_NS = _ns(0.02)                    # the oldest Sample in use stands in for a time this much before it
_EVENT_GAP_NS = _ns(CURVE_EVENT_GAP_S)
_PAIR_BACK_NS = _ns(0.4)                # the delayed pair: the demand this long ago
_PAIR_FROM_NS = _PAIR_BACK_NS - _EDGE_NS
_LAG_STEP_NS = _ns(LAG_STEP_S)
_LAG_FROM_NS = LAG_STEPS * _LAG_STEP_NS - _EDGE_NS      # a lag Sample needs a good Sample at least this far back
_LAG_MAX_STEP_NS = _ns(0.15)            # ... and no two Samples since then further apart than this
_LAG_TRAIL_NS = _ns(LAG_TRAIL_S)

_S, _EVENTS, _BELOW, _ABOVE, _S04, _BELOW04, _ABOVE04, _LOW_S, _LOW_LIMIT_S, _LOW_BELOW, _LOW_ABOVE = range(11)
_CURVE_FIELDS = ("s", "events", "belowSum", "aboveSum", "s04", "below04Sum", "above04Sum", "lowS", "lowLimitS",
                 "lowBelowSum", "lowAboveSum")


def _demands(v, desired, actual):
    """(known, x, y): demand and actual lateral acceleration; zeros when a curvature is unknown."""
    if desired is None or actual is None:
        return False, 0.0, 0.0
    return True, v * v * desired, v * v * actual


class CurveCore:
    """Curve sums on their own. Times are log time stamps in nanoseconds.

        add(t_ns, (v, desired_curvature, actual_curvature, dt, at_limit, clean), accept_flag, key)
                       every Sample, in time order. `accept_flag`: accepted for the curve sums.
                       `clean`: accepted for the low-speed line, which adds its own speed band.
        new_run()      a gap: forget the earlier demands
        flush()        {key: block} booked since the last flush
    """

    def __init__(self):
        self._hist = deque()            # (time ns, demand, accepted) of the run, newest last
        self._last_curve_ns = None      # last curve Sample of the drive
        self._booked = {}

    @property
    def held(self) -> int:
        return len(self._hist)

    def add(self, t_ns: int, values, accept_flag, window_key) -> None:
        v, desired, actual, dt, at_limit, clean = values
        known, x, y = _demands(v, desired, actual)
        accepted = bool(accept_flag) and known
        hist = self._hist
        hist.append((t_ns, x, accepted))
        while hist[0][0] < t_ns - _HISTORY_NS:
            hist.popleft()
        is_curve = abs(x) >= CURVE_DEMAND_MS2
        if accepted:
            if is_curve:
                sums = self._sums(window_key)
                e = x - y if x > 0 else y - x
                sums[_S] += dt
                if e > 0:
                    sums[_BELOW] += e * dt
                else:
                    sums[_ABOVE] -= e * dt
                if self._last_curve_ns is None or t_ns - self._last_curve_ns > _EVENT_GAP_NS:
                    sums[_EVENTS] += 1
                self._last_curve_ns = t_ns
            x04 = self._earlier_demand(t_ns)
            if x04 is not None and abs(x04) >= CURVE_DEMAND_MS2:
                sums = self._sums(window_key)
                e = x04 - y if x04 > 0 else y - x04
                sums[_S04] += dt
                if e > 0:
                    sums[_BELOW04] += e * dt
                else:
                    sums[_ABOVE04] -= e * dt
        if clean and known and is_curve and CURVE_LOW_MIN_MS <= v < V_MIN_QUALITY_MS:
            sums = self._sums(window_key)
            e = x - y if x > 0 else y - x
            sums[_LOW_S] += dt
            if e > 0:
                sums[_LOW_BELOW] += e * dt
            else:
                sums[_LOW_ABOVE] -= e * dt
            if at_limit:
                sums[_LOW_LIMIT_S] += dt

    def _sums(self, window_key) -> list:
        sums = self._booked.get(window_key)
        if sums is None:
            sums = self._booked[window_key] = [0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return sums

    def _earlier_demand(self, t_ns: int):
        """The demand 0.4 s ago, or None when the Sample it rests on is missing or was not accepted.

        That Sample is the newest one at least 0.38 s old. When it is younger
        than 0.4 s, its own demand is taken.
        """
        hist = self._hist
        from_ns = t_ns - _PAIR_FROM_NS
        i = len(hist) - 1
        while i >= 0 and hist[i][0] > from_ns:
            i -= 1
        if i < 0 or not hist[i][2]:
            return None
        base_ns, base_x, _ = hist[i]
        want_ns = t_ns - _PAIR_BACK_NS
        if want_ns <= base_ns:
            return base_x
        next_ns, next_x, _ = hist[i + 1]
        return base_x + (next_x - base_x) * ((want_ns - base_ns) / (next_ns - base_ns))

    def new_run(self) -> None:
        self._hist.clear()

    def flush(self) -> dict:
        booked, self._booked = self._booked, {}
        return {key: dict(zip(_CURVE_FIELDS, sums)) for key, sums in booked.items()}


class LagCore:
    """Lag sums on their own. Times are log time stamps in nanoseconds.

        add(t_ns, (v, desired_curvature, actual_curvature, dt, at_limit), accept_flag, key)
                       every Sample, in time order. `at_limit`: the command is at its limit; a
                       Sample with it ends the stretch of good Samples.
        new_run()      a gap: forget the earlier demands
        flush()        {key: block} booked since the last flush
    """

    def __init__(self):
        self._hist = deque()            # (time ns, demand) of the run, newest last
        self._prev_ns = None            # previous Sample of the run
        self._good_from_ns = None       # first Sample of the unbroken stretch of good Samples up to now
        self._last_curve_ns = None      # last curve Sample of the drive
        self._booked = {}

    @property
    def held(self) -> int:
        return len(self._hist)

    def add(self, t_ns: int, values, accept_flag, window_key) -> None:
        v, desired, actual, dt, at_limit = values
        known, x, y = _demands(v, desired, actual)
        accepted = bool(accept_flag) and known
        hist = self._hist
        hist.append((t_ns, x))
        while hist[0][0] < t_ns - _HISTORY_NS:
            hist.popleft()
        prev_ns, self._prev_ns = self._prev_ns, t_ns
        if accepted and abs(x) >= CURVE_DEMAND_MS2:
            self._last_curve_ns = t_ns
        if not accepted or at_limit:
            self._good_from_ns = None
            return
        if self._good_from_ns is None or t_ns - prev_ns > _LAG_MAX_STEP_NS:
            self._good_from_ns = t_ns   # a stretch may start after a long step, not go on across one
        if t_ns - self._good_from_ns < _LAG_FROM_NS:
            return
        if self._last_curve_ns is None or t_ns - self._last_curve_ns > _LAG_TRAIL_NS:
            return

        sums = self._booked.get(window_key)
        if sums is None:
            sums = self._booked[window_key] = [0.0, 0, 0.0, [0.0] * (LAG_STEPS + 1), [0.0] * (LAG_STEPS + 1)]
        sums[0] += dt
        sums[1] += 1
        sums[2] += y * y
        sxy, sxx = sums[3], sums[4]
        # The demand at each step back, newest first. The oldest Sample in use is the newest one at least
        # 0.98 s old; it also stands in for the last step when that falls just before it.
        rows = list(hist)
        oldest_ns = t_ns - _LAG_FROM_NS
        i = len(rows) - 1
        for k in range(LAG_STEPS + 1):
            want_ns = t_ns - k * _LAG_STEP_NS
            while rows[i][0] > want_ns and rows[i][0] > oldest_ns:
                i -= 1
            base_ns, base_x = rows[i]
            if want_ns <= base_ns or i == len(rows) - 1:
                xk = base_x
            else:
                next_ns, next_x = rows[i + 1]
                xk = base_x + (next_x - base_x) * ((want_ns - base_ns) / (next_ns - base_ns))
            sxy[k] += y * xk
            sxx[k] += xk * xk

    def new_run(self) -> None:
        self._hist.clear()
        self._prev_ns = None
        self._good_from_ns = None

    def flush(self) -> dict:
        booked, self._booked = self._booked, {}
        return {key: {"s": sums[0], "n": sums[1], "syy": sums[2], "sxy": sums[3], "sxx": sums[4]}
                for key, sums in booked.items()}


def rounded_curve(block: dict) -> dict:
    """The curve block as it is sent: seconds to one decimal, the sums (m/s) to four."""
    return {key: value if key == "events" else round(value, 1 if key in ("s", "s04", "lowS", "lowLimitS") else 4) + 0.0
            for key, value in block.items()}


def rounded_lag(block: dict) -> dict:
    """The lag block as it is sent: the sums to five significant digits."""
    return {"s": round(block["s"], 1), "n": block["n"], "syy": significant(block["syy"]),
            "sxy": [significant(value) for value in block["sxy"]],
            "sxx": [significant(value) for value in block["sxx"]]}


class CurveSums(Measure):
    name = "curve"

    def __init__(self):
        self._core = CurveCore()

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        self._core.add(g.t_ns, (s.v_ego, s.desired_curvature, s.actual_curvature, s.dt, g.at_limit, g.clean),
                       g.eligible, ctx.w)

    def on_gap(self, ctx) -> None:
        self._core.new_run()

    def window_fields(self) -> dict:
        block = closing_block(self._core.flush())
        if block is None:
            return {}
        sent = rounded_curve(block)
        # Left out when the times it would show are all zero. The start of a curve is never left out with it.
        return {self.name: sent} if sent["s"] or sent["s04"] or sent["lowS"] or sent["events"] else {}


class CurveLag(Measure):
    name = "lag"

    def __init__(self):
        self._core = LagCore()

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        # A command that was not seen is not known to be off its limit: that Sample ends the stretch too.
        limit = g.at_limit or s.torque_output is None
        self._core.add(g.t_ns, (s.v_ego, s.desired_curvature, s.actual_curvature, s.dt, limit), g.eligible, ctx.w)

    def on_gap(self, ctx) -> None:
        self._core.new_run()

    def window_fields(self) -> dict:
        block = closing_block(self._core.flush())
        return {} if block is None else {self.name: rounded_lag(block)}
