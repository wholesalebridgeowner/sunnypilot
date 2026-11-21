"""Railway service as it is built: signed ingest, storage of what a drive sends, and a report with no suggestion.

The suggestion path is tested in test_dormant_path.py, with the lock patched open,
and its lock in test_unreachable.py.
"""
import importlib.util
import json
import unittest
from pathlib import Path

import service_app
from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from wire import sign_request

KEY = b"k" * 32
URL = "https://advisor.example"
SETTINGS = {"lagdToggle": True, "lagdToggleDelayS": 0.2, "nnlc": False, "laneChangeTimer": 0,
            "modelBundleId": "0123456789ab", "build": "ba9876543210"}
START = {"resumed": False, "settings": SETTINGS,
         "delay": {"learnedS": 0.3457, "estimateS": 0.3457, "estimateStdS": 0.01, "validBlocks": 30,
                   "status": "estimated", "cacheS": 0.3457, "baseActuatorS": 0.2, "outOfBand": False},
         "agent": {"version": "2.0", "measures": ["steering"], "applyEnabled": False}}
READY = {"delayProbe": {"result": "inert", "helperSha": "0123456789ab", "probeMs": 12}, "nnlcModel": "ram",
         "nnlcFuzzy": False}
END = {"windows": 10, "listening": True, "restore": "nothing_to_restore", "failed": [], "settings": SETTINGS,
       "totals": {"windowS": 600.0, "unobservedS": 0.0, "steeringS": 540.0, "movingS": 600.0},
       "delay": START["delay"], "delayProbe": READY["delayProbe"], "truncatedLaneChanges": 0}


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


def minute(w=0, **overrides):
    """One summary as the agent sends it (the fields the service reads, and a few more)."""
    values = {"w": w, "tMonoS": 1000.0 + 60 * w, "n": 590, "windowS": 60.0, "unobservedS": 0.0, "steeringS": 54.0,
              "movingS": 60.0, "warnEvents": 0, "brakeEpisodes": 0, "lcCompleted": 0,
              "health": {"gapN": 0, "modelGapN": 0, "staleS": {}, "staleSteerS": {}}}
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
        return self.service.handle_event({"v": 2, "type": kind, "driveId": drive, "id": f"e{self.seq}",
                                          "data": data or {}})

    def start(self, drive="d1"):
        self.event("drive_start", drive, START)

    def minutes(self, n, drive="d1", **overrides):
        for w in range(n):
            self.event("summary", drive, minute(w, **overrides))

    def stored(self, drive="d1"):
        return self.service.store.get_json(f"drives/{drive}.json")


class ServiceTests(ServiceFixture):
    def test_what_a_drive_sends_is_stored_and_nothing_is_suggested(self):
        self.start()
        self.event("drive_ready", data=READY)
        self.minutes(7)
        self.event("drive_end", data=END)
        drive = self.stored()
        self.assertEqual((drive["start"], drive["ready"], drive["end"]), (START, READY, END))
        self.assertEqual([s["w"] for s in drive["summaries"]], list(range(7)))
        self.assertEqual((drive["ended"], drive["restore"]), (True, "nothing_to_restore"))
        self.assertEqual(self.notifier.sent, [])

    def test_duplicate_events_ignored(self):
        self.start()
        payload = {"v": 2, "type": "summary", "driveId": "d1", "id": "same", "data": minute()}
        self.service.handle_event(payload)
        self.service.handle_event(payload)
        self.assertEqual(len(self.stored()["summaries"]), 1)

    def test_every_record_of_a_long_drive_is_kept(self):
        # Three hours of driving with 130 braking, lane-change and limit records: none is dropped.
        self.start()
        kinds = ("brake", "lane_change", "limit_event")
        for i in range(130):
            self.event(kinds[i % 3], data={"n": i})
        self.event("settings_change", data={"settings": SETTINGS, "changed": ["laneChangeTimer"]})
        self.event("listening_disabled", data={"reason": "self_limit", "cpuPct": 16.0, "rssMb": 20.0})
        events = self.stored()["events"]
        self.assertEqual(len(events), 132)
        self.assertEqual([e["data"]["n"] for e in events[:130]], list(range(130)))
        self.assertEqual([e["type"] for e in events[130:]], ["settings_change", "listening_disabled"])

    def test_report_an_hour_after_the_last_drive(self):
        self.start()
        self.minutes(10, warnEvents=1)
        self.event("drive_end", data=END)
        before = len(self.notifier.sent)
        self.clock.t += 1800
        self.assertFalse(self.service.run_nightly())
        self.assertEqual(len(self.notifier.sent), before)
        self.clock.t += 1900
        self.assertTrue(self.service.run_nightly())
        report = self.notifier.sent[-1]
        self.assertEqual(report["title"], "Ram drive report")
        self.assertEqual(report["actions"], [])                      # a report, with no button
        self.assertEqual(report["message"].splitlines()[-1], "No changes are suggested. This report only measures.")
        self.assertEqual(self.service.approvals_for_comma(), [])
        self.assertFalse(self.service.run_nightly())                 # sent once

    def test_new_drive_postpones_the_report(self):
        self.start()
        self.minutes(1)
        self.event("drive_end", data=END)
        self.clock.t += 1800
        self.start(drive="d2")
        self.clock.t += 3000
        self.service.run_nightly()
        self.assertEqual(self.notifier.sent, [])
        self.event("drive_end", drive="d2", data=END)
        self.clock.t += 3700
        self.service.run_nightly()
        self.assertEqual([n["title"] for n in self.notifier.sent], ["Ram drive report"])
        self.assertIn("2 drives", self.notifier.sent[0]["message"])

    def test_the_report_holds_no_advice_and_nothing_of_the_first_version(self):
        self.start()
        self.minutes(10, warnEvents=3)
        self.event("drive_end", data=END)
        self.clock.t += 3700
        self.service.run_nightly()
        message = self.notifier.sent[-1]["message"]
        for gone in ("Suggestion", "Trial", "Pull over", "following-distance", "Hard stops", "No change suggested",
                     "neural-network steering for your next drive"):
            self.assertNotIn(gone, message)


class RemovedTests(unittest.TestCase):
    """What the first version's suggestion rules needed is gone from the service."""

    def test_the_constructor_takes_no_switch(self):
        for switch in ("live_enabled", "nightly_suggestions"):
            with self.subTest(switch=switch), self.assertRaises(TypeError):
                AdvisorService(MemoryStore(), FakeNotifier(), KEY, URL, **{switch: True})

    def test_no_switch_is_read_from_the_environment_and_no_old_text_is_left(self):
        source = Path(service_app.__file__).read_text(encoding="utf-8")
        for gone in ("LIVE_SUGGESTIONS", "NIGHTLY_SUGGESTIONS", "live_enabled", "nightly_suggestions", "Pull over",
                     "Nightly review", "weavePerStraightMinute", "curveRelError", "REASON_TEXT", "TITLE_LIVE",
                     "_maybe_suggest_live"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, source)

    def test_the_rule_and_metric_modules_of_the_first_version_do_not_exist(self):
        self.assertIsNone(importlib.util.find_spec("rules"))
        for name in ("live_suggestion", "nightly_recommendation", "trial_values", "REASON_TEXT", "TITLE_LIVE"):
            self.assertFalse(hasattr(service_app, name), name)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.service = AdvisorService(MemoryStore(), FakeNotifier(), KEY, URL)

    def post(self, event):
        raw = json.dumps(event).encode()
        return dispatch(self.service, "POST", "/v1/events", sign_request(KEY, "POST", "/v1/events", raw), raw)

    def test_unsigned_event_rejected(self):
        status, _, _ = dispatch(self.service, "POST", "/v1/events", {}, b"{}")
        self.assertEqual(status, 401)

    def test_signed_event_accepted(self):
        self.assertEqual(self.post({"v": 2, "type": "agent_start", "driveId": None, "id": "x1", "data": {}})[0], 200)

    def test_signed_approvals_listing(self):
        headers = sign_request(KEY, "GET", "/v1/approvals", b"")
        status, _, body = dispatch(self.service, "GET", "/v1/approvals", headers, b"")
        self.assertEqual((status, json.loads(body)), (200, {"approvals": []}))

    def test_signed_status_reports_last_event(self):
        self.post({"v": 2, "type": "agent_start", "driveId": None, "id": "x9", "data": {}})
        self.assertEqual(dispatch(self.service, "GET", "/v1/status", {}, b"")[0], 401)
        status, _, out = dispatch(self.service, "GET", "/v1/status", sign_request(KEY, "GET", "/v1/status", b""), b"")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(out)["lastEventType"], "agent_start")
        for event in ({"v": 2, "type": "drive_start", "driveId": "d9", "id": "x10", "data": START},
                      {"v": 2, "type": "summary", "driveId": "d9", "id": "x11", "data": minute(steeringS=48.0)},
                      {"v": 2, "type": "listening_disabled", "driveId": "d9", "id": "x12",
                       "data": {"reason": "self_limit", "cpuPct": 16.0, "rssMb": 20.0}}):
            self.post(event)
        _, _, out = dispatch(self.service, "GET", "/v1/status", sign_request(KEY, "GET", "/v1/status", b""), b"")
        drive = json.loads(out)["lastDrive"]
        self.assertEqual((drive["summaries"], drive["ended"], drive["lastEvent"]),
                         (1, False, "listening_disabled:self_limit"))
        self.assertEqual(drive["last"], {"w": 0, "n": 590, "windowS": 60.0, "steeringS": 48.0, "movingS": 60.0,
                                         "warnEvents": 0})

    def test_health_and_unknown_token(self):
        self.assertEqual(dispatch(self.service, "GET", "/healthz", {}, b"")[0], 200)
        self.assertEqual(dispatch(self.service, "POST", "/a/nope", {}, b"")[0], 404)


if __name__ == "__main__":
    unittest.main()
