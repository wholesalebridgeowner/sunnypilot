"""Whole loop in one process: agent -> uplink -> service -> owner approval -> agent applies in Park."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as ns

from agent import Agent
from applier import Applier
from journal import Journal, NonceLedger
from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from settings_guard import trial_values
from test_agent import Clock, FakeParams, FakeSock
from test_service import FakeNotifier
from uplink import Uplink

KEY = b"k" * 32
URL = "https://advisor.example"


class EndToEndTests(unittest.TestCase):
    def test_weaving_drive_to_parked_apply_and_restore(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        wall, mono = Clock(50_000.0), Clock(0.0)
        notifier = FakeNotifier()
        service = AdvisorService(MemoryStore(), notifier, KEY, URL, clock=wall)

        def opener(method, url, headers, body, timeout):
            status, _, out = dispatch(service, method, url[len(URL):], headers, body)
            return status, out

        uplink = Uplink(URL, KEY, opener=opener)
        params = FakeParams({"IsOnroad": False, "LagdToggle": True, "LagdToggleDelay": 0.2, "LagdValueCache": 0.25,
                             "NeuralNetworkLateralControl": False, "CarParams": b"0.1"})
        applier = Applier(params, Journal(Path(folder.name) / "trial.json"),
                          NonceLedger(Path(folder.name) / "nonces.json"), KEY)
        socks = {}

        def make_sock(service_name):
            socks[service_name] = FakeSock()
            return socks[service_name]

        agent = Agent(params=params, applier=applier, uplink=uplink, socket_factory=make_sock,
                      decode=lambda service_name, raw: raw, slot_check=lambda: True,
                      car_params_decode=lambda raw: ns(steerActuatorDelay=float(raw.decode())),
                      wall_clock=wall, monotonic=mono, subscribe_delay_s=0.0)
        agent.start()

        def feed(i, gear="drive", speed=25.0, enabled=True):
            t = i * 100_000_000
            rate = 5.0 if (i // 10) % 2 == 0 else -5.0
            socks["carControl"].queue.append((t, ns(latActive=enabled, enabled=enabled)))
            socks["controlsState"].queue.append((t, ns(desiredCurvature=0.0, curvature=0.0)))
            socks["carState"].queue.append((t, ns(vEgo=speed, aEgo=0.0, steeringRateDeg=rate,
                                                  steeringPressed=False, gearShifter=gear)))

        params.values["IsOnroad"] = True
        agent.tick()
        for i in range(3100):            # a little over 5 minutes of weaving at 10 Hz
            feed(i)
            agent.tick()
            mono.t += 0.1
            if i % 50 == 0:
                uplink.run_once()
        uplink.run_once()
        live = [n for n in notifier.sent if n["actions"]]
        self.assertEqual(len(live), 1)
        approve_token = dict(live[0]["actions"])["Approve"].rsplit("/", 1)[1]
        self.assertEqual(service.approve(approve_token)[0], 200)

        uplink.run_once()                # agent receives the approval while still driving
        feed(3100)
        agent.tick()
        self.assertIs(params.values["LagdToggle"], True)        # not applied while moving

        feed(3101, gear="park", speed=0.0, enabled=False)
        mono.t += 2
        agent.tick()
        expected = trial_values(0.25, 0.1, -1)
        self.assertIs(params.values["LagdToggle"], False)
        self.assertEqual(params.values["LagdToggleDelay"], expected["LagdToggleDelay"])
        uplink.run_once()
        self.assertEqual(notifier.sent[-1]["title"], "Ram advisor: Applied")

        params.values["IsOnroad"] = False
        agent.tick()
        uplink.run_once()
        self.assertIs(params.values["LagdToggle"], True)
        self.assertEqual(params.values["LagdToggleDelay"], 0.2)
        drives = [service.store.get_json(k) for k in service.store.objects if k.startswith("drives/")]
        self.assertEqual(len(drives), 1)
        self.assertTrue(drives[0]["ended"])
        self.assertEqual(drives[0]["trial"]["reason"], "weaving_on_straight_road")
        self.assertEqual(drives[0]["restore"], "restored")


if __name__ == "__main__":
    unittest.main()
