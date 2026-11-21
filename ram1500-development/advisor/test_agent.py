"""Agent loop rules, with fake params, sockets, uplink and clocks."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as ns

from agent import Agent
from applier import Applier
from approval import sign_approval
from journal import Journal, NonceLedger

KEY = b"k" * 32
LIVE = {"LagdToggle": False, "LagdToggleDelay": 0.1}


class FakeParams:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value

    def onroad(self):
        return self.values.get("IsOnroad") is True


class FakeUplink:
    def __init__(self):
        self.events = []
        self.incoming = []

    def send(self, event):
        self.events.append(event)

    def approvals(self):
        out, self.incoming = self.incoming, []
        return out

    def types(self):
        return [e["type"] for e in self.events]


class FakeSock:
    def __init__(self):
        self.queue = []

    def receive(self, non_blocking=True):
        return self.queue.pop(0) if self.queue else None


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class AgentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.params = FakeParams({"IsOnroad": False, "LagdToggle": True, "LagdToggleDelay": 0.2,
                                  "LagdValueCache": 0.25, "NeuralNetworkLateralControl": False,
                                  "CarParams": b"0.1"})
        self.applier = Applier(self.params, Journal(Path(folder.name) / "trial.json"),
                               NonceLedger(Path(folder.name) / "nonces.json"), KEY)
        self.uplink = FakeUplink()
        self.socks = {}
        self.decoded = []
        self.mono = Clock(100.0)
        self.wall = Clock(1000.0)
        self.slots_ok = True
        self.probe = (0.0, 20.0)
        self.agent = Agent(params=self.params, applier=self.applier, uplink=self.uplink,
                           socket_factory=self.make_sock, decode=self.decode,
                           slot_check=lambda: self.slots_ok,
                           car_params_decode=lambda raw: ns(steerActuatorDelay=float(raw.decode())),
                           wall_clock=self.wall, monotonic=self.mono,
                           resource_probe=lambda: self.probe, subscribe_delay_s=0.0)
        self.count = 0

    def make_sock(self, service):
        self.socks[service] = FakeSock()
        return self.socks[service]

    def decode(self, service, raw):
        self.decoded.append(service)
        return raw

    def approval(self, change=LIVE, mode="live"):
        self.count += 1
        return sign_approval(KEY, suggestion_id=f"s{self.count}", mode=mode, change=change,
                             issued_at=self.wall.t, nonce=f"{self.count:016d}")

    def drive(self):
        self.params.values["IsOnroad"] = True
        self.agent.tick()

    def feed(self, t_ns, gear="drive", speed=25.0, enabled=True):
        self.socks["carControl"].queue.append((t_ns, ns(latActive=enabled, enabled=enabled)))
        self.socks["controlsState"].queue.append((t_ns, ns(desiredCurvature=0.0, curvature=0.0)))
        self.socks["carState"].queue.append((t_ns, ns(vEgo=speed, aEgo=0.0, steeringRateDeg=0.0,
                                                      steeringPressed=False, gearShifter=gear)))

    def test_start_restores_open_trial_when_offroad(self):
        self.assertEqual(self.applier.apply(self.approval(), self.agent.vehicle_state(), now=self.wall.t,
                                            learned_delay_s=0.25, base_actuator_delay_s=0.1), "applied")
        fresh = Agent(params=self.params, applier=Applier(self.params, self.applier.journal, self.applier.nonces, KEY),
                      uplink=self.uplink, socket_factory=self.make_sock, decode=self.decode, slot_check=lambda: True)
        fresh.start()
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.assertEqual(self.uplink.events[-1]["data"], {"restore": "restored"})

    def test_drive_start_and_end(self):
        self.drive()
        start = self.uplink.events[0]
        self.assertEqual(start["type"], "drive_start")
        self.assertEqual((start["data"]["learnedDelayS"], start["data"]["baseActuatorDelayS"]), (0.25, 0.1))
        self.params.values["IsOnroad"] = False
        self.agent.tick()
        self.assertEqual(self.uplink.events[-1]["type"], "drive_end")
        self.assertEqual(self.uplink.events[-1]["driveId"], start["driveId"])

    def test_waits_before_subscribing(self):
        self.agent.subscribe_delay_s = 30.0
        self.drive()
        self.assertIsNone(self.agent.socks)
        self.mono.t += 31
        self.agent.tick()
        self.assertEqual(set(self.socks), {"carState", "carControl", "controlsState", "carOutput",
                                           "radarState", "drivingModelData", "onroadEvents"})

    def test_full_slots_disable_listening(self):
        self.slots_ok = False
        self.drive()
        self.assertIsNone(self.agent.socks)
        self.assertIn("listening_disabled", self.uplink.types())

    def test_one_summary_per_minute(self):
        self.drive()
        for i in range(605):
            self.feed(i * 100_000_000)
            self.agent.tick()
        summaries = [e for e in self.uplink.events if e["type"] == "summary"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["data"]["engagedMinutes"], 1.0)
        self.assertFalse(summaries[0]["data"]["trialActive"])

    def test_control_message_a_few_ms_newer_still_counts_as_engaged(self):
        # Regression for the 11/20 test drive: engaged read ~0.5/min because carControl
        # for the same cycle is a few ms newer than carState.
        self.drive()
        for i in range(605):
            t = i * 100_000_000
            self.socks["carState"].queue.append((t, ns(vEgo=30.0, aEgo=0.0, steeringRateDeg=0.0,
                                                       steeringPressed=False, gearShifter="drive")))
            self.socks["carControl"].queue.append((t + 5_000_000, ns(latActive=True, enabled=True)))
            self.socks["controlsState"].queue.append((t + 6_000_000, ns(desiredCurvature=0.0, curvature=0.0)))
            self.agent.tick()
        summary = next(e for e in self.uplink.events if e["type"] == "summary")
        self.assertEqual(summary["data"]["engagedMinutes"], 1.0)

    def test_decodes_only_newest_message_per_service(self):
        self.drive()
        for i in range(5):
            self.socks["carState"].queue.append((i, ns(vEgo=0.0)))
        self.agent.tick()
        self.assertEqual(self.decoded.count("carState"), 1)

    def test_approval_waits_for_park_then_applies(self):
        self.drive()
        self.feed(0, gear="drive", speed=20.0)
        self.agent.tick()
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.feed(100_000_000, gear="park", speed=0.0, enabled=False)
        self.mono.t += 2
        self.agent.tick()
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.1)
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "applied")

    def test_unparked_approval_dropped_at_expiry(self):
        self.drive()
        self.feed(0, gear="drive", speed=20.0)
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.wall.t += 601
        self.mono.t += 2
        self.feed(100_000_000, gear="drive", speed=20.0)
        self.agent.tick()
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "refused:expired")
        self.assertEqual(self.agent.pending, {})

    def test_stale_vehicle_state_is_not_parked(self):
        self.drive()
        self.feed(0, gear="park", speed=0.0, enabled=False)
        self.agent.tick()
        self.mono.t += 5
        self.assertFalse(self.agent.vehicle_state().parked())

    def test_offroad_counts_as_parked_for_nightly(self):
        self.uplink.incoming.append(self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly"))
        self.agent.tick()
        self.assertIs(self.params.values["NeuralNetworkLateralControl"], True)

    def test_owner_override_ends_trial(self):
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.params.values["LagdToggle"] = True
        self.agent.tick()
        self.assertIn("trial_ended", self.uplink.types())

    def test_drive_end_restores_trial(self):
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.drive()
        self.params.values["IsOnroad"] = False
        self.agent.tick()
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.assertEqual(self.uplink.events[-1]["data"], {"restore": "restored"})

    def test_cpu_limit_stops_listening(self):
        self.drive()
        for cpu in (0.0, 2.0, 4.0):   # 20% of one core per 10 s window
            self.probe = (cpu, 20.0)
            self.agent.tick()
            self.mono.t += 10
        self.assertFalse(self.agent.listening)
        self.assertEqual(self.uplink.events[-1]["data"]["reason"], "self_limit")


if __name__ == "__main__":
    unittest.main()
