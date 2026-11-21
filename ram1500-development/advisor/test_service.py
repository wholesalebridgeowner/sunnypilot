"""Railway service: signed ingest, live suggestions, approvals, nightly report."""
import json
import unittest

from approval import verify_approval
from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from settings_guard import trial_values
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


class ServiceTests(unittest.TestCase):
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

    def test_weaving_creates_one_live_suggestion_with_sound(self):
        self.start()
        self.weave_minutes(7)
        suggestions = [n for n in self.notifier.sent if n["actions"]]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["priority"], 4)
        self.assertEqual([label for label, _ in suggestions[0]["actions"]], ["Approve", "Decline"])
        self.assertIn("Park", suggestions[0]["message"])

    def test_approve_signs_the_guarded_change_once(self):
        self.start()
        self.weave_minutes(5)
        token = self.token("Approve")
        self.assertEqual(self.service.approve(token)[0], 200)
        approvals = self.service.approvals_for_comma()
        self.assertEqual(len(approvals), 1)
        fields = verify_approval(KEY, approvals[0], now=self.clock.t, used_nonces=set())
        self.assertEqual(fields["change"], trial_values(0.25, 0.1, -1))
        self.assertEqual(fields["mode"], "live")
        self.assertEqual(self.service.approve(token)[0], 409)

    def test_decline_blocks_the_same_reason_for_this_drive(self):
        self.start()
        self.weave_minutes(5)
        self.assertEqual(self.service.decline(self.token("Decline"))[0], 200)
        self.weave_minutes(10)
        self.assertEqual(len([n for n in self.notifier.sent if n["actions"]]), 1)
        self.assertEqual(self.service.approvals_for_comma(), [])

    def test_live_suggestions_can_be_switched_off(self):
        self.service.live_enabled = False
        self.start()
        self.weave_minutes(7)
        self.assertEqual([n for n in self.notifier.sent if n["actions"]], [])

    def test_no_live_suggestion_without_learned_delay(self):
        self.start(learned=None)
        self.weave_minutes(7)
        self.assertEqual([n for n in self.notifier.sent if n["actions"]], [])

    def test_apply_result_records_trial_and_confirms(self):
        self.start()
        self.weave_minutes(5)
        self.service.approve(self.token("Approve"))
        sid = self.service.approvals_for_comma()[0]["suggestion_id"]
        self.event("apply_result", data={"suggestionId": sid, "status": "applied"})
        self.assertEqual(self.service.approvals_for_comma(), [])
        self.assertIn("Applied", self.notifier.sent[-1]["title"])
        self.assertEqual(self.service.store.get_json("drives/d1.json")["trial"]["reason"], "weaving_on_straight_road")

    def test_approvals_expire_for_the_comma(self):
        self.start()
        self.weave_minutes(5)
        self.service.approve(self.token("Approve"))
        self.clock.t += 601
        self.assertEqual(self.service.approvals_for_comma(), [])

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
        self.assertEqual([label for label, _ in report["actions"]], ["Approve", "Decline"])
        self.service.approve(self.token("Approve"))
        fields = verify_approval(KEY, self.service.approvals_for_comma()[0], now=self.clock.t + 11 * 3600,
                                 used_nonces=set())
        self.assertEqual(fields["change"], {"NeuralNetworkLateralControl": True})

    def test_nightly_suggestions_can_be_switched_off(self):
        self.service.nightly_suggestions = False
        self.start()
        self.event("summary", data=minute(steerWarningEpisodes=1))
        for _ in range(9):
            self.event("summary", data=minute())
        self.event("drive_end")
        self.clock.t += 3700
        self.service.run_nightly()
        report = self.notifier.sent[-1]
        self.assertIn("Nightly", report["title"])
        self.assertEqual(report["actions"], [])
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
