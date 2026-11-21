"""Whole loop in one process: agent -> uplink -> service -> owner.

As built, a drive that the old rules would have acted on reaches no suggestion
and no setting, even with an approval signed with the real key. The same loop
with the locks patched open is in test_dormant_path.py.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as ns

from agent import Agent
from applier import Applier, VehicleState
from approval import sign_approval
from journal import Journal, NonceLedger
from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from settings_guard import trial_values
from test_agent import Clock, FakeParams, FakeSock
from test_service import FakeNotifier
from uplink import Uplink
from wire import sign_request

KEY = b"k" * 32
URL = "https://advisor.example"
LEARNED = 0.32812345678901234      # an invented value with the precision a learned delay really has
BASE = 0.2
START = {"LagdToggle": True, "LagdToggleDelay": 0.2, "NeuralNetworkLateralControl": False}


class Loop:
    """Service, uplink, applier and agent wired together, with fake settings, sockets and clocks."""

    def __init__(self, test: unittest.TestCase):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.wall, self.mono = Clock(50_000.0), Clock(0.0)
        self.notifier = FakeNotifier()
        self.service = AdvisorService(MemoryStore(), self.notifier, KEY, URL, clock=self.wall)
        self.requests = []                 # (method, path) of everything the Comma side asked the service
        self.sent_types = []               # type of every event it posted

        def opener(method, url, headers, body, timeout):
            self.requests.append((method, url[len(URL):]))
            if method == "POST":
                self.sent_types.append(json.loads(body)["type"])
            status, _, out = dispatch(self.service, method, url[len(URL):], headers, body)
            return status, out

        self.uplink = Uplink(URL, KEY, opener=opener)
        self.params = FakeParams({"IsOnroad": False, "LagdValueCache": LEARNED, "CarParams": str(BASE).encode(),
                                  **START})
        self.applier = Applier(self.params, Journal(Path(folder.name) / "trial.json"),
                               NonceLedger(Path(folder.name) / "nonces.json"), KEY)
        self.socks = {}

        def make_sock(service_name):
            self.socks[service_name] = FakeSock()
            return self.socks[service_name]

        self.agent = Agent(params=self.params, applier=self.applier, uplink=self.uplink, socket_factory=make_sock,
                           decode=lambda service_name, raw: raw, slot_check=lambda: True,
                           car_params_decode=lambda raw: ns(steerActuatorDelay=float(raw.decode())),
                           wall_clock=self.wall, monotonic=self.mono, subscribe_delay_s=0.0)

    def feed(self, i, gear="drive", speed=25.0, enabled=True):
        t = i * 100_000_000
        rate = 5.0 if (i // 10) % 2 == 0 else -5.0
        self.socks["carControl"].queue.append((t, ns(latActive=enabled, enabled=enabled)))
        self.socks["controlsState"].queue.append((t, ns(desiredCurvature=0.0, curvature=0.0)))
        self.socks["carState"].queue.append((t, ns(vEgo=speed, aEgo=0.0, steeringRateDeg=rate,
                                                   steeringPressed=False, gearShifter=gear)))

    def weaving_drive(self, ticks=3100):
        """A little over 5 minutes of weaving at 10 Hz: what the old live rule acted on."""
        self.agent.start()
        self.params.values["IsOnroad"] = True
        self.agent.tick()
        for i in range(ticks):
            self.feed(i)
            self.agent.tick()
            self.mono.t += 0.1
            if i % 50 == 0:
                self.uplink.run_once()
        self.uplink.run_once()

    def park(self, i):
        self.feed(i, gear="park", speed=0.0, enabled=False)
        self.mono.t += 2
        self.agent.tick()

    def end_drive(self):
        self.params.values["IsOnroad"] = False
        self.agent.tick()
        self.uplink.run_once()

    def stored(self, prefix):
        return [self.service.store.get_json(key) for key in self.service.store.objects if key.startswith(prefix)]

    def with_buttons(self):
        return [n for n in self.notifier.sent if n["actions"]]


class EndToEndTests(unittest.TestCase):
    def test_weaving_drive_reaches_no_suggestion_and_no_setting(self):
        loop = Loop(self)
        loop.weaving_drive()
        drive = loop.stored("drives/")[0]
        self.assertGreaterEqual(len(drive["summaries"]), 5)
        self.assertGreater(drive["summaries"][-1]["weavePerStraightMinute"], 6.0)   # the old rule's condition holds
        self.assertEqual(drive["learnedDelayS"], LEARNED)

        # No suggestion record, no notification with buttons.
        self.assertEqual(loop.with_buttons(), [])
        self.assertEqual(loop.stored("suggestions/") + loop.stored("tokens/"), [])

        # A suggestion that an earlier service version stored and approved, signed with the real key.
        change = trial_values(LEARNED, BASE, -1)
        approval = sign_approval(KEY, suggestion_id="feedfacefeedface", mode="live", change=change,
                                 issued_at=loop.wall.t, nonce="0123456789abcdef0123456789abcdef")
        record = {"id": "feedfacefeedface", "mode": "live", "change": change, "reason": "weaving_on_straight_road",
                  "driveId": drive["driveId"], "created": loop.wall.t, "status": "approved", "approval": approval,
                  "approveToken": "approve-token-0123456789", "declineToken": "decline-token-0123456789"}
        store = loop.service.store
        store.put_json("suggestions/feedfacefeedface.json", record)
        store.put_json("tokens/approve-token-0123456789.json", {"sid": record["id"], "action": "approve"})
        store.put_json("tokens/decline-token-0123456789.json", {"sid": record["id"], "action": "decline"})
        state = store.get_json("state.json")
        state["approved"] = [record["id"]]
        store.put_json("state.json", state)

        # The service hands it to nobody and its links answer "not found".
        self.assertEqual(dispatch(loop.service, "POST", "/a/approve-token-0123456789", {}, b"")[0], 404)
        self.assertEqual(dispatch(loop.service, "POST", "/d/decline-token-0123456789", {}, b"")[0], 404)
        status, _, body = dispatch(loop.service, "GET", "/v1/approvals",
                                   sign_request(KEY, "GET", "/v1/approvals", b""), b"")
        self.assertEqual((status, json.loads(body)), (200, {"approvals": []}))

        # The Comma never asks for approvals, and ignores one that is put in front of it, parked or not.
        loop.uplink.run_once()
        loop.uplink.inbox.append(approval)
        loop.feed(3100)
        loop.agent.tick()
        loop.park(3101)
        loop.park(3102)
        self.assertEqual(loop.applier.apply(approval, VehicleState(in_park=True, speed_ms=0.0, engaged=False),
                                            now=loop.wall.t, learned_delay_s=LEARNED, base_actuator_delay_s=BASE),
                         "refused:apply_disabled")
        self.assertEqual(loop.params.writes, [])
        self.assertEqual({key: loop.params.values[key] for key in START}, START)
        self.assertFalse(loop.applier.journal.is_open())

        loop.end_drive()
        self.assertEqual([method for method, _ in loop.requests if method != "POST"], [])   # not one GET
        self.assertEqual({path for _, path in loop.requests}, {"/v1/events"})
        self.assertEqual(loop.params.writes, [])
        drive = loop.stored("drives/")[0]
        self.assertTrue(drive["ended"])
        self.assertEqual(drive["restore"], "nothing_to_restore")
        self.assertIsNone(drive["trial"])
        self.assertEqual(set(loop.sent_types), {"agent_start", "drive_start", "summary", "drive_end"})
        self.assertEqual(loop.with_buttons(), [])


if __name__ == "__main__":
    unittest.main()
