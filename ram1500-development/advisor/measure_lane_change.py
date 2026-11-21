"""Lane changes: counted by how they ended, and one record of how each one went.

Only lane changes made with steering on exist here: the lane-change state
comes from the driving model, which the build holds at "off" whenever
steering is not active.

Counting runs on every new model message, in time order:

    blinker only   the state goes off -> blinker (pre) -> off with no lane change between
    attempt        starts where the state turns to "starting"; ends at the first message
                   that is neither starting nor finishing, or where the next attempt starts
    completed      the attempt reached "finishing" and stayed there long enough
    cancelled      every other attempt that could be followed to its end ("ended early")
    truncated      the model stream had a gap while the attempt was open, or the drive ended
                   in it: counted apart, never as completed or cancelled, and no record

After a gap in the stream, and at the start of the data, nothing opens until
an "off" or blinker message has been seen: where a lane change started is not
known then. A message whose state cannot be read breaks the stream like a gap.

Per window: lcStarts (= lcCompleted + lcCancelled), lcCompleted, lcCancelled,
lcBlinkerOnly, lcHandsOn (completed ones with driver steering detected),
lcTruncated. Each is booked when the attempt ends, when the blinker state
returns to off, or when the gap shows.

One `lane_change` record per completed or cancelled attempt. Its numbers are
for the log only; nothing in the report reads them:

    outcome, dir (L / R), vStartMs, startingS (start to "finishing")
    pushTorqueMax    largest driver torque toward the new lane around the start
    pressedShare     share of the Samples from 1.0 s after the start to the end with the
    handsOn          wheel sensor reading "pressed"; hands on above a tenth
    crossed, crossS  the lane centre jumped (the lines became those of the new lane), and when
    peakLatAccel     largest change of lateral acceleration against its level before the start
    settleState      settled / not_settled, or why there is no settle figure
    settleS          time from the crossing until the truck stays within 0.25 m of where it ends up
    overshootM       how far it swung past that position

The record is sent when it is complete: 8 s after the crossing for a completed
lane change on a full-rate stream, otherwise 3 s after the attempt's end.
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure
from shared_rules import MODEL_GAP_SLOW_S

OFF, PRE, STARTING, FINISHING = 0, 1, 2, 3     # lane-change states, as in samples.py
LEFT, RIGHT = 1, 2                             # lane-change directions

HANDS_ON_PERCENT = 10       # pressed share above this: driver steering during the lane change
CROSS_JUMP_FAST_M = 1.2     # jump of the lane centre that marks the line crossing (full-rate stream)
CROSS_JUMP_SLOW_M = 1.0     # ... on a slow stream
SETTLE_BAND_M = 0.25        # settled: within this of the final position
LINE_PROB_MIN = 0.5         # both lane lines at least this sure: a confident message
LAT_ON_MIN_PERCENT = 98     # steering must be on for this share of the settle window's Samples


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


_FINISH_MIN_FAST_NS = _ns(0.7)      # least finishing time of a completed lane change (the phase lasts 1.00 s)
_FINISH_MIN_SLOW_NS = _ns(0.45)
_LOOK_BACK_FAST_NS = _ns(1.0)       # the jump is measured against the highest point this far back
_LOOK_BACK_SLOW_NS = _ns(1.6)
_HISTORY_NS = _ns(3.5)              # kept before a start
_PUSH_BEFORE_NS = _ns(1.0)          # the push is looked for from this long before the start ...
_PUSH_AFTER_NS = _ns(0.2)           # ... to this long after it
_BODY_FROM_NS = _ns(1.0)            # the pressed share leaves out the push: it starts this long after the start
_CROSS_AFTER_NS = _ns(2.0)          # the crossing is looked for until this long after the end
_BASE_FROM_NS = _ns(3.0)            # level before the lane change: from this long before the start ...
_BASE_TO_NS = _ns(1.0)              # ... to this long before it
_PEAK_AFTER_NS = _ns(3.0)           # the peak is looked for until this long after the end
_SETTLE_NS = _ns(8.0)               # the truck is followed this long after the crossing
_FINAL_FROM_NS = _ns(5.0)           # final position: from this long after the crossing to the end of that window
_DATA_END_NS = _ns(0.6)             # the model stream may end this long before the window does
_SILENT_NS = _ns(MODEL_GAP_SLOW_S)  # no model message for longer than this is a gap at any stream speed

_COUNTS = ("lcStarts", "lcCompleted", "lcCancelled", "lcBlinkerOnly", "lcHandsOn", "lcTruncated")
_T = 0
_TORQUE, _PRESSED, _LAT_ON, _ACCEL = 1, 2, 3, 4      # Sample rows
_STATE, _CENTRE, _CONFIDENT, _GAP = 1, 2, 3, 4       # model-message rows


def _rounded(value, digits):
    """Rounded, never "-0.0"; None stays None."""
    return None if value is None else round(value, digits) + 0.0


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


class _Attempt:
    __slots__ = ("t_start", "t_fin", "t_end", "left", "right", "v_start", "slow", "completed", "pressed_share",
                 "hands_on", "next_start", "due", "searched", "crossed", "t_cross")

    def __init__(self, t_start: int, v_start: float):
        self.t_start, self.v_start = t_start, v_start
        self.t_fin = self.t_end = None
        self.left = self.right = 0          # messages in the starting state that reported this direction
        self.slow = self.completed = self.hands_on = False
        self.pressed_share = 0.0
        self.next_start = None              # start of the next attempt
        self.due = None                     # the record is complete once every message up to here is in
        self.searched = self.crossed = False
        self.t_cross = None

    @property
    def sign(self):
        """+1 toward the left, -1 toward the right; None when no direction was reported."""
        if not self.left and not self.right:
            return None
        return 1 if self.left >= self.right else -1


class LaneChanges(Measure):
    name = "laneChange"
    owner_facing = True

    def __init__(self):
        self._samples = deque()             # (time ns, driver torque, pressed, steering switched on, lateral accel.)
        self._msgs = deque()                # (time ns, state, lane centre, confident, gap before it)
        self._prev_state = None             # state of the previous message; None = not known
        self._armed = False                 # an off or blinker message has been seen since the last break
        self._pre_pending = False           # in a blinker stretch that came from off and has not started
        self._open = None                   # the attempt under way
        self._waiting = []                  # ended attempts whose record is not complete yet, oldest first
        self._last_sample_ns = None
        self._last_model_ns = None
        self._win = dict.fromkeys(_COUNTS, 0)
        self._truncated = 0                 # whole drive

    @property
    def buffered(self) -> int:
        """Samples and model messages held."""
        return len(self._samples) + len(self._msgs)

    # --- every Sample ---------------------------------------------------------
    def on_sample(self, ctx, g) -> None:
        seen_ns = self._last_sample_ns
        if seen_ns is not None:
            # Every Sample and every model message stamped up to the previous Sample is in by now.
            if self._waiting:
                self._send_complete(ctx, seen_ns)
            if self._open is not None and seen_ns - self._last_model_ns > _SILENT_NS:
                self._break_stream()        # whatever message comes next, it comes after a gap
        s = g.sample
        accel = None if s.actual_curvature is None else s.v_ego * s.v_ego * s.actual_curvature
        self._samples.append((g.t_ns, s.steering_torque, s.steering_pressed, g.lat_on, accel))
        self._last_sample_ns = g.t_ns
        if self._waiting:
            keep_ns = self._waiting[0].t_start
        elif self._open is not None:
            keep_ns = self._open.t_start
        else:
            keep_ns = g.t_ns
        keep_ns -= _HISTORY_NS
        for rows in (self._samples, self._msgs):
            while rows and rows[0][_T] < keep_ns:
                rows.popleft()

    # --- every new model message ------------------------------------------------
    def on_model(self, ctx, m, g) -> None:
        t_ns = int(round(m.t_m * 1e9))
        state = m.lane_change_state
        broken = m.gap or state is None
        if broken:
            self._break_stream()
        centre = None if m.left_y is None or m.right_y is None else (m.left_y + m.right_y) / 2
        confident = (centre is not None and m.left_prob is not None and m.right_prob is not None
                     and m.left_prob >= LINE_PROB_MIN and m.right_prob >= LINE_PROB_MIN)
        self._msgs.append((t_ns, state, centre, confident, broken))
        self._last_model_ns = t_ns
        if state is None:
            return

        attempt = self._open
        if attempt is not None:
            if attempt.t_fin is None:
                if state == FINISHING:
                    attempt.t_fin = t_ns
                elif state != STARTING:
                    self._end(attempt, t_ns, m.slow)
            elif state != FINISHING:        # also finishing -> starting: the next attempt starts on this message
                self._end(attempt, t_ns, m.slow)

        win = self._win
        if state == STARTING:
            self._pre_pending = False
            if self._open is None and self._armed and self._prev_state != STARTING:
                for waiting in self._waiting:
                    if waiting.next_start is None:
                        waiting.next_start = t_ns
                self._open = _Attempt(t_ns, g.sample.v_ego)
            if self._open is not None:
                if m.lane_change_direction == LEFT:
                    self._open.left += 1
                elif m.lane_change_direction == RIGHT:
                    self._open.right += 1
        elif state == PRE:
            if self._prev_state == OFF:
                self._pre_pending = True
        elif state == OFF:
            if self._pre_pending:
                win["lcBlinkerOnly"] += 1
            self._pre_pending = False
        else:
            self._pre_pending = False
        if state == OFF or state == PRE:
            self._armed = True
        self._prev_state = state

    def _break_stream(self) -> None:
        """A gap in the model stream: the open attempt cannot be followed to its end."""
        if self._open is not None:
            self._open = None
            self._win["lcTruncated"] += 1
            self._truncated += 1
        self._pre_pending = False
        self._armed = False
        self._prev_state = None

    def _end(self, attempt: _Attempt, t_end: int, slow: bool) -> None:
        attempt.t_end, attempt.slow = t_end, slow
        finish_min_ns = _FINISH_MIN_SLOW_NS if slow else _FINISH_MIN_FAST_NS
        attempt.completed = attempt.t_fin is not None and t_end - attempt.t_fin >= finish_min_ns
        body_from = attempt.t_start + _BODY_FROM_NS
        n = pressed = 0
        for row in reversed(self._samples):
            if row[_T] < body_from:
                break
            if row[_T] <= t_end:
                n += 1
                pressed += row[_PRESSED]
        attempt.pressed_share = pressed / n if n else 0.0
        attempt.hands_on = pressed * 100 > HANDS_ON_PERCENT * n
        win = self._win
        win["lcStarts"] += 1
        if attempt.completed:
            win["lcCompleted"] += 1
            if attempt.hands_on:
                win["lcHandsOn"] += 1
        else:
            win["lcCancelled"] += 1
        attempt.due = t_end + _PEAK_AFTER_NS
        self._open = None
        self._waiting.append(attempt)

    # --- records ------------------------------------------------------------------
    def _send_complete(self, ctx, seen_ns: int) -> None:
        still = []
        for attempt in self._waiting:
            if attempt.due <= seen_ns and not attempt.searched:
                self._find_crossing(attempt)
                if attempt.completed and attempt.crossed and not attempt.slow:
                    attempt.due = attempt.t_cross + _SETTLE_NS      # may already be past: then it goes out now
            if attempt.due <= seen_ns:
                ctx.emit("lane_change", self._record(attempt, self._settle(attempt)))
            else:
                still.append(attempt)
        self._waiting = still

    def on_drive_end(self, ctx) -> None:
        if self._open is not None:
            self._break_stream()            # an open attempt is truncated and not counted otherwise
        last_ns = self._last_sample_ns
        for attempt in self._waiting:
            cut = False
            if not attempt.searched:
                self._find_crossing(attempt)
                cut = last_ns < attempt.t_end + _CROSS_AFTER_NS
            if attempt.completed and not attempt.slow and (
                    last_ns < attempt.t_cross + _SETTLE_NS if attempt.crossed else cut):
                settle = ("not_measurable:drive_end", None, None)      # still waiting for its settle window
            else:
                settle = self._settle(attempt)
            ctx.emit("lane_change", self._record(attempt, settle))
        self._waiting = []

    def _find_crossing(self, attempt: _Attempt) -> None:
        """Line crossing: the largest fall of the lane centre (counted toward the new lane) below its highest
        point of the look-back time, among the messages from the start to 2 s after the end."""
        attempt.searched = True
        sign = attempt.sign
        if sign is None:
            return
        look_back_ns = _LOOK_BACK_SLOW_NS if attempt.slow else _LOOK_BACK_FAST_NS
        first, last = attempt.t_start - look_back_ns, attempt.t_end + _CROSS_AFTER_NS
        rows = [(m[_T], sign * m[_CENTRE]) for m in self._msgs if m[_CENTRE] is not None and first <= m[_T] <= last]
        deepest, t_cross = 0.0, None
        for j, (t_j, x_j) in enumerate(rows):
            if t_j < attempt.t_start:
                continue
            top = x_j
            for i in range(j - 1, -1, -1):
                if rows[i][0] < t_j - look_back_ns:
                    break
                if rows[i][1] > top:
                    top = rows[i][1]
            if x_j - top < deepest:
                deepest, t_cross = x_j - top, t_j
        if t_cross is not None and deepest <= -(CROSS_JUMP_SLOW_M if attempt.slow else CROSS_JUMP_FAST_M):
            attempt.crossed, attempt.t_cross = True, t_cross

    def _settle(self, attempt: _Attempt) -> tuple:
        """(settleState, settle time in s, overshoot in m). The first failing test, in this order, names the reason."""
        if not attempt.completed or not attempt.crossed:
            return "no_crossing", None, None
        if attempt.slow:
            return "slow_stream", None, None
        t_cross = attempt.t_cross
        window_end = t_cross + _SETTLE_NS
        msgs = [m for m in self._msgs if attempt.t_start <= m[_T] <= window_end]
        if (not msgs or msgs[-1][_T] < window_end - _DATA_END_NS
                or any(m[_GAP] for m in msgs if m[_T] > attempt.t_start)):
            return "not_measurable:data", None, None
        if any(m[_STATE] in (STARTING, FINISHING) for m in msgs if m[_T] > attempt.t_end):
            return "not_measurable:next_lane_change", None, None
        n = lat_on = 0
        for row in self._samples:
            if attempt.t_start <= row[_T] <= window_end:
                n += 1
                lat_on += row[_LAT_ON]
        if n == 0 or lat_on * 100 < LAT_ON_MIN_PERCENT * n:
            return "not_measurable:steering_off", None, None
        final_from = t_cross + _FINAL_FROM_NS
        after = [m for m in msgs if m[_T] >= t_cross]
        confident = [m for m in after if m[_CONFIDENT]]
        final = [m for m in confident if m[_T] >= final_from]
        if (2 * len(confident) < len(after) or len(final) < 2
                or 2 * len(final) < sum(1 for m in after if m[_T] >= final_from)):
            return "not_measurable:lines", None, None

        sign = attempt.sign
        x_final = _median([sign * m[_CENTRE] for m in final])
        offsets = [(m[_T], sign * m[_CENTRE] - x_final) for m in confident]
        overshoot = max(0.0, max(e for _, e in offsets))
        last_outside = None
        for i, (_, e) in enumerate(offsets):
            if abs(e) > SETTLE_BAND_M:
                last_outside = i
        if last_outside is None:
            return "settled", 0.0, overshoot
        if offsets[last_outside][0] >= final_from or last_outside + 1 == len(offsets):
            return "not_settled", None, overshoot
        return "settled", (offsets[last_outside + 1][0] - t_cross) / 1e9, overshoot

    def _record(self, attempt: _Attempt, settle: tuple) -> dict:
        sign = attempt.sign
        t_start = attempt.t_start
        push = peak = None
        if sign is not None:
            pushes = [sign * row[_TORQUE] for row in self._samples
                      if t_start - _PUSH_BEFORE_NS <= row[_T] <= t_start + _PUSH_AFTER_NS]
            if pushes:
                push = int(round(max(pushes)))
        if attempt.completed and attempt.crossed:
            level = [row[_ACCEL] for row in self._samples
                     if row[_ACCEL] is not None and t_start - _BASE_FROM_NS <= row[_T] <= t_start - _BASE_TO_NS]
            until = attempt.t_end + _PEAK_AFTER_NS
            if attempt.next_start is not None and attempt.next_start < until:
                until = attempt.next_start
            if level:
                base = _median(level)
                changes = [abs(row[_ACCEL] - base) for row in self._samples
                           if row[_ACCEL] is not None and t_start <= row[_T] <= until]
                if changes:
                    peak = max(changes)
        state, settle_s, overshoot = settle
        return {"outcome": "completed" if attempt.completed else "cancelled",
                "dir": None if sign is None else ("L" if sign > 0 else "R"),
                "handsOn": attempt.hands_on, "pressedShare": round(attempt.pressed_share, 2),
                "pushTorqueMax": push, "vStartMs": round(attempt.v_start, 1),
                "crossed": attempt.crossed,
                "crossS": round((attempt.t_cross - t_start) / 1e9, 2) if attempt.crossed else None,
                "startingS": None if attempt.t_fin is None else round((attempt.t_fin - t_start) / 1e9, 2),
                "peakLatAccel": _rounded(peak, 2), "settleState": state,
                "settleS": _rounded(settle_s, 2), "overshootM": _rounded(overshoot, 2)}

    # --- summaries ------------------------------------------------------------------
    def window_fields(self) -> dict:
        fields, self._win = self._win, dict.fromkeys(_COUNTS, 0)
        return fields

    def drive_end_fields(self) -> dict:
        return {"truncatedLaneChanges": self._truncated}
