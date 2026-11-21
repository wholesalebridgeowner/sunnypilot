"""EXPERIMENTAL live parking telemetry on the Comma. SWITCHED OFF in this build: PARKING_LIVE_ENABLED = False.

A code constant, never a remote setting: it is switched on only by a new agent build, after a parked test on
the truck with the owner's go. While it is off nothing below runs and no `can` socket exists.

When on (revised after the 2026-11-21 review of 3098b5f):

  Gate     Open only with a FRESH, VALID vehicle-state sample (at most STATE_MAX_AGE_S old, finite speed,
           event valid): in Reverse, or at or below GATE_OPEN_AT_MS (3.6 m/s, about 8 mph); it closes at or
           above GATE_CLOSE_AT_MS (4.2 m/s) and holds in between. Missing, stale, invalid or non-finite state
           closes it. The agent opens the `can` socket only while the gate is open (after a subscriber-slot
           check) and CLOSES it when the gate closes or this path switches itself off, so at highway speed
           and after a cutoff there is no `can` work at all.
  Reads    At most MAX_MSGS_PER_TICK messages per tick; the rest wait (counted as a capped tick).
  Cost     All of the path's work while collecting (socket receive, list building, decode, frame traversal,
           counting, cleanup, failures) is timed with the process CPU clock. It switches itself off for the
           rest of the drive if one tick uses more than TICK_CPU_LIMIT_S, or a full SELF_CHECK_S window of
           collecting uses more than SELF_CPU_LIMIT_PCT of one core. Windows restart at every gate opening.
           These are checks after the work, not a hard ceiling: they limit this path's share but cannot
           guarantee the agent's total stays under its own 15 % limit.
  Errors   Every failure to decode a message or walk its frames is counted in decodeErrors.

One `parking_raw` record is sent at drive end or when listening stops: raw counts, bounded raw histograms,
collection time, CPU, decode errors and the cutoff reason. No distance, unit, sensor position or alert;
0xFF and 0xF0 are not "clear" and not "failed". Nothing here can change a setting or driving.
"""
from __future__ import annotations

import math
import time
from contextlib import contextmanager

from parking_raw import ADDR_E9, ADDR_F6, GEARS, ParkingLog

PARKING_LIVE_ENABLED = False
STREAM = "can"
GATE_OPEN_AT_MS = 3.6
GATE_CLOSE_AT_MS = 4.2
STATE_MAX_AGE_S = 1.0
MAX_MSGS_PER_TICK = 40
SELF_CPU_LIMIT_PCT = 5.0
SELF_CHECK_S = 10.0
TICK_CPU_LIMIT_S = 0.25
SLOT_RETRY_S = 10.0


def _pairs(hist: dict) -> list:
    """{"31": 5, "F0": 2} -> [0x31, 5, 0xF0, 2]: a flat, bounded list of numbers for the wire."""
    return [x for key, n in sorted(hist.items()) for x in (int(key, 16), n)]


class ParkingLive:
    def __init__(self, decode_can, cpu_clock=time.process_time, monotonic=None):
        self.decode_can = decode_can        # raw message -> (logMonoTime_ns, iterable of frames: src/address/dat)
        self.cpu_clock = cpu_clock
        self.monotonic = monotonic             # device clock, or explicit timestamps in an offline replay
        self.log = ParkingLog()
        self.disabled_reason = None
        self.open = False                   # the gate decision of the newest tick
        self._expires = None                # seconds on the carState publisher's monotonic clock
        self._last_stamp_ns = None
        self._collecting = False
        self._next_slot_try = 0.0
        self._measuring = False
        self._last_now = None
        self.gate_open_s = 0.0
        self.collect_s = 0.0
        self.unknown_state_s = 0.0
        self.can_msgs = 0
        self.decode_errors = 0
        self.capped_ticks = 0
        self.socket_opens = 0
        self.slot_refusals = 0
        self.cpu_s = 0.0
        self._win_start = None
        self._win_cpu = 0.0

    # --- vehicle state and gate --------------------------------------------------------------------
    def _advance(self, now: float) -> None:
        """Account only the portion of the previous interval covered by fresh vehicle state."""
        previous = self._last_now
        if previous is not None:
            if now < previous:
                raise ValueError("parking monotonic clock went backwards")
            known = max(0.0, min(now, self._expires) - previous) if self._expires is not None else 0.0
            self.unknown_state_s += max(0.0, now - previous - known)
            if self.open:
                self.gate_open_s += known
                if self._collecting and self.disabled_reason is None:
                    self.collect_s += known
        self._last_now = now
        if self._expires is not None and now > self._expires:
            self._expires = None
            self.open = False

    def context(self, now: float, t_ns, v_ego, gear, valid) -> None:
        """now and logMonoTime must both use the carState publisher's monotonic clock."""
        self._advance(now)
        stamp_ok = (type(t_ns) is int and 0 <= t_ns and t_ns / 1e9 <= now
                    and (self._last_stamp_ns is None or t_ns > self._last_stamp_ns))
        if stamp_ok:
            self._last_stamp_ns = t_ns
        t = t_ns / 1e9 if stamp_ok else None
        gear = str(gear)
        good = (valid is True and t is not None and now - t <= STATE_MAX_AGE_S and gear in GEARS
                and type(v_ego) in (int, float) and math.isfinite(v_ego))
        self.log.car_state(t, gear, v_ego, good)
        if not good:
            self._expires = None
            self.open = False
            return
        self._expires = t + STATE_MAX_AGE_S
        if gear == "reverse" or abs(v_ego) <= GATE_OPEN_AT_MS:
            self.open = True
        elif abs(v_ego) >= GATE_CLOSE_AT_MS:
            self.open = False

    def want_open(self, now: float) -> bool:
        """The gate for this tick. Also books gate, collection and unknown-state time."""
        self._advance(now)
        return self.open and self.disabled_reason is None

    # --- collection ------------------------------------------------------------------------------------
    def on_open(self, now: float) -> None:
        self._advance(now)
        self.socket_opens += 1
        self._collecting = True
        self._win_start, self._win_cpu = self.collect_s, 0.0

    def on_close(self, now: float) -> None:
        self._advance(now)
        self._collecting = False
        if not self._measuring:
            self._check_budget(0.)
        self.log.break_counter_continuity()

    @contextmanager
    def measure_tick(self, now: float):
        """Charge the whole feature tick, including failed setup, receive, parsing and teardown."""
        if self._measuring:
            yield
            return
        self._advance(now)
        self._measuring = True
        c0 = self.cpu_clock()
        try:
            yield
        finally:
            self._measuring = False
            if self.monotonic is not None:
                self._advance(self.monotonic())
            used = max(0.0, self.cpu_clock() - c0)
            self._check_budget(used)

    def _check_budget(self, used: float) -> None:
        self.cpu_s += used
        self._win_cpu += used
        if self.disabled_reason is None and used > TICK_CPU_LIMIT_S:
            self.disabled_reason = "tick_cpu_limit"
        if self._win_start is not None:
            elapsed = self.collect_s - self._win_start
            if elapsed >= SELF_CHECK_S:
                if self.disabled_reason is None and 100 * self._win_cpu / elapsed > SELF_CPU_LIMIT_PCT:
                    self.disabled_reason = "self_cpu_limit"
                self._win_start, self._win_cpu = self.collect_s, 0.0
        if not self._collecting:
            self._win_start, self._win_cpu = None, 0.0

    def collect(self, now: float, sock) -> None:
        if not self._collecting or not self.want_open(now):
            return
        with self.measure_tick(now):
            for _ in range(MAX_MSGS_PER_TICK):
                raw = sock.receive(non_blocking=True)
                if raw is None:
                    break
                self._one(raw)
            else:
                self.capped_ticks += 1

    def _one(self, raw) -> None:
        try:
            t_ns, frames = self.decode_can(raw)
            if type(t_ns) is not int or t_ns < 0:
                raise ValueError("invalid CAN event timestamp")
            seen = []
            for f in frames:                            # lazy frame access stays inside the error handling
                address = f.address
                if address == ADDR_F6 or address == ADDR_E9:
                    seen.append((int(f.src), address, bytes(f.dat)))
            for src, address, dat in seen:
                self.log.frame(t_ns / 1e9, src, address, dat)
        except Exception:
            self.decode_errors += 1
            self.log.break_counter_continuity()
            return
        self.can_msgs += 1

    def can_try_slot(self, now: float) -> bool:
        return now >= self._next_slot_try

    def note_slot_refusal(self, now: float) -> None:
        self.slot_refusals += 1
        self._next_slot_try = now + SLOT_RETRY_S

    # --- record --------------------------------------------------------------------------------------------
    def record(self) -> dict:
        r = self.log.record()
        f6, e9 = r["f6"], r["e9"]
        codes = f6["codes_by_byte"]
        return {"experimental": True, "gateOpenS": round(self.gate_open_s, 1), "collectS": round(self.collect_s, 1),
                "unknownStateS": round(self.unknown_state_s, 1), "canMsgs": self.can_msgs,
                "decodeErrors": self.decode_errors, "cappedTicks": self.capped_ticks,
                "socketOpens": self.socket_opens, "slotRefusals": self.slot_refusals,
                "cpuS": round(self.cpu_s, 3), "selfDisabled": self.disabled_reason is not None,
                "disabledReason": self.disabled_reason, "invalidContext": r["invalid_context"],
                "f6Frames": f6.get("frames", 0), "f6FormatMismatch": f6.get("format_mismatch", 0),
                "f6UnknownCodeValues": f6.get("unknown_code_values", 0),
                "groupAActive": f6.get("group_a_active", 0), "groupBActive": f6.get("group_b_active", 0),
                "groupBActiveReverse": f6["groupB_active_by_gear"].get("reverse", 0),
                **{f"f6CodesB{i}": _pairs(codes[str(i)]) for i in range(1, 7)},
                "e9Frames": e9.get("frames", 0), "e9ValidFrames": e9.get("valid_frames", 0),
                "e9ChecksumFail": e9.get("checksum_fail", 0), "e9FormatMismatch": e9.get("format_mismatch", 0),
                "e9CounterPairs": e9.get("counter_pairs", 0), "e9CounterSkips": e9.get("counter_skips", 0),
                "e9Byte0Set": e9["byte0_set"], "e9Byte0Min": e9["byte0_min"], "e9Byte0Max": e9["byte0_max"],
                "e9Byte0Hist": _pairs(e9["byte0_hist"])}
