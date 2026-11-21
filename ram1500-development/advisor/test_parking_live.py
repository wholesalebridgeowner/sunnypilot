"""Experimental live parking telemetry: off in this build; when switched on in a test it decodes `can` only at
parking speeds or in Reverse, limits its own CPU, and sends one raw `parking_raw` record."""
import unittest
from types import SimpleNamespace as ns
from unittest import mock

import messages
import parking_live
import schema_v2
import synth
from parking_live import GATE_CLOSE_AT_MS, GATE_OPEN_AT_MS, ParkingLive
from synth import SEC
from test_agent import T0, AgentFixture
from test_parking_raw import e9, f6


def frame(address, dat, src=0):
    return ns(src=src, address=address, dat=dat)


def can_msg(t_ns, *frames):
    return (t_ns, list(frames))


class ClockCpu:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class GateTests(unittest.TestCase):
    def test_gate_opens_slow_or_in_reverse_and_holds_between(self):
        p = ParkingLive(lambda raw: raw)
        p.context(0.0, 25.0, "drive")
        self.assertFalse(p.open)
        p.context(1.0, GATE_OPEN_AT_MS, "drive")
        self.assertTrue(p.open)
        p.context(2.0, (GATE_OPEN_AT_MS + GATE_CLOSE_AT_MS) / 2, "drive")   # in between: holds
        self.assertTrue(p.open)
        p.context(3.0, GATE_CLOSE_AT_MS, "drive")
        self.assertFalse(p.open)
        p.context(4.0, 6.0, "reverse")
        self.assertTrue(p.open)
        self.assertAlmostEqual(p.gate_open_s, 2.0)                        # open 1->2 and 2->3; closed 3->4

    def test_nothing_is_decoded_while_the_gate_is_closed(self):
        calls = []
        p = ParkingLive(lambda raw: calls.append(raw) or raw)
        p.context(0.0, 25.0, "drive")
        p.consume(0.0, [can_msg(T0, frame(0x1F6, f6(a=0xF0)))])
        self.assertEqual(calls, [])
        self.assertEqual(p.record()["f6Frames"], 0)

    def test_counts_when_open_and_ignores_other_ids(self):
        p = ParkingLive(lambda raw: raw)
        p.context(0.0, 1.0, "reverse")
        p.consume(0.0, [can_msg(T0, frame(0x1F6, f6(a=0xF0, b=0x31)), frame(0x123, bytes(8))),
                        can_msg(T0 + 40_000_000, frame(0x1E9, e9(byte0=0x50, byte1=0x0C, byte3=1, counter=1))),
                        can_msg(T0 + 80_000_000, frame(0x1E9, e9(counter=2)), frame(0x1E9, e9(counter=3), src=2))])
        rec = p.record()
        self.assertEqual((rec["f6Frames"], rec["groupAActive"], rec["groupBActive"], rec["groupBActiveReverse"]),
                         (1, 1, 1, 1))
        self.assertEqual((rec["e9Frames"], rec["e9ChecksumFail"], rec["e9CounterSkips"], rec["e9Byte0Set"]), (2, 0, 0, 1))
        self.assertEqual((rec["canMsgs"], rec["decodeErrors"]), (3, 0))
        self.assertTrue(rec["experimental"])

    def test_decode_errors_are_counted_not_hidden(self):
        def broken(raw):
            raise ValueError("bad message")
        p = ParkingLive(broken)
        p.context(0.0, 1.0, "reverse")
        p.consume(0.0, [b"x", b"y"])
        self.assertEqual((p.record()["decodeErrors"], p.record()["canMsgs"]), (2, 0))

    def test_switches_itself_off_above_its_cpu_limit(self):
        cpu = ClockCpu()
        def slow_decode(raw):
            cpu.t += 0.02                       # 20 ms of CPU per message
            return raw
        p = ParkingLive(slow_decode, cpu_clock=cpu)
        p.context(0.0, 1.0, "drive")
        for i in range(12):                     # 12 s at 10 messages a tick: 20 % of a core
            p.consume(float(i), [can_msg(T0 + i * SEC, frame(0x1F6, f6()))] * 10)
        self.assertTrue(p.record()["selfDisabled"])
        before = p.record()["f6Frames"]
        p.consume(20.0, [can_msg(T0 + 20 * SEC, frame(0x1F6, f6()))])
        self.assertEqual(p.record()["f6Frames"], before)

    def test_record_fits_schema_v2(self):
        p = ParkingLive(lambda raw: raw)
        p.context(0.0, 1.0, "reverse")
        p.consume(0.0, [can_msg(T0, frame(0x1E9, e9(byte0=0x50, byte1=0x0C, byte3=1)))])
        event = messages.envelope("parking_raw", "d" * 32, "0123456789ab-1", p.record())
        self.assertEqual(schema_v2.problems(event), [])


class AgentParkingTests(AgentFixture):
    def make_agent(self, **overrides):
        overrides.setdefault("can_decode", lambda raw: raw)
        return super().make_agent(**overrides)

    def test_switched_off_in_this_build_no_can_socket(self):
        self.assertIs(parking_live.PARKING_LIVE_ENABLED, False)
        self.drive()
        self.assertNotIn("can", self.socks)
        self.assertNotIn("can", [name for name, _ in self.all_socks])
        self.end()
        self.assertNotIn("parking_raw", self.uplink.types())

    def test_when_switched_on_it_reports_at_drive_end(self):
        with mock.patch.object(parking_live, "PARKING_LIVE_ENABLED", True):
            self.drive()
            self.assertIn("can", self.socks)
            t = T0
            for i in range(30):                 # 3 s backing up at 1 m/s, then 3 s at 25 m/s
                t += 100_000_000
                slow = i < 15
                self.socks["can"].queue.append(can_msg(t, frame(0x1F6, f6(b=0x31) if slow else f6())))
                self.feed({"carState": (t, synth.car_state(v=1.0 if slow else 25.0,
                                                          gear="reverse" if slow else "drive")),
                           "carControl": (t, synth.car_control(lat=not slow))})
                self.agent.tick()
                self.mono.t = round(self.mono.t + 0.1, 6)
            self.assertEqual(self.socks["can"].queue, [])                   # emptied every tick, open or not
            self.end()
        records = self.uplink.data("parking_raw")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["groupBActiveReverse"], 15)                    # the 15 slow Reverse frames
        self.assertLessEqual(rec["f6Frames"], 16)                           # fast frames not decoded
        self.assertFalse(rec["selfDisabled"])
        event = [e for e in self.uplink.events if e["type"] == "parking_raw"][0]
        self.assertEqual(schema_v2.problems(event), [])
        self.assertTrue(self.socks == {} or "can" not in self.socks or self.socks["can"].closed)


if __name__ == "__main__":
    unittest.main()
