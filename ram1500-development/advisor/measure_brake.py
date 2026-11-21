"""Braking episodes: each brake application counted once, graded by the speed
lost in one second, with who did the braking.

Comma does not brake on this truck. This is information only; nothing here
links to any setting.

An episode opens when the truck has lost 2.0 m/s within the last second and
stays open until the one-second drop falls below 1.0 m/s, so a stop that eases
and tightens again is still one episode. Speeds come from the speed signal
alone (the acceleration signal jumps with brief wheel-speed dips), and the
speed one and two seconds back is interpolated by time stamp, so the result
does not depend on the loop period. Holes of up to 1.5 s are bridged. A speed
step of more than 2.0 m/s between two Samples at most 0.3 s apart is a sensor
step, not braking: it discards what was open.

One `brake` record is sent when an episode closes (also at the end of the
drive), and it is counted in the window open at that moment:

    peakDrop1s           most speed lost within one second, m/s. Grades are cut
                         from it by the service (hard from 3.0 m/s)
    peakDrop2s           ... within two seconds; null without two seconds of history
    who                  driver (brake pedal), cruise (the truck's own cruise control,
                         on for at least half of the episode), or other
    tookOverFromCruise   the driver braked within 3 s of the cruise being on
    speedMs              speed one second before the episode opened
    leadDistanceM        distance to the vehicle ahead in the 3.0 to 1.5 s before the
    leadGapS             episode opened, and that distance as a time gap; null unless
                         a lead was seen for at least 80% of that time

Driving time and cruise time, the denominators, come from the steering-time
measure (movingS, cruiseS).
"""
from __future__ import annotations

from collections import deque

from measure_api import Measure

OPEN_DROP_MS = 2.0          # speed lost within one second that opens an episode
CLOSE_DROP_MS = 1.0         # the episode closes when the one-second drop falls below this
GLITCH_STEP_MS = 2.0        # a larger step between two Samples close together is not braking
LEAD_MIN_PERCENT = 80       # of the look-back Samples must have a lead with a known distance
LEAD_MIN_SPEED_MS = 1.0     # no time gap from a crawl


def _ns(seconds: float) -> int:
    return int(round(seconds * 1e9))


_ONE_S_NS = _ns(1.0)
_TWO_S_NS = _ns(2.0)
_KEEP_NS = _ns(4.2)             # the buffer: enough for the two-second drop and the lead look-back
_MAX_HOLE_NS = _ns(1.5)         # a longer hole closes the episode and clears the buffer
_GLITCH_WITHIN_NS = _ns(0.3)    # a speed step counts as a glitch only between Samples this close
_EDGE_NS = _ns(0.02)            # the oldest Sample stands in for a time this much before it
_TAKEOVER_NS = _ns(3.0)         # cruise on this long before the first pedal Sample: took over from cruise
_LEAD_FROM_NS = _ns(3.0)        # lead look-back: from this long before the episode opened ...
_LEAD_TO_NS = _ns(1.5)          # ... to this long before it

_T, _V, _BRAKE, _CRUISE, _LEAD = range(5)


class _Episode:
    __slots__ = ("peak1", "peak2", "start_speed", "brake", "took_over", "n", "cruise_n", "lead_gap", "lead_distance")


class BrakeDetector:
    """One per drive. Feed every Sample in time order; a finished episode comes back as its record."""

    def __init__(self):
        self._buf = deque()         # (time ns, speed, brake pedal, cruise on, lead distance or None)
        self._ep = None             # the open episode

    @property
    def buffered(self) -> int:
        return len(self._buf)

    def add(self, t_ns: int, v: float, brake: bool, cruise: bool, lead_distance) -> dict | None:
        """`lead_distance`: metres to the lead when there is one with a known distance, else None."""
        buf = self._buf
        done = None
        if buf:
            step_ns = t_ns - buf[-1][_T]
            if step_ns < 0 or step_ns > _MAX_HOLE_NS:
                done = self._close()                # what was measured before the hole still counts
                buf.clear()
            elif abs(v - buf[-1][_V]) > GLITCH_STEP_MS and step_ns <= _GLITCH_WITHIN_NS:
                self._ep = None                     # a sensor step: the open episode is dropped uncounted
                buf.clear()
        buf.append((t_ns, v, brake, cruise, lead_distance))
        while buf[0][_T] < t_ns - _KEEP_NS:
            buf.popleft()

        v1 = self._speed_at(t_ns - _ONE_S_NS)
        v2 = self._speed_at(t_ns - _TWO_S_NS)
        drop1 = 0.0 if v1 is None else v1 - v
        drop2 = None if v2 is None else v2 - v
        ep = self._ep
        if ep is None:
            if drop1 >= OPEN_DROP_MS:
                self._open(t_ns, drop1, drop2, v1, v2)
        elif drop1 < CLOSE_DROP_MS:
            done = self._close()
        else:
            if drop1 > ep.peak1:
                ep.peak1 = drop1
            if drop2 is not None and (ep.peak2 is None or drop2 > ep.peak2):
                ep.peak2 = drop2
            ep.n += 1
            if cruise:
                ep.cruise_n += 1
            if brake and not ep.brake:
                ep.brake = True
                ep.took_over = self._cruise_on_between(t_ns - _TAKEOVER_NS, t_ns)
        return done

    def flush(self) -> dict | None:
        """End of the drive: an open episode closes as it is."""
        return self._close()

    def _speed_at(self, target_ns: int):
        """Speed at a time before the newest Sample, interpolated between the two Samples around it.
        None when the buffer does not reach back that far."""
        later = None
        for row in reversed(self._buf):
            if row[_T] <= target_ns:
                if later is None or row[_T] == target_ns:
                    return row[_V]
                share = (target_ns - row[_T]) / (later[_T] - row[_T])
                return row[_V] + share * (later[_V] - row[_V])
            later = row
        return later[_V] if later[_T] - target_ns <= _EDGE_NS else None

    def _cruise_on_between(self, from_ns: int, to_ns: int) -> bool:
        return any(row[_CRUISE] for row in self._buf if from_ns <= row[_T] <= to_ns)

    def _open(self, t_ns: int, drop1: float, drop2, v1: float, v2) -> None:
        buf = self._buf
        ep = self._ep = _Episode()
        ep.peak1, ep.peak2, ep.start_speed = drop1, drop2, v1
        last_second = [row for row in buf if row[_T] >= t_ns - _ONE_S_NS]
        ep.n = len(last_second)
        ep.cruise_n = sum(1 for row in last_second if row[_CRUISE])
        first_pedal_ns = next((row[_T] for row in last_second if row[_BRAKE]), None)
        ep.brake = first_pedal_ns is not None
        ep.took_over = ep.brake and self._cruise_on_between(first_pedal_ns - _TAKEOVER_NS, first_pedal_ns)

        ep.lead_distance = ep.lead_gap = None
        look_back = [row for row in buf if t_ns - _LEAD_FROM_NS <= row[_T] <= t_ns - _LEAD_TO_NS]
        distances = sorted(row[_LEAD] for row in look_back if row[_LEAD] is not None)
        if (look_back and len(distances) * 100 >= LEAD_MIN_PERCENT * len(look_back)
                and v2 is not None and v2 > LEAD_MIN_SPEED_MS):
            ep.lead_distance = distances[len(distances) // 2]       # the upper middle value of an even number
            ep.lead_gap = ep.lead_distance / v2

    def _close(self) -> dict | None:
        ep, self._ep = self._ep, None
        if ep is None:
            return None
        if ep.brake:
            who = "driver"
        elif ep.cruise_n * 2 >= ep.n:
            who = "cruise"
        else:
            who = "other"               # no brake pedal, and the cruise off for most of the episode
        return {"peakDrop1s": round(ep.peak1, 2),
                "peakDrop2s": None if ep.peak2 is None else round(ep.peak2, 2),
                "who": who, "tookOverFromCruise": bool(ep.brake and ep.took_over),
                "speedMs": round(ep.start_speed, 1),
                "leadGapS": None if ep.lead_gap is None else round(ep.lead_gap, 1),
                "leadDistanceM": None if ep.lead_distance is None else round(ep.lead_distance, 1)}


class Braking(Measure):
    name = "brake"
    owner_facing = True

    def __init__(self):
        self._detector = BrakeDetector()
        self._episodes = 0              # closed in the open window

    def on_sample(self, ctx, g) -> None:
        s = g.sample
        self._send(ctx, self._detector.add(g.t_ns, s.v_ego, s.brake_pressed, s.cruise_enabled,
                                           s.lead_distance_m if s.lead_present else None))

    def on_drive_end(self, ctx) -> None:
        self._send(ctx, self._detector.flush())

    def _send(self, ctx, record) -> None:
        if record is not None:
            self._episodes += 1
            ctx.emit("brake", record)

    def window_fields(self) -> dict:
        episodes, self._episodes = self._episodes, 0
        return {"brakeEpisodes": episodes}
