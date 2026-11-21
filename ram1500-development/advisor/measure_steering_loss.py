"""Steering losses: every time sunnypilot's steering switches off while the truck is moving, and what was
happening around that moment.

A loss is a falling edge of the steering-on flag (carControl.latActive, via the GateTracker) at a speed of
at least 5 m/s on the last steering-on Sample, with no data gap between the two Samples. The evidence window runs from 0.3 s before the edge to 0.3 s
after it. One `steering_loss` record is sent per loss:

    cause       the first of these that is in the evidence, in this order:
                brake       the brake pedal was down
                gas         the gas pedal was down
                cruise_off  the truck's cruise was on just before the edge and off inside the window
                gear        the gear left Drive
                fault       the truck reported a steering fault
                blinker     a turn signal was on (blinker pause and lane-change logic act on it)
                driver      driver steering was detected (short blips ignored, see gates.py)
                none        none of the above: an unexplained loss, worth a look
    evidence    every item found, so a brake press with the blinker on shows both
    vMs         speed on the last steering-on Sample
    windowTruncated  a gap or the drive end came before the window closed

What this does not do. The steering-limit warning (steerSaturated) is a warning, not a loss: steering stays
on through it, so it is never a cause here and is counted by measure_limit.py. A steering-wheel press is
evidence, not proof of what the driver meant. The order of the causes decides the label when several are
present; the full evidence list is kept so a reader can judge. In the 2026-11-21 audit of 27 recorded
drives every loss at 5 m/s or more had a brake press within 0.3 s (118 of 118).
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure
from shared_rules import LOSS_MIN_SPEED_MS, LOSS_WINDOW_S

CAUSES = ("brake", "gas", "cruise_off", "gear", "fault", "blinker", "driver", "none")


def _evidence(g) -> set:
    s = g.sample
    found = set()
    if s.brake_pressed:
        found.add("brake")
    if s.gas_pressed:
        found.add("gas")
    if not s.gear_drive:
        found.add("gear")
    if s.steer_fault:
        found.add("fault")
    if s.blinker:
        found.add("blinker")
    if g.driver:
        found.add("driver")
    return found


class SteeringLoss(Measure):
    name = "steeringLoss"
    owner_facing = True

    def __init__(self):
        self._recent = deque()      # (t, evidence) of the last LOSS_WINDOW_S of evaluated Samples
        self._pending = []          # losses whose after-window is still open
        self._prev_on = False
        self._prev_v = 0.0
        self._prev_cruise = False

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        t = s.t
        found = _evidence(g)
        self._recent.append((t, found))
        while self._recent and self._recent[0][0] < t - LOSS_WINDOW_S:
            self._recent.popleft()

        still_open = []
        for loss in self._pending:
            if t > loss["t"] + LOSS_WINDOW_S:
                self._send(ctx, loss, truncated=False)
                continue
            loss["evidence"] |= found
            if loss["cruiseBefore"] and not s.cruise_enabled:
                loss["evidence"].add("cruise_off")
            still_open.append(loss)
        self._pending = still_open

        on = bool(g.lat_on)
        # An edge across a data gap is not attributed: what happened in the unobserved stretch is unknown.
        if self._prev_on and not on and not s.gap and self._prev_v >= LOSS_MIN_SPEED_MS:
            before = set()
            for t_r, f_r in self._recent:
                if t_r >= t - LOSS_WINDOW_S:
                    before |= f_r
            if self._prev_cruise and not s.cruise_enabled:
                before.add("cruise_off")
            self._pending.append({"t": t, "v": self._prev_v, "evidence": before, "cruiseBefore": self._prev_cruise})
        self._prev_on, self._prev_v, self._prev_cruise = on, s.v_ego, s.cruise_enabled

    def _send(self, ctx, loss, *, truncated: bool) -> None:
        evidence = loss["evidence"]
        cause = next((c for c in CAUSES if c in evidence), "none")
        ctx.emit("steering_loss", {"cause": cause, "evidence": sorted(evidence), "vMs": round(loss["v"], 1),
                                   "windowTruncated": truncated})

    def _flush(self, ctx) -> None:
        for loss in self._pending:
            self._send(ctx, loss, truncated=True)
        self._pending = []

    def on_gap(self, ctx) -> None:
        self._flush(ctx)
        self._recent.clear()
        self._prev_on, self._prev_v, self._prev_cruise = False, 0.0, False

    def on_drive_end(self, ctx) -> None:
        self._flush(ctx)
