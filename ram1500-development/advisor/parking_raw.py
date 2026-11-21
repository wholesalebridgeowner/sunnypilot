"""EXPERIMENTAL parking telemetry: raw counts of two candidate parking messages, meaning unconfirmed.

The decode library for the offline report (parking_report.py) and for the live path (parking_live.py), which
is SWITCHED OFF in this build. Standard library only. The
truck's parking-sensor data reaches the Comma's harness only as raw CAN on bus 0; nothing in the build parses
it, so on the device it would need the full `can` stream, which is held back until its CPU cost is measured
on the truck (owner's go).

Decoding rules come from the offline reverse engineering of 2026-11-21 (Codex, "ram1500-parking-re-v2",
checked against 133,109 recorded bus-0 frames) and were cross-checked by an independent Reverse-gear scan
(Phase 0, same bytes flagged). Byte numbers are zero based.

  0x1F6  byte 0 = 0x04, byte 7 = 0x00, bytes 1-6 six coded fields in two groups that switch FF/non-FF
         together: group A = bytes 1, 2, 5 (active at low speed in D/N/R: front-like, a hypothesis) and
         group B = bytes 3, 4, 6 (active in Reverse: rear-like, a hypothesis). No checksum.
  0x1E9  byte 7 Chrysler CRC-8 (poly 0x1D, init 0xFF, xorout 0xFF), byte 6 high nibble a rolling counter,
         byte 0 a changing raw value seen only in Reverse (0xFF otherwise), byte 1 / byte 3 mode-like.

What this does NOT do: it gives no distance, no unit, no sensor position, and no alert. 0xFF is not "clear"
and not "failed"; 0xF0 is not "clear". Values stay raw. Bus 2 copies are counted but never treated as a
second sensor. Nothing here may drive an alert or a change in driving.
"""
from __future__ import annotations

import math
from collections import Counter, deque

ADDR_F6 = 0x1F6
ADDR_E9 = 0x1E9
OBSERVED_F6_CODES = frozenset((0x31, 0x32, 0x33, 0xB4, 0xB5, 0xB6, 0xF0, 0xFF))
GROUP_A = (1, 2, 5)
GROUP_B = (3, 4, 6)
COUNTER_MAX_GAP_S = 0.55
CONTEXT_MAX_AGE_S = 0.5
GEARS = ("drive", "reverse", "neutral", "park")  # anything else is "other"; no recent valid context is "unknown"


def e9_checksum_ok(d: bytes) -> bool:
    crc = 0xFF
    for value in d[:-1]:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1D if crc & 0x80 else 0)) & 0xFF
    return (crc ^ 0xFF) == d[-1]


def _group_state(d: bytes, group: tuple):
    states = [d[i] != 0xFF for i in group]
    return states[0] if all(s == states[0] for s in states) else None


def decode(address: int, bus: int, payload: bytes) -> dict | None:
    """Raw fields and checks of one recorded frame; None for another address, bus or length."""
    d = bytes(payload)
    if bus != 0 or len(d) != 8:
        return None
    if address == ADDR_E9:
        layout_ok = (d[1] in (0, 8, 12) and d[2] == 0x14 and d[3] in (0, 1) and d[4] == 0x14 and d[5] == 0
                     and (d[6] & 15) == 0)
        return {"address": address, "byte0": d[0], "byte1": d[1], "byte3": d[3], "counter": d[6] >> 4,
                "checksum_ok": e9_checksum_ok(d), "format_ok": layout_ok}
    if address == ADDR_F6:
        a, b = _group_state(d, GROUP_A), _group_state(d, GROUP_B)
        codes = {i: d[i] for i in range(1, 7)}
        return {"address": address, "codes": codes, "group_a_active": a, "group_b_active": b,
                "format_ok": d[0] == 4 and d[7] == 0 and all(c in OBSERVED_F6_CODES for c in codes.values())
                and a is not None and b is not None}
    return None


class ParkingLog:
    """Per-drive raw counts. Feed carState context (with its time) and frames in time order.

    A frame is attributed to a gear only through a recent, valid vehicle-state sample: the newest context
    at or before the frame and at most CONTEXT_MAX_AGE_S older; otherwise its gear is "unknown". 0x1E9
    frames that fail the checksum or layout are counted as diagnostics only: they never enter the value
    statistics, and they break counter continuity. Every histogram is bounded (one byte = 256 keys at most).
    """

    def __init__(self):
        self._ctx = deque(maxlen=64)  # (source time, gear, speed), including timestamped invalidations
        self._last_context_t = None
        self.f6 = Counter()
        self.f6_codes = {i: Counter() for i in range(1, 7)}
        self.f6_a_gear = Counter()
        self.f6_b_gear = Counter()
        self.f6_a_vmax = None
        self.e9 = Counter()
        self.e9_byte0 = Counter()     # valid, non-FF raw byte-0 values
        self.e9_byte1 = Counter()
        self.e9_byte3_gear = Counter()
        self._e9_prev = None          # (t, counter) of the previous VALID frame
        self._e9_latest_t = None      # repeated or older timestamps cannot seed the next pair
        self.bus2 = Counter()
        self.invalid_context = 0

    def car_state(self, t: float | None, gear, v_ego, valid: bool = True) -> None:
        if type(t) not in (int, float) or not math.isfinite(t):
            self.invalid_context += 1
            self._ctx.clear()
            return
        if self._last_context_t is not None and t <= self._last_context_t:
            self.invalid_context += 1
            return
        self._last_context_t = t
        if valid is not True or type(v_ego) not in (int, float) or not math.isfinite(v_ego):
            self.invalid_context += 1
            self._ctx.append((t, "unknown", None))
            return
        gear = str(gear) if gear is not None else "other"
        self._ctx.append((t, gear if gear in GEARS else "other", float(v_ego)))

    def _context_at(self, t: float):
        for ct, gear, v in reversed(self._ctx):
            if ct <= t:
                return (gear, v) if t - ct <= CONTEXT_MAX_AGE_S else ("unknown", None)
        return ("unknown", None)

    def break_counter_continuity(self):
        self._e9_prev = None

    def _observe_counter(self, t, counter):
        if not math.isfinite(t) or (self._e9_latest_t is not None and t <= self._e9_latest_t):
            self.break_counter_continuity()
            return
        self._e9_latest_t = t
        if counter is None:
            self.break_counter_continuity()
            return
        if self._e9_prev is not None:
            pt, pc = self._e9_prev
            if t - pt < COUNTER_MAX_GAP_S:
                self.e9["counter_pairs"] += 1
                self.e9["counter_skips"] += counter != (pc + 1) % 16
        self._e9_prev = (t, counter)

    def frame(self, t: float, bus: int, address: int, payload: bytes) -> None:
        if address not in (ADDR_F6, ADDR_E9):
            return
        if bus == 2:
            self.bus2[hex(address)] += 1
            return
        if bus != 0:
            return
        stats = self.f6 if address == ADDR_F6 else self.e9
        stats["frames"] += 1
        r = decode(address, bus, payload)
        if r is None:
            stats["format_mismatch"] += 1
            if address == ADDR_E9:
                self._observe_counter(t, None)
            return
        gear, v = self._context_at(t)
        if address == ADDR_F6:
            self.f6["format_mismatch"] += not r["format_ok"]
            if r["group_a_active"] is None or r["group_b_active"] is None:
                self.f6["inconsistent_group"] += 1
            for i, c in r["codes"].items():
                self.f6_codes[i][f"{c:02X}"] += 1          # unknown codes stay distinct
                if c not in OBSERVED_F6_CODES:
                    self.f6["unknown_code_values"] += 1
            if not r["format_ok"]:
                return
            if r["group_a_active"]:
                self.f6["group_a_active"] += 1
                self.f6_a_gear[gear] += 1
                if v is not None:
                    self.f6_a_vmax = v if self.f6_a_vmax is None else max(self.f6_a_vmax, v)
            if r["group_b_active"]:
                self.f6["group_b_active"] += 1
                self.f6_b_gear[gear] += 1
        else:
            if not (r["checksum_ok"] and r["format_ok"]):
                self.e9["checksum_fail"] += not r["checksum_ok"]
                self.e9["format_mismatch"] += not r["format_ok"]
                self._observe_counter(t, None)
                return
            self.e9["valid_frames"] += 1
            self._observe_counter(t, r["counter"])
            if r["byte0"] != 0xFF:
                self.e9_byte0[r["byte0"]] += 1
                self.e9[f"byte0_set_in_{gear}"] += 1
            self.e9_byte1[f"{r['byte1']:02X}"] += 1
            if r["byte3"] == 1:
                self.e9_byte3_gear[gear] += 1

    def record(self) -> dict:
        b0 = self.e9_byte0
        return {
            "experimental": True, "meaningConfirmed": False, "units": None,
            "invalid_context": self.invalid_context,
            "f6": {**dict(self.f6), "groupA_active_by_gear": dict(self.f6_a_gear),
                   "groupB_active_by_gear": dict(self.f6_b_gear),
                   "groupA_active_vmax_ms": None if self.f6_a_vmax is None else round(self.f6_a_vmax, 3),
                   "codes_by_byte": {str(i): dict(c) for i, c in self.f6_codes.items()}},
            "e9": {**dict(self.e9), "byte0_set": sum(b0.values()), "byte0_min": min(b0) if b0 else None,
                   "byte0_max": max(b0) if b0 else None, "byte0_hist": {f"{k:02X}": n for k, n in sorted(b0.items())},
                   "byte1_codes": dict(self.e9_byte1), "byte3_one_by_gear": dict(self.e9_byte3_gear)},
            "bus2_copies_ignored": dict(self.bus2),
        }
