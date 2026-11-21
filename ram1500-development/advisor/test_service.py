"""Railway service as it is built: signed ingest and the nightly report, with no suggestion.

The suggestion path is tested in test_dormant_path.py, with the lock patched open,
and its lock in test_unreachable.py.
"""
import json
import unittest

from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from wire import sign_request

KEY = b"k" * 32
URL = "https://advisor.example"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, title, message, *, priority=3, actions=None):
        self.sent.append({"title": title, "message": message, "priority": priority, "actions": actions or []})
        return True


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


def minute(weave=1.0, **overrides):
    values = {"engagedMinutes": 1.0, "straightMinutes": 1.0, "weavePerStraightMinute": weave,
              "curveSamples": 0, "curveRelError": None, "steerWarningEpisodes": 0, "laneChanges": 0,
              "overrideEpisodes": 0, "hardBrakes": [], "speedMeanMs": 25.0,
              "trialActive": False, "trialsThisDrive": 0}
    values.update(overrides)
    return values


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.notifier = FakeNotifier()
        self.service = AdvisorService(MemoryStore(), self.notifier, KEY, URL, clock=self.clock)
        self.seq = 0

    def event(self, kind, drive="d1", data=None):
        self.seq += 1
        return self.service.handle_event({"type": kind, "driveId": drive, "id": f"e{self.seq}", "data": data or {}})

    def start(self, drive="d1", learned=0.25):
        self.event("drive_start", drive, {"learnedDelayS": learned, "baseActuatorDelayS": 0.1,
                                          "settings": {"LagdToggle": True, "LagdToggleDelay": 0.2,
                                                       "NeuralNetworkLateralControl": False}})

    def weave_minutes(self, n, drive="d1", **overrides):
        for _ in range(n):
            self.event("summary", drive, minute(weave=10.0, **overrides))

    def token(self, label, index=-1):
        actions = self.notifier.sent[index]["actions"]
        url = dict(actions)[label]
        return url.rsplit("/", 1)[1]


class ServiceTests(ServiceFixture):
    def test_weaving_minutes_are_stored_and_nothing_is_suggested(self):
        self.start()
        self.weave_minutes(7)
        self.assertEqual(len(self.service.store.get_json("drives/d1.json")["summaries"]), 7)
        self.assertEqual(self.notifier.sent, [])

    def test_duplicate_events_ignored(self):
        self.start()
        payload = {"type": "summary", "driveId": "d1", "id": "same", "data": minute()}
        self.service.handle_event(payload)
        self.service.handle_event(payload)
        self.assertEqual(len(self.service.store.get_json("drives/d1.json")["summaries"]), 1)

    def test_nightly_report_an_hour_after_last_drive(self):
        self.start()
        self.event("summary", data=minute(steerWarningEpisodes=1))
        for _ in range(9):
            self.event("summary", data=minute())
        self.event("drive_end")
        before = len(self.notifier.sent)
        self.clock.t += 1800
        self.service.run_nightly()
        self.assertEqual(len(self.notifier.sent), before)
        self.clock.t += 1900
        self.service.run_nightly()
        report = self.notifier.sent[-1]
        self.assertIn("Nightly", report["title"])
        self.assertEqual(report["actions"], [])                      # a report, with no button
        self.assertIn("No change suggested", report["message"])
        self.assertEqual(self.service.approvals_for_comma(), [])

    def test_new_drive_postpones_nightly_report(self):
        self.start()
        self.event("summary", data=minute())
        self.event("drive_end")
        self.clock.t += 1800
        self.start(drive="d2")
        self.clock.t += 3000
        self.service.run_nightly()
        self.assertFalse(any("Nightly" in n["title"] for n in self.notifier.sent))
        self.event("drive_end", drive="d2")
        self.clock.t += 3700
        self.service.run_nightly()
        self.assertTrue(any("Nightly" in n["title"] for n in self.notifier.sent))


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.service = AdvisorService(MemoryStore(), FakeNotifier(), KEY, URL)

    def test_unsigned_event_rejected(self):
        status, _, _ = dispatch(self.service, "POST", "/v1/events", {}, b"{}")
        self.assertEqual(status, 401)

    def test_signed_event_accepted(self):
        body = json.dumps({"type": "agent_start", "driveId": None, "id": "x1", "data": {}}).encode()
        status, _, _ = dispatch(self.service, "POST", "/v1/events", sign_request(KEY, "POST", "/v1/events", body), body)
        self.assertEqual(status, 200)

    def test_signed_approvals_listing(self):
        headers = sign_request(KEY, "GET", "/v1/approvals", b"")
        status, _, body = dispatch(self.service, "GET", "/v1/approvals", headers, b"")
        self.assertEqual((status, json.loads(body)), (200, {"approvals": []}))

    def test_signed_status_reports_last_event(self):
        body = json.dumps({"type": "agent_start", "driveId": None, "id": "x9", "data": {}}).encode()
        dispatch(self.service, "POST", "/v1/events", sign_request(KEY, "POST", "/v1/events", body), body)
        self.assertEqual(dispatch(self.service, "GET", "/v1/status", {}, b"")[0], 401)
        status, _, out = dispatch(self.service, "GET", "/v1/status", sign_request(KEY, "GET", "/v1/status", b""), b"")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(out)["lastEventType"], "agent_start")
        for event in ({"type": "drive_start", "driveId": "d9", "id": "x10", "data": {}},
                      {"type": "summary", "driveId": "d9", "id": "x11", "data": {"engagedMinutes": 0.8, "samples": 600}}):
            raw = json.dumps(event).encode()
            dispatch(self.service, "POST", "/v1/events", sign_request(KEY, "POST", "/v1/events", raw), raw)
        _, _, out = dispatch(self.service, "GET", "/v1/status", sign_request(KEY, "GET", "/v1/status", b""), b"")
        drive = json.loads(out)["lastDrive"]
        self.assertEqual((drive["summaries"], drive["last"]["engagedMinutes"]), (1, 0.8))

    def test_health_and_unknown_token(self):
        self.assertEqual(dispatch(self.service, "GET", "/healthz", {}, b"")[0], 200)
        self.assertEqual(dispatch(self.service, "POST", "/a/nope", {}, b"")[0], 404)


if __name__ == "__main__":
    unittest.main()
