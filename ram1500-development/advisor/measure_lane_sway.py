"""Lane sway: slow side-to-side drift of the truck between the lane lines on
straight road.

LOG-ONLY. It is recorded and stored; no report line, flag or rule reads it.
It cannot see fast wobble or anything in curves, and about half of a normal
minute's value is the lines moving against each other, not the truck.

Per new model message:

    offset, width   middle of the two lane lines, and the distance between them
    valid           the Sample it came with is within 0.5 s and accepted by the shared
                    gates; both line probabilities at least 0.90; width 2.5 to 4.5 m;
                    and no jump near it: when two valid messages in a row (at most
                    0.25 s apart, 0.75 s on a slow stream) differ by more than 0.30 m
                    in offset or width, every message from 1.0 s before to 2.0 s after
                    the later one is invalid
    scored          8.6 s after its time stamp, against every message received within
                    5.0 s either side: it is valid and on straight road, the valid
                    messages carry at least 80 % of the window's time, the window
                    holds at least 5.0 s, and a valid message lies on each side
    residual        offset less the mean offset of the valid messages in the window

A message stands for the time since the one before it. Everything is booked
when the message is evaluated. A gap releases what waits and starts afresh.

The block of a window (left out when the time of its accepted messages, as
sent, is zero and none was scored):

    n, sumSq           scored messages and the sum of their squared residuals, m^2
    scoredS            their time, s
    validS, eligibleS  time of the valid messages, and of those the shared gates accepted
    vMean              mean speed of the scored messages, m/s
    widthMin/Max, probMin    over the accepted messages: lane width, and the lower line probability

The service turns these into a value in metres (quality_values.py).

The core takes its acceptance flags and its booking key from the caller and
decides neither. It has two entries because a message is judged against the
ones after it: `see` shows it every message once its gate flag is known, `add`
scores a message later, once everything up to 6.0 s after it has been shown.
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure, closing_block, significant
from shared_rules import EVAL_LATE_LANE_S, LANE_HALF_S

LINE_PROB_MIN = 0.90            # both lane lines must be at least this certain
WIDTH_MIN_M = 2.5
WIDTH_MAX_M = 4.5
JUMP_M = 0.30                   # a larger change of offset or width between two messages is a jump
VALID_SHARE = 0.80              # of the window's time must be valid
MIN_WINDOW_S = 5.0              # the window must hold this much received time


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


_HALF_NS = _ns(LANE_HALF_S)
_LATE_NS = _ns(EVAL_LATE_LANE_S)
_PAIR_NS = _ns(0.5)             # a message and the Sample it came with may be this far apart
_JUMP_FAST_NS = _ns(0.25)       # two messages this close together can show a jump ...
_JUMP_SLOW_NS = _ns(0.75)       # ... on a slow stream
_JUMP_BEFORE_NS = _ns(1.0)
_JUMP_AFTER_NS = _ns(2.0)
_FLOAT_SLACK = 1e-9             # sums of message times must not miss a limit by rounding
_TRIM_AT = 64                   # rows behind the window are dropped in lumps of this size

_T, _DT, _OFFSET, _WIDTH, _ACCEPTED, _ALONE_OK, _VALID, _PROB = range(8)
_N, _SUM_SQ, _SCORED_S, _VALID_S, _ELIGIBLE_S, _V_SUM, _WIDTH_MIN, _WIDTH_MAX, _PROB_MIN = range(9)


class LaneSwayCore:
    """The lane-sway rules on their own. Times are log time stamps in nanoseconds.

        see(t_ns, (dt_m, left_y, right_y, left_prob, right_prob, slow), accept_flag)
                       every received message of the run, in time order. `accept_flag`: the gates accept
                       it (the first part of "valid"). `slow`: the stream is a slow one.
        add(t_ns, v, accept_flag, key)
                       score the oldest message not yet scored (`t_ns` must be its time), once every
                       message up to 6.0 s after it has been shown. `accept_flag`: it is on straight
                       road. `v`: the speed that goes with it.
        new_run()      a gap: forget every message
        flush()        {key: block} booked since the last flush
    """

    def __init__(self):
        self._rows = []             # the messages shown, oldest first; rows behind the window are dropped
        self._next = 0              # index of the next message to score
        self._lo = self._hi = 0     # the window of the message being scored is rows[lo:hi]
        self._recv_s = self._valid_s = self._offset_sum = 0.0       # over the window
        self._valid_n = 0
        self._jump_until_ns = None
        self._booked = {}

    @property
    def held(self) -> int:
        return len(self._rows)

    def see(self, t_ns: int, values, accept_flag) -> None:
        dt_m, left_y, right_y, left_prob, right_prob, slow = values
        if left_y is None or right_y is None:
            offset = width = None
        else:
            offset, width = (left_y + right_y) / 2, right_y - left_y
        prob = None if left_prob is None or right_prob is None else min(left_prob, right_prob)
        accepted = bool(accept_flag)
        alone_ok = (accepted and prob is not None and prob >= LINE_PROB_MIN
                    and width is not None and WIDTH_MIN_M <= width <= WIDTH_MAX_M)
        rows = self._rows
        if alone_ok and rows:
            prev = rows[-1]
            if (prev[_ALONE_OK] and t_ns - prev[_T] <= (_JUMP_SLOW_NS if slow else _JUMP_FAST_NS)
                    and (abs(offset - prev[_OFFSET]) > JUMP_M or abs(width - prev[_WIDTH]) > JUMP_M)):
                self._jump_until_ns = t_ns + _JUMP_AFTER_NS
                from_ns = t_ns - _JUMP_BEFORE_NS
                i = len(rows) - 1
                while i >= 0 and rows[i][_T] >= from_ns:
                    self._invalidate(i)
                    i -= 1
        valid = alone_ok and (self._jump_until_ns is None or t_ns > self._jump_until_ns)
        rows.append([t_ns, dt_m, offset, width, accepted, alone_ok, valid, prob])

    def _invalidate(self, i: int) -> None:
        row = self._rows[i]
        if row[_VALID]:
            row[_VALID] = False
            if self._lo <= i < self._hi:            # already counted in the window sums
                self._valid_s -= row[_DT]
                self._offset_sum -= row[_OFFSET]
                self._valid_n -= 1

    def add(self, t_ns: int, v: float, accept_flag, window_key) -> None:
        rows = self._rows
        i = self._next
        if i >= len(rows) or rows[i][_T] != t_ns:
            raise ValueError("messages are scored in the order they were shown")
        self._next = i + 1
        row = rows[i]

        hi_ns, lo_ns = t_ns + _HALF_NS, t_ns - _HALF_NS
        hi = self._hi
        while hi < len(rows) and rows[hi][_T] <= hi_ns:
            entering = rows[hi]
            self._recv_s += entering[_DT]
            if entering[_VALID]:
                self._valid_s += entering[_DT]
                self._offset_sum += entering[_OFFSET]
                self._valid_n += 1
            hi += 1
        self._hi = hi
        lo = self._lo
        while rows[lo][_T] < lo_ns:
            leaving = rows[lo]
            self._recv_s -= leaving[_DT]
            if leaving[_VALID]:
                self._valid_s -= leaving[_DT]
                self._offset_sum -= leaving[_OFFSET]
                self._valid_n -= 1
            lo += 1
        self._lo = lo

        if row[_ACCEPTED]:
            sums = self._booked.get(window_key)
            if sums is None:
                sums = self._booked[window_key] = [0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, None]
            dt_m = row[_DT]
            sums[_ELIGIBLE_S] += dt_m
            width, prob = row[_WIDTH], row[_PROB]
            if width is not None:
                if sums[_WIDTH_MIN] is None or width < sums[_WIDTH_MIN]:
                    sums[_WIDTH_MIN] = width
                if sums[_WIDTH_MAX] is None or width > sums[_WIDTH_MAX]:
                    sums[_WIDTH_MAX] = width
            if prob is not None and (sums[_PROB_MIN] is None or prob < sums[_PROB_MIN]):
                sums[_PROB_MIN] = prob
            if row[_VALID]:
                sums[_VALID_S] += dt_m
                if (accept_flag and self._recv_s + _FLOAT_SLACK >= MIN_WINDOW_S
                        and self._valid_s + _FLOAT_SLACK >= VALID_SHARE * self._recv_s
                        and self._valid_beside(i, lo, -1) and self._valid_beside(i, hi - 1, 1)):
                    residual = row[_OFFSET] - self._offset_sum / self._valid_n
                    sums[_N] += 1
                    sums[_SUM_SQ] += residual * residual
                    sums[_SCORED_S] += dt_m
                    sums[_V_SUM] += v

        if lo >= _TRIM_AT:
            del rows[:lo]
            self._next -= lo
            self._hi -= lo
            self._lo = 0

    def _valid_beside(self, i: int, end: int, step: int) -> bool:
        """True when a valid message lies between row `i` (not counted) and row `end` (counted)."""
        rows = self._rows
        while i != end:
            i += step
            if rows[i][_VALID]:
                return True
        return False

    def new_run(self) -> None:
        self._rows = []
        self._next = self._lo = self._hi = 0
        self._recv_s = self._valid_s = self._offset_sum = 0.0
        self._valid_n = 0
        self._jump_until_ns = None

    def flush(self) -> dict:
        booked, self._booked = self._booked, {}
        return {key: {"n": sums[_N], "sumSq": sums[_SUM_SQ], "scoredS": sums[_SCORED_S], "validS": sums[_VALID_S],
                      "eligibleS": sums[_ELIGIBLE_S], "vMean": sums[_V_SUM] / sums[_N] if sums[_N] else None,
                      "widthMin": sums[_WIDTH_MIN], "widthMax": sums[_WIDTH_MAX], "probMin": sums[_PROB_MIN]}
                for key, sums in booked.items()}


def _digits(value, digits):
    return None if value is None else round(value, digits) + 0.0


def rounded(block: dict) -> dict:
    """The block as it is sent."""
    return {"n": block["n"], "sumSq": significant(block["sumSq"]), "scoredS": round(block["scoredS"], 1),
            "validS": round(block["validS"], 1), "eligibleS": round(block["eligibleS"], 1),
            "vMean": _digits(block["vMean"], 1), "widthMin": _digits(block["widthMin"], 2),
            "widthMax": _digits(block["widthMax"], 2), "probMin": _digits(block["probMin"], 2)}


class LaneSway(Measure):
    name = "lane"

    def __init__(self):
        self._core = LaneSwayCore()
        self._unseen = deque()      # (time ns, message, its Sample): the Sample's gate flag is not known yet
        self._unscored = deque()    # (time ns, its Sample, within 0.5 s): shown to the core, evaluation not due

    @property
    def buffered(self) -> int:
        """Model messages held."""
        return len(self._unseen) + self._core.held

    def on_model(self, ctx, m, g) -> None:
        self._unseen.append((int(round(m.t_m * 1e9)), m, g))

    def on_sample(self, ctx, g) -> None:
        self._advance(ctx, g.t_ns)

    def on_gap(self, ctx) -> None:
        self._advance(ctx, None)    # every flag is final now: release what waits, then start afresh
        self._core.new_run()

    def on_drive_end(self, ctx) -> None:
        self._advance(ctx, None)

    def _advance(self, ctx, now_ns) -> None:
        """Show the core every message whose flag is known; score those that are due (all, when `now_ns` is None)."""
        core, unseen, unscored = self._core, self._unseen, self._unscored
        while unseen:
            t_ns, m, g = unseen[0]
            paired = g.t_ns - t_ns <= _PAIR_NS
            if paired and g.eligible is None and now_ns is not None:
                break               # its Sample is judged 2.0 s after itself
            unseen.popleft()
            core.see(t_ns, (m.dt_m, m.left_y, m.right_y, m.left_prob, m.right_prob, m.slow),
                     paired and g.eligible is True)
            unscored.append((t_ns, g, paired))
        while unscored and (now_ns is None or unscored[0][0] + _LATE_NS <= now_ns):
            t_ns, g, paired = unscored.popleft()
            core.add(t_ns, g.sample.v_ego, paired and g.straight is True, ctx.w)

    def window_fields(self) -> dict:
        block = closing_block(self._core.flush())
        if block is None:
            return {}
        sent = rounded(block)
        # Left out when the time it would show is zero. A scored message is never left out with it.
        return {self.name: sent} if sent["eligibleS"] or sent["n"] else {}
