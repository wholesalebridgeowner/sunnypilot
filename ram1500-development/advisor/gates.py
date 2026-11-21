"""GateTracker: turns Samples into the flags every measure shares.

One per drive. Measures never work these flags out again; each measure only
adds its own evaluation lateness.

    known at once        lat_on, forced_zero, steering, driver, driver_recent,
                         disturbed, at_limit
    known 2.0 s late     clean, eligible
    known 5.05 s late    straight, straight_eligible

The tracker owns the one delay line of Samples. `add` hands back the Samples
whose evaluation the new Sample triggers: those at least 6.0 s old, with every
flag final. A gap Sample and the end of the drive release every waiting Sample
with the data that exists.

Intervals are compared on whole nanoseconds, so "from 2.0 s before to 5.0 s
after, ends included" holds exactly.
"""
from __future__ import annotations

from collections import deque

from shared_rules import (ALERT_BELOW_STEER_SPEED, AT_LIMIT_TORQUE, DRIVER_RECENT_S, EVAL_LATE_S, HOLD_AFTER_S,
                          HOLD_BEFORE_S, LATCH_RESET_GAP_S, LATCH_SPEED_MS, PRESS_MIN_SPAN_S, STRAIGHT_HALF_S,
                          STRAIGHT_MEAN_CURV, STRAIGHT_MIN_COVER_S, V_MIN_QUALITY_MS)


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


_PRESS_MIN_SPAN_NS = _ns(PRESS_MIN_SPAN_S)
_DRIVER_RECENT_NS = _ns(DRIVER_RECENT_S)
_HOLD_AFTER_NS = _ns(HOLD_AFTER_S)
_HOLD_BEFORE_NS = _ns(HOLD_BEFORE_S)
_STRAIGHT_HALF_NS = _ns(STRAIGHT_HALF_S)
_EVAL_LATE_NS = _ns(EVAL_LATE_S)


class Gated:
    """A Sample with its shared flags. The four late flags are None until they are known."""
    __slots__ = ("sample", "t_ns", "lat_on", "forced_zero", "steering", "driver", "driver_recent", "disturbed",
                 "at_limit", "clean", "eligible", "straight", "straight_eligible", "_back_ok")

    def __init__(self, sample, t_ns):
        self.sample = sample
        self.t_ns = t_ns
        self.lat_on = self.forced_zero = self.steering = False
        self.driver = self.driver_recent = self.disturbed = self.at_limit = False
        self.clean = self.eligible = self.straight = self.straight_eligible = None
        self._back_ok = False


class GateTracker:
    def __init__(self):
        self._first = True
        self._latch = None                 # speed latch: None unknown, False not yet at speed, True steering started
        self._press_start_ns = None        # start of the current run of pressed Samples
        self._last_driver_ns = None
        self._last_disturbed_ns = None
        self._disturbed_ns = deque()       # disturbed time stamps a waiting Sample may still have ahead of it
        self._await_clean = deque()        # the delay line, in three stages
        self._await_straight = deque()
        self._await_release = deque()
        self._ahead = deque()              # (t_ns, desired curvature, dt) not yet inside the straight window
        self._inside = deque()             # ... inside it
        self._sum_curv = 0.0
        self._sum_dt = 0.0

    # --- one Sample in ------------------------------------------------------
    def add(self, sample):
        """Returns (flags of this Sample, Samples released for evaluation by it, oldest first)."""
        g = self._flags(sample)
        released = []
        if sample.gap:                     # the gap Sample is the first Sample of a new run
            self._finish_run(released, end_of_data=False)
        self._await_clean.append(g)
        if sample.desired_curvature is not None:
            self._ahead.append((g.t_ns, sample.desired_curvature, sample.dt))
        now_ns = g.t_ns
        while self._await_clean and self._await_clean[0].t_ns + _HOLD_BEFORE_NS <= now_ns:
            self._await_straight.append(self._set_clean(self._await_clean.popleft()))
        while self._await_straight and self._await_straight[0].t_ns + _STRAIGHT_HALF_NS <= now_ns:
            self._await_release.append(self._set_straight(self._await_straight.popleft()))
        while self._await_release and self._await_release[0].t_ns + _EVAL_LATE_NS <= now_ns:
            released.append(self._await_release.popleft())
        return g, released

    def end(self) -> list:
        """Drive end: every waiting Sample, oldest first. The last 2.0 s cannot be clean."""
        released = []
        self._finish_run(released, end_of_data=True)
        return released

    @property
    def waiting(self) -> int:
        """Samples in the delay line."""
        return len(self._await_clean) + len(self._await_straight) + len(self._await_release)

    @property
    def buffered(self) -> int:
        """Values held besides the delay line (straight window, disturbance times)."""
        return len(self._ahead) + len(self._inside) + len(self._disturbed_ns)

    # --- flags known at once ------------------------------------------------
    def _flags(self, sample) -> Gated:
        t_ns = int(round(sample.t * 1e9))
        g = Gated(sample, t_ns)
        lat_on = sample.lat_active is True

        # Fallback for "held at zero" while no alert list is available: a speed latch.
        if sample.gap and sample.raw_dt > LATCH_RESET_GAP_S:
            self._latch = None             # a hole this long hides what the truck did
        if not sample.gear_drive:
            self._latch = False
        elif self._latch is False and sample.v_ego >= LATCH_SPEED_MS:
            self._latch = True
        if sample.alerts is not None:
            forced_zero = lat_on and ALERT_BELOW_STEER_SPEED in sample.alerts
        else:
            forced_zero = lat_on and self._latch is False
        steering = lat_on and not forced_zero

        # Driver steering: pressed Samples in a row that span the minimum time.
        if sample.steering_pressed:
            if self._press_start_ns is None or sample.gap:
                self._press_start_ns = t_ns
            driver = t_ns - self._press_start_ns >= _PRESS_MIN_SPAN_NS
        else:
            self._press_start_ns = None
            driver = False
        if driver:
            self._last_driver_ns = t_ns
        driver_recent = self._last_driver_ns is not None and t_ns - self._last_driver_ns < _DRIVER_RECENT_NS

        disturbed = (self._first or not steering or sample.lane_change_state != 0 or driver or sample.blinker
                     or sample.gap or sample.desired_curvature is None or sample.actual_curvature is None)
        self._first = False
        if disturbed:
            self._last_disturbed_ns = t_ns
            self._disturbed_ns.append(t_ns)

        g.lat_on, g.forced_zero, g.steering = lat_on, forced_zero, steering
        g.driver, g.driver_recent, g.disturbed = driver, driver_recent, disturbed
        g.at_limit = (lat_on and sample.torque_output is not None
                      and abs(sample.torque_output) >= AT_LIMIT_TORQUE)
        g._back_ok = self._last_disturbed_ns is None or t_ns - self._last_disturbed_ns > _HOLD_AFTER_NS
        return g

    # --- flags known late ---------------------------------------------------
    def _set_clean(self, g: Gated, end_of_data: bool = False) -> Gated:
        ahead = self._disturbed_ns
        while ahead and ahead[0] <= g.t_ns:
            ahead.popleft()
        disturbed_soon = bool(ahead) and ahead[0] <= g.t_ns + _HOLD_BEFORE_NS
        g.clean = g._back_ok and not disturbed_soon and not end_of_data
        g.eligible = g.clean and g.sample.v_ego >= V_MIN_QUALITY_MS
        return g

    def _set_straight(self, g: Gated) -> Gated:
        lo, hi = g.t_ns - _STRAIGHT_HALF_NS, g.t_ns + _STRAIGHT_HALF_NS
        ahead, inside = self._ahead, self._inside
        while ahead and ahead[0][0] <= hi:
            item = ahead.popleft()
            inside.append(item)
            self._sum_curv += item[1]
            self._sum_dt += item[2]
        while inside and inside[0][0] < lo:
            item = inside.popleft()
            self._sum_curv -= item[1]
            self._sum_dt -= item[2]
        if not inside:
            self._sum_curv = self._sum_dt = 0.0
        g.straight = (bool(inside) and abs(self._sum_curv / len(inside)) < STRAIGHT_MEAN_CURV
                      and self._sum_dt >= STRAIGHT_MIN_COVER_S)
        g.straight_eligible = g.eligible and g.straight
        return g

    def _finish_run(self, released: list, end_of_data: bool) -> None:
        """Release every waiting Sample with the data that exists, then start afresh.

        At a gap the gap Sample's own time is already among the disturbance
        times, so only the Samples in the 2.0 s before it lose `clean` to it.
        At the end of the drive the Samples still waiting for `clean` are the
        last 2.0 s, and they cannot be clean.
        """
        for g in self._await_clean:
            self._set_clean(g, end_of_data)
        self._await_straight.extend(self._await_clean)
        self._await_clean.clear()
        for g in self._await_straight:
            self._set_straight(g)
        released.extend(self._await_release)
        released.extend(self._await_straight)
        self._await_release.clear()
        self._await_straight.clear()
        self._ahead.clear()
        self._inside.clear()
        self._sum_curv = self._sum_dt = 0.0
