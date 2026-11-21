"""EXPERIMENTAL live parking telemetry on the Comma. SWITCHED OFF in this build: PARKING_LIVE_ENABLED = False.

A code constant, never a remote setting: it is switched on only by a new agent build, after a parked test on
the truck with the owner's go. While it is off the agent opens no `can` socket at all and nothing below runs.

What it does when on. The truck's parking-sensor fields reach the Comma only as raw CAN (0x1F6, 0x1E9 on
bus 0, see parking_raw.py). The agent then subscribes to the full `can` stream (about 100 messages and 5,300
frames a second while driving) and empties it every tick, but decodes it only while a gate is open:

    open   at or below GATE_OPEN_AT_MS (3.6 m/s, about 8 mph) or with the gear in Reverse
    closed at or above GATE_CLOSE_AT_MS (4.2 m/s, about 9.4 mph) and not in Reverse; in between it holds

The parking fields are only active at these speeds (low-speed group off above about 11 km/h) or in Reverse,
so highway driving costs a socket read per message and no decoding.

It measures its own CPU time. If it uses more than SELF_CPU_LIMIT_PCT of one core over SELF_CHECK_S, it
switches itself off for the rest of the drive, so it can never push the agent over the agent's own 15 %
limit (which would end all listening). At drive end, or when listening stops, one `parking_raw` record is
sent: raw counts only, with `experimental` true. No distance, unit, sensor position or alert; 0xFF is not
"clear" and not "failed". Nothing here can change a setting or driving.
"""
from __future__ import annotations

import time

from parking_raw import ADDR_E9, ADDR_F6, ParkingLog

PARKING_LIVE_ENABLED = False
STREAM = "can"
GATE_OPEN_AT_MS = 3.6
GATE_CLOSE_AT_MS = 4.2
SELF_CPU_LIMIT_PCT = 5.0
SELF_CHECK_S = 10.0


class ParkingLive:
    def __init__(self, decode_can, cpu_clock=time.process_time):
        self.decode_can = decode_can        # raw message -> (logMonoTime_ns, list of frames with src/address/dat)
        self.cpu_clock = cpu_clock
        self.log = ParkingLog()
        self.open = False
        self.disabled_reason = None
        self.gate_open_s = 0.0
        self.can_msgs = 0
        self.decode_errors = 0              # reported, so a broken decode never looks like "no parking data"
        self.cpu_s = 0.0
        self._last_now = None
        self._win_start = None
        self._win_cpu = 0.0

    def context(self, now: float, v_ego, gear) -> None:
        """The newest carState of this tick: speed (m/s) and gear name."""
        if self._last_now is not None and self.open:
            self.gate_open_s += max(0.0, now - self._last_now)
        self._last_now = now
        gear = str(gear) if gear is not None else "other"
        if isinstance(v_ego, (int, float)):
            self.log.car_state(gear, float(v_ego))
        if gear == "reverse":
            self.open = True
        elif isinstance(v_ego, (int, float)):
            if v_ego <= GATE_OPEN_AT_MS:
                self.open = True
            elif v_ego >= GATE_CLOSE_AT_MS:
                self.open = False

    def consume(self, now: float, raws: list) -> None:
        """Every raw `can` message drained this tick. Decoded only while the gate is open."""
        if self.disabled_reason is not None or not self.open or not raws:
            return
        c0 = self.cpu_clock()
        for raw in raws:
            try:
                t_ns, frames = self.decode_can(raw)
            except Exception:
                self.decode_errors += 1
                continue
            self.can_msgs += 1
            for f in frames:
                address = f.address
                if address == ADDR_F6 or address == ADDR_E9:
                    self.log.frame(t_ns / 1e9, int(f.src), address, bytes(f.dat))
        used = self.cpu_clock() - c0
        self.cpu_s += used
        self._win_cpu += used
        if self._win_start is None:
            self._win_start = now
        elif now - self._win_start >= SELF_CHECK_S:
            if 100.0 * self._win_cpu / (now - self._win_start) > SELF_CPU_LIMIT_PCT:
                self.disabled_reason = "self_cpu_limit"
            self._win_start, self._win_cpu = now, 0.0

    def record(self) -> dict:
        r = self.log.record()
        f6, e9 = r["f6"], r["e9"]
        return {"experimental": True, "gateOpenS": round(self.gate_open_s, 1), "canMsgs": self.can_msgs, "decodeErrors": self.decode_errors,
                "cpuS": round(self.cpu_s, 3), "selfDisabled": self.disabled_reason is not None,
                "f6Frames": f6.get("frames", 0), "f6FormatMismatch": f6.get("format_mismatch", 0),
                "groupAActive": f6.get("group_a_active", 0), "groupBActive": f6.get("group_b_active", 0),
                "groupBActiveReverse": f6["groupB_active_by_gear"].get("reverse", 0),
                "e9Frames": e9.get("frames", 0), "e9ChecksumFail": e9.get("checksum_fail", 0),
                "e9CounterSkips": e9.get("counter_skips", 0), "e9Byte0Set": e9["byte0_set"],
                "e9Byte0Min": e9["byte0_min"], "e9Byte0Max": e9["byte0_max"]}
