"""Whole loop in one process: agent -> uplink -> service -> owner.

As built, a drive with a busy steering wheel (what the first version's rules
acted on) reaches no suggestion and no setting, even with an approval signed
with the real key. The same loop with the locks patched open is in
test_dormant_path.py.
"""
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as ns

import schema_v2
import synth
from agent import Agent
from applier import Applier, VehicleState
from approval import sign_approval
from journal import Journal, NonceLedger
from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from settings_guard import trial_values
from synth import SEC
from test_agent import BASE, BEFORE, LEARNED, Clock, FakeParams, FakeSock, parked, stored_delay
from test_service import FakeNotifier
from test_settings_snapshot import BUNDLE
from uplink import Uplink
from wire import sign_request

KEY = b"k" * 32
URL = "https://advisor.example"
T0 = 1_000 * SEC
START = {**BEFORE, "NeuralNetworkLateralControl": False}
INERT = {"result": "inert", "helperSha": "0123456789ab", "probeMs": 12}


def weaving(i, t_ns):
    """A straight at 25 m/s with the wheel swinging 3 degrees each way, four seconds a cycle."""
    return {"carState": dict(angle=3.0 * math.sin(2 * math.pi * 0.25 * (t_ns - T0) / SEC))}


class Loop:
    """Service, uplink, applier and agent wired together, with fake settings, sockets and clocks."""

    def __init__(self, test: unittest.TestCase):
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.wall, self.mono = Clock(50_000.0), Clock(0.0)
        self.notifier = FakeNotifier()
        self.service = AdvisorService(MemoryStore(), self.notifier, KEY, URL, clock=self.wall)
        self.requests = []                 # (method, path) of everything the Comma side asked the service
        self.sent = []                     # every event it posted
        self.headers = []                  # the header names of every request

        def opener(method, url, headers, body, timeout):
            self.requests.append((method, url[len(URL):]))
            self.headers.append(sorted(headers))
            if method == "POST":
                self.sent.append(json.loads(body))
            status, _, out = dispatch(self.service, method, url[len(URL):], headers, body)
            return status, out

        # The pacing of the uplink is tested on its own; here it never waits.
        self.uplink = Uplink(URL, KEY, opener=opener, monotonic=self.mono, sleep=lambda seconds: None)
        self.params = FakeParams({"IsOnroad": False, **START, "LagdValueCache": LEARNED, "AutoLaneChangeTimer": 0,
                                  "ModelManager_ActiveBundle": dict(BUNDLE), "GitCommit": "0123456789abcdef",
                                  "CarParams": str(BASE).encode(), "LiveDelay": stored_delay(LEARNED)})
        self.applier = Applier(self.params, Journal(Path(folder.name) / "trial.json"),
                               NonceLedger(Path(folder.name) / "nonces.json"), KEY)
        self.socks = {}
        self.probe = dict(INERT)           # what the delay probe finds on this build
        self.log_ns = T0

        def make_sock(stream):
            self.socks[stream] = FakeSock()
            return self.socks[stream]

        def decode_delay(raw):
            fields = json.loads(raw)
            return synth.live_delay(delay=fields["delay"], status=fields["status"], blocks=fields["blocks"])

        self.agent = Agent(params=self.params, applier=self.applier, uplink=self.uplink, socket_factory=make_sock,
                           decode=lambda stream, raw: raw, slot_check=lambda: True,
                           car_params_decode=lambda raw: ns(steerActuatorDelay=float(raw.decode())),
                           live_delay_decode=decode_delay, delay_probe=lambda: dict(self.probe),
                           wall_clock=self.wall, monotonic=self.mono, subscribe_delay_s=0.0)

    def sent_types(self):
        return [event["type"] for event in self.sent]

    def play(self, n, change=None):
        """`n` ticks of driving; the uplink runs every five seconds, as on the Comma."""
        times = synth.ticks(n, 100, self.log_ns)
        self.log_ns = times[-1] + 100_000_000
        for i, batch in enumerate(synth.drive(times, change)):
            for stream, message in batch.items():
                self.socks[stream].queue.append(message)
            self.agent.tick()
            self.mono.t = round(self.mono.t + 0.1, 6)
            if i % 50 == 0:
                self.uplink.run_once()

    def weaving_drive(self, ticks=3100):
        """A little over 5 minutes with a busy steering wheel: what the old live rule acted on."""
        self.agent.start()
        self.params.values["IsOnroad"] = True
        self.agent.tick()
        self.play(ticks, weaving)
        self.uplink.run_once()

    def park(self):
        self.mono.t += 2
        self.play(1, parked)

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
        busy = drive["summaries"][-1]["swings"]
        self.assertGreater(busy["n"] / (busy["straightS"] / 60.0), 6.0)     # the wheel is as busy as the old rule asked

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
        loop.play(1, weaving)
        loop.park()
        loop.park()
        self.assertTrue(loop.agent.vehicle_state().parked())
        self.assertEqual(loop.applier.apply(approval, VehicleState(in_park=True, speed_ms=0.0, engaged=False),
                                            now=loop.wall.t, learned_delay_s=LEARNED, base_actuator_delay_s=BASE,
                                            delay_probe="effective"),
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
        self.assertEqual(drive["start"]["delay"]["learnedS"], round(LEARNED, 4))
        self.assertEqual(drive["ready"]["delayProbe"], INERT)
        self.assertEqual((drive["end"]["windows"], drive["end"]["listening"]), (len(drive["summaries"]), True))
        self.assertEqual(drive["events"], [])
        self.assertEqual(set(loop.sent_types()), {"agent_start", "drive_start", "drive_ready", "summary", "drive_end"})
        self.assertEqual(loop.with_buttons(), [])

    def test_what_reaches_the_service_is_schema_v2_and_nothing_else(self):
        loop = Loop(self)
        loop.weaving_drive(ticks=1300)
        loop.end_drive()
        self.assertEqual(loop.uplink.queue_len(), 0)                    # everything was delivered
        self.assertEqual(loop.sent_types()[:3], ["agent_start", "drive_start", "drive_ready"])
        self.assertEqual(loop.sent_types()[-1], "drive_end")
        for event in loop.sent:
            with self.subTest(type=event["type"], id=event["id"]):
                self.assertEqual(schema_v2.problems(event), [])
        # The only headers handed to the HTTP library.
        self.assertEqual({tuple(names) for names in loop.headers}, {("Content-Type", "X-Advisor-Sig", "X-Advisor-Ts")})
        start = next(e for e in loop.sent if e["type"] == "drive_start")["data"]
        self.assertEqual((start["delay"]["learnedS"], start["agent"]["applyEnabled"]), (round(LEARNED, 4), False))
        ready = next(e for e in loop.sent if e["type"] == "drive_ready")["data"]
        self.assertEqual(ready["delayProbe"], INERT)


if __name__ == "__main__":
    unittest.main()
