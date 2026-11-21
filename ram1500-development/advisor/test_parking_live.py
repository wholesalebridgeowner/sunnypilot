"""Experimental live parking telemetry: off in this build. When switched on in a test it reads `can` only while
a fresh, valid vehicle state puts the gate open, times all of its own work, and sends one `parking_raw` record.
Cases marked "review" reproduce the findings of the 2026-11-21 review of 3098b5f (Codex)."""
import math
import unittest
from types import SimpleNamespace as ns
from unittest import mock

import messages
import parking_live
import schema_v2
import synth
from parking_live import GATE_CLOSE_AT_MS, GATE_OPEN_AT_MS, MAX_MSGS_PER_TICK, ParkingLive
from test_agent import T0, AgentFixture
from test_parking_raw import e9, f6

MS = 1_000_000


def frame(address, dat, src=0):
    return ns(src=src, address=address, dat=dat)


def can_msg(t_ns, *frames):
    return (t_ns, list(frames))


class FakeCpu:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Sock:
    """A `can` socket fake: `cost` CPU seconds per receive call on `cpu`."""

    def __init__(self, messages=(), cpu=None, cost=0.0):
        self.queue = list(messages)
        self.reads = 0
        self.cpu, self.cost = cpu, cost

    def receive(self, non_blocking=True):
        self.reads += 1
        if self.cpu is not None:
            self.cpu.t += self.cost
        return self.queue.pop(0) if self.queue else None


class GateTests(unittest.TestCase):
    def live(self, **kw):
        return ParkingLive(lambda raw: raw, **kw)

    def test_opens_slow_or_in_reverse_holds_between_closes_fast(self):
        p = self.live()
        p.context(0.0, T0, 25.0, "drive", True)
        self.assertFalse(p.want_open(0.0))
        p.context(1.0, T0 + 1000 * MS, GATE_OPEN_AT_MS, "drive", True)
        self.assertTrue(p.want_open(1.0))
        p.context(2.0, T0 + 2000 * MS, (GATE_OPEN_AT_MS + GATE_CLOSE_AT_MS) / 2, "drive", True)
        self.assertTrue(p.want_open(2.0))
        p.context(3.0, T0 + 3000 * MS, GATE_CLOSE_AT_MS, "drive", True)
        self.assertFalse(p.want_open(3.0))
        p.context(4.0, T0 + 4000 * MS, 6.0, "reverse", True)
        self.assertTrue(p.want_open(4.0))

    def test_review_stale_state_closes_the_gate(self):
        p = self.live()
        p.context(0.0, T0, 1.0, "reverse", True)
        self.assertTrue(p.want_open(0.5))
        self.assertFalse(p.want_open(600.0))            # 600 s without a new carState: unknown, closed
        self.assertGreater(p.record()["unknownStateS"], 0)

    def test_review_nan_or_invalid_state_closes_the_gate(self):
        p = self.live()
        p.context(0.0, T0, float("nan"), "unknown", True)
        self.assertFalse(p.want_open(0.0))
        p.context(1.0, T0 + 1000 * MS, 1.0, "reverse", False)  # event marked invalid
        self.assertFalse(p.want_open(1.0))
        self.assertEqual(p.record()["invalidContext"], 2)

    def test_review_frame_traversal_failure_is_counted_and_timed(self):
        cpu = FakeCpu()

        class BadFrames:
            def __iter__(self):
                cpu.t += 0.1
                raise ValueError("lazy list access failed")

        p = ParkingLive(lambda raw: (T0, BadFrames()), cpu_clock=cpu)
        p.context(0.0, T0, 1.0, "reverse", True)
        self.assertTrue(p.want_open(0.0))
        p.on_open(0.0)
        p.collect(0.0, Sock([b"x"]))
        rec = p.record()
        self.assertEqual((rec["decodeErrors"], rec["canMsgs"]), (1, 0))
        self.assertAlmostEqual(rec["cpuS"], 0.1)

    def test_review_receive_cost_counts_and_trips_the_tick_limit(self):
        cpu = FakeCpu()
        p = ParkingLive(lambda raw: raw, cpu_clock=cpu)
        p.context(0.0, T0, 1.0, "reverse", True)
        p.want_open(0.0)
        p.on_open(0.0)
        p.collect(0.0, Sock([can_msg(T0, frame(0x1F6, f6()))] * 5, cpu=cpu, cost=0.1))
        rec = p.record()
        self.assertGreater(rec["cpuS"], 0.25)
        self.assertEqual(rec["disabledReason"], "tick_cpu_limit")

    def test_review_window_restarts_at_each_opening(self):
        cpu = FakeCpu()
        p = ParkingLive(lambda raw: raw, cpu_clock=cpu)
        p.context(0.0, T0, 1.0, "reverse", True)
        p.want_open(0.0)
        p.on_open(0.0)
        p.on_close()                                    # closed for 100 s
        p.context(100.0, T0 + 100_000 * MS, 1.0, "reverse", True)
        p.want_open(100.0)
        p.on_open(100.0)
        for i in range(101):                            # 0.1 s CPU per 0.1 s tick for 10 s: 100 % of a core
            p.collect(100.0 + 0.1 * i, Sock([can_msg(T0, frame(0x1F6, f6()))], cpu=cpu, cost=0.1))
            if p.disabled_reason:
                break
        self.assertEqual(p.disabled_reason, "self_cpu_limit")

    def test_reads_are_bounded_per_tick(self):
        p = self.live()
        p.context(0.0, T0, 1.0, "reverse", True)
        p.want_open(0.0)
        p.on_open(0.0)
        sock = Sock([can_msg(T0 + i * MS, frame(0x1F6, f6())) for i in range(1000)])
        p.collect(0.0, sock)
        self.assertEqual(sock.reads, MAX_MSGS_PER_TICK)
        self.assertEqual(p.record()["cappedTicks"], 1)

    def test_record_fits_schema_v2(self):
        p = self.live()
        p.context(0.0, T0, 1.0, "reverse", True)
        p.want_open(0.0)
        p.on_open(0.0)
        p.collect(0.0, Sock([can_msg(T0, frame(0x1E9, e9(byte0=0x50, byte1=0x0C, byte3=1))),
                             can_msg(T0, frame(0x1F6, f6(b=0x31)))]))
        rec = p.record()
        self.assertEqual(rec["e9Byte0Hist"], [0x50, 1])
        self.assertIn(0x31, rec["f6CodesB3"])
        event = messages.envelope("parking_raw", "d" * 32, "0123456789ab-1", rec)
        self.assertEqual(schema_v2.problems(event), [])


class AgentParkingTests(AgentFixture):
    def make_agent(self, **overrides):
        overrides.setdefault("can_decode", lambda raw: raw)
        return super().make_agent(**overrides)

    def tick_with(self, t_ns, v, gear, can=()):
        if "can" in self.socks:
            self.socks["can"].queue.extend(can)
        self.feed({"carState": (t_ns, synth.car_state(v=v, gear=gear)),
                   "carControl": (t_ns, synth.car_control(lat=False))})
        self.agent.tick()
        self.mono.t = round(self.mono.t + 0.1, 6)

    def can_socks(self):
        return [s for name, s in self.all_socks if name == "can"]

    def test_switched_off_in_this_build_no_can_socket(self):
        self.assertIs(parking_live.PARKING_LIVE_ENABLED, False)
        self.drive()
        for i in range(20):
            self.tick_with(T0 + i * 100 * MS, 1.0, "reverse")
        self.end()
        self.assertEqual(self.can_socks(), [])
        self.assertNotIn("parking_raw", self.uplink.types())

    def test_review_no_can_work_at_highway_speed(self):
        with mock.patch.object(parking_live, "PARKING_LIVE_ENABLED", True):
            self.drive()
            for i in range(50):
                self.tick_with(T0 + i * 100 * MS, 25.0, "drive")
            self.end()
        self.assertEqual(self.can_socks(), [])          # never opened, never read
        rec = self.uplink.data("parking_raw")[0]
        self.assertEqual((rec["canMsgs"], rec["socketOpens"]), (0, 0))

    def test_socket_opens_slow_closes_fast_and_reports(self):
        with mock.patch.object(parking_live, "PARKING_LIVE_ENABLED", True):
            self.drive()
            t = T0
            for i in range(30):                          # 3 s backing up, then 3 s at 25 m/s
                t += 100 * MS
                slow = i < 15
                self.tick_with(t, 1.0 if slow else 25.0, "reverse" if slow else "drive",
                               can=[can_msg(t, frame(0x1F6, f6(b=0x31) if slow else f6()))])
            opened = self.can_socks()
            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].closed)           # closed when the gate closed
            reads_at_close = opened[0].reads
            for i in range(20):
                t += 100 * MS
                self.tick_with(t, 25.0, "drive")
            self.assertEqual(opened[0].reads, reads_at_close)   # no reads while closed
            self.end()
        rec = self.uplink.data("parking_raw")[0]
        self.assertGreaterEqual(rec["groupBActiveReverse"], 13)
        self.assertEqual(rec["socketOpens"], 1)
        event = [e for e in self.uplink.events if e["type"] == "parking_raw"][0]
        self.assertEqual(schema_v2.problems(event), [])

    def test_review_after_a_cutoff_the_socket_is_closed_and_left_alone(self):
        with mock.patch.object(parking_live, "PARKING_LIVE_ENABLED", True):
            self.drive()
            cpu = FakeCpu()
            self.agent.parking.cpu_clock = cpu
            self.agent.parking.decode_can = lambda raw: (cpu.__setattr__("t", cpu.t + 0.3) or raw)
            t = T0
            for i in range(5):
                t += 100 * MS
                self.tick_with(t, 1.0, "reverse", can=[can_msg(t, frame(0x1F6, f6()))])
            self.assertEqual(self.agent.parking.disabled_reason, "tick_cpu_limit")
            sock = self.can_socks()[0]
            self.assertTrue(sock.closed)
            reads = sock.reads
            for i in range(10):
                t += 100 * MS
                self.tick_with(t, 1.0, "reverse")
            self.assertEqual(sock.reads, reads)
            self.assertEqual(len(self.can_socks()), 1)  # not reopened after the cutoff
            self.end()
        self.assertTrue(self.uplink.data("parking_raw")[0]["selfDisabled"])

    def test_review_stale_state_closes_the_socket(self):
        with mock.patch.object(parking_live, "PARKING_LIVE_ENABLED", True):
            self.drive()
            self.tick_with(T0, 1.0, "reverse")
            self.assertEqual(len(self.can_socks()), 1)
            for _ in range(20):                         # 2 s with no new carState
                self.agent.tick()
                self.mono.t = round(self.mono.t + 0.1, 6)
            self.assertTrue(self.can_socks()[0].closed)
            self.end()


if __name__ == "__main__":
    unittest.main()
