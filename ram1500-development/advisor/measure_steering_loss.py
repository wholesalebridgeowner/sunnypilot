"""Steering losses: every time sunnypilot's steering switches off while the truck is moving, and what was
happening around that moment.

A loss requires adjacent Samples with observed carControl.latActive values of True then False at a speed of
at least 5 m/s on the last steering-on Sample. A break in either the carState samples or the delivered
control observations interrupts edge detection. Live and decimated inputs retain the newest control
per tick. The evidence window extends 0.3 s before and after the edge. One `steering_loss` record is sent per loss:

    cause       the first of these that is in the evidence, in this order:
                brake       the brake pedal was down
                main_off    cruise availability (carState.cruiseState.available) went from on to off inside
                            the window, both readings observed on adjacent Samples with valid CAN data
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

Why main_off comes before gas. On the owner's truck MADS ties steering to the cruise main switch
(MadsMainCruiseAllowed=1, MadsUnifiedEngagementMode=1), so switching the main off drops steering even with
cruise not engaged. Case: route 401, segment 13, at 48 mph with the gas held. A raw
main-switch button pulse (CRUISE_BUTTONS ACC_OnOff, about 140 ms) began 37 ms before steering went off,
availability fell 5 ms before it, and a second pulse brought both back about 1.48 s later. The gas was down
throughout and was labelled the cause before main_off existed. A brake press still comes first. What this
sees is the fall of availability, not the button itself; on this truck the proven source is the main switch.
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure
from shared_rules import LOSS_MIN_SPEED_MS, LOSS_WINDOW_S

CAUSES = ("brake", "main_off", "gas", "cruise_off", "gear", "fault", "blinker", "driver", "none")


def _cruise_available(s) -> bool | None:
    """Observed cruise availability, or None. samples.py fills `cruise_observation` only from a carState with
    canValid True and observed enabled/gas/brake flags; its second item is the observed `available` flag."""
    observation = s.cruise_observation
    return observation[1] if observation is not None else None


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
        self._prev_on = None
        self._prev_control_epoch = None
        self._prev_v = 0.0
        self._prev_cruise = False
        self._prev_available = None   # observed cruise availability on the previous Sample; None = unknown

    def on_evaluate(self, ctx, g) -> None:
        s = g.sample
        t = s.t
        found = _evidence(g)
        available = _cruise_available(s)
        # A fall needs both readings observed on adjacent Samples: an unknown reading or a gap breaks it.
        if self._prev_available is True and available is False and not s.gap:
            found.add("main_off")
        self._prev_available = available
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

        on = s.lat_active
        # An edge across a data gap is not attributed: what happened in the unobserved stretch is unknown.
        if (self._prev_on is True and on is False and self._prev_control_epoch == s.control_epoch
                and not s.gap and self._prev_v >= LOSS_MIN_SPEED_MS):
            before = set()
            for t_r, f_r in self._recent:
                if t_r >= t - LOSS_WINDOW_S:
                    before |= f_r
            if self._prev_cruise and not s.cruise_enabled:
                before.add("cruise_off")
            self._pending.append({"t": t, "v": self._prev_v, "evidence": before, "cruiseBefore": self._prev_cruise})
        self._prev_on, self._prev_v, self._prev_cruise = on, s.v_ego, s.cruise_enabled
        self._prev_control_epoch = s.control_epoch

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
        self._prev_on, self._prev_v, self._prev_cruise = None, 0.0, False
        self._prev_available = None

    def on_drive_end(self, ctx) -> None:
        self._flush(ctx)
