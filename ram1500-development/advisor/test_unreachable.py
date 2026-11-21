"""No suggestion is reachable on this build.

Two locks, both code constants: `service_app.SUGGESTIONS_BUILT_IN` and
`applier.APPLY_ENABLED`. Nothing received over the network or set in the
environment opens them; the only way in is a test that patches the constants
inside its own process (test_dormant_path.py). This file holds the service
side and the source scan; the agent, applier and uplink sides are in their own
test files, and the whole loop is in test_end_to_end.py.
"""
import ast
import json
import unittest
from pathlib import Path

import applier
import service_app
from approval import sign_approval
from service_app import AdvisorService, dispatch
from service_store import MemoryStore
from test_service import KEY, URL, Clock, FakeNotifier, minute
from wire import sign_request

LOCKS = {"SUGGESTIONS_BUILT_IN": "service_app.py", "APPLY_ENABLED": "applier.py"}
LEARNED = 0.32812345678901234      # an invented value with the precision a learned delay really has
SETTINGS = {"LagdToggle": True, "LagdToggleDelay": 0.2, "NeuralNetworkLateralControl": False}
CLOSE_STOP = {"leadPresent": True, "leadDistanceM": 9.0, "speedMs": 20.0}


class ServiceLockTests(unittest.TestCase):
    """Lock 1: the service as it is built creates, signs and hands out nothing."""

    def setUp(self):
        self.clock = Clock()
        self.notifier = FakeNotifier()
        self.service = AdvisorService(MemoryStore(), self.notifier, KEY, URL, clock=self.clock)
        self.seq = 0

    def event(self, kind, drive, data=None):
        self.seq += 1
        return self.service.handle_event({"type": kind, "driveId": drive, "id": f"e{self.seq}", "data": data or {}})

    def drive(self, drive, **metrics):
        self.event("drive_start", drive, {"learnedDelayS": LEARNED, "baseActuatorDelayS": 0.2,
                                          "settings": dict(SETTINGS)})
        for _ in range(7):
            self.event("summary", drive, minute(**metrics))
        self.event("drive_end", drive)

    def stored(self, prefix):
        return [key for key in self.service.store.objects if key.startswith(prefix)]

    def with_buttons(self):
        return [n for n in self.notifier.sent if n["actions"]]

    def leftover_suggestion(self, status="pending", mode="live"):
        """A suggestion as an earlier service version left it in the store."""
        change = {"LagdToggle": False, "LagdToggleDelay": 0.078}
        record = {"id": "feedfacefeedface", "mode": mode, "change": change, "reason": "weaving_on_straight_road",
                  "driveId": "d0", "created": self.clock.t, "status": status, "approval": None,
                  "approveToken": "approve-token-0123456789", "declineToken": "decline-token-0123456789"}
        if status == "approved":
            record["approval"] = sign_approval(KEY, suggestion_id=record["id"], mode=mode, change=change,
                                               issued_at=self.clock.t, nonce="0123456789abcdef0123456789abcdef")
        store = self.service.store
        store.put_json(f"suggestions/{record['id']}.json", record)
        store.put_json(f"tokens/{record['approveToken']}.json", {"sid": record["id"], "action": "approve"})
        store.put_json(f"tokens/{record['declineToken']}.json", {"sid": record["id"], "action": "decline"})
        store.put_json("state.json", {"seen": [], "approved": [record["id"]] if status == "approved" else [],
                                      "nightlyDue": None, "nightlyDrives": []})
        return record

    def test_drives_that_trigger_every_old_rule_create_no_suggestion(self):
        self.drive("d1", weave=10.0)                                         # the old weaving rule
        self.drive("d2", curveSamples=600, curveRelError=0.6)                # the old curve rule
        self.drive("d3", steerWarningEpisodes=3, hardBrakes=[CLOSE_STOP])    # the old nightly rules
        self.assertEqual(self.with_buttons(), [])
        self.assertEqual(self.stored("suggestions/") + self.stored("tokens/"), [])
        self.clock.t += 3700
        self.assertTrue(self.service.run_nightly())
        report = self.notifier.sent[-1]
        self.assertIn("Nightly", report["title"])
        self.assertIn("Hard stops: 7 (7 with a car close ahead).", report["message"])     # the fact stays
        self.assertNotIn("following-distance", report["message"])                         # the advice does not
        self.assertNotIn("Suggestion", report["message"])
        self.assertEqual(self.with_buttons(), [])
        self.assertEqual(self.stored("suggestions/") + self.stored("tokens/"), [])
        self.assertEqual(self.service.approvals_for_comma(), [])

    def test_creating_a_suggestion_raises(self):
        with self.assertRaises(RuntimeError):
            self.service._create_suggestion(mode="nightly", change={"NeuralNetworkLateralControl": True},
                                            reason="frequent_steering_warnings", drive_id=None)
        self.assertEqual(self.stored("suggestions/") + self.stored("tokens/"), [])

    def test_approve_and_decline_links_answer_not_found(self):
        record = self.leftover_suggestion()
        for path in ("/a/" + record["approveToken"], "/d/" + record["declineToken"]):
            with self.subTest(path=path[:3]):
                self.assertEqual(dispatch(self.service, "POST", path, {}, b"")[0], 404)
        self.assertEqual(self.service.approve(record["approveToken"])[0], 404)
        self.assertEqual(self.service.decline(record["declineToken"])[0], 404)
        kept = self.service.store.get_json(f"suggestions/{record['id']}.json")
        self.assertEqual((kept["status"], kept["approval"]), ("pending", None))     # nothing signed, nothing changed

    def test_an_approval_signed_with_the_real_key_is_not_handed_to_the_comma(self):
        self.leftover_suggestion(status="approved")
        self.assertEqual(self.service.approvals_for_comma(), [])
        headers = sign_request(KEY, "GET", "/v1/approvals", b"")
        status, _, body = dispatch(self.service, "GET", "/v1/approvals", headers, b"")
        self.assertEqual((status, json.loads(body)), (200, {"approvals": []}))

    def test_no_constructor_argument_opens_the_lock(self):
        service = AdvisorService(MemoryStore(), self.notifier, KEY, URL, clock=self.clock,
                                 live_enabled=True, nightly_suggestions=True)
        self.service = service
        self.drive("d1", weave=10.0, steerWarningEpisodes=3)
        self.clock.t += 3700
        service.run_nightly()
        self.assertEqual(self.with_buttons(), [])
        self.assertEqual(self.stored("suggestions/"), [])


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def _ways_to_set(tree, name):
    """Every place in a module that binds `name`, or that names it in a string (setattr, environment, globals)."""
    skip = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load):
            found.append("assigned")
        elif isinstance(node, ast.Attribute) and node.attr == name and not isinstance(node.ctx, ast.Load):
            found.append("assigned through a module")
        elif isinstance(node, ast.alias) and name in (node.name, node.asname):
            found.append("imported by name")
        elif isinstance(node, (ast.arg, ast.keyword)) and node.arg == name:
            found.append("argument")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            found.append("global")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            found.append("defined")
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name == name:
            found.append("bound by a statement")
        elif isinstance(node, ast.MatchMapping) and node.rest == name:
            found.append("bound by a statement")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and name in node.value \
                and id(node) not in skip:
            found.append("named in a string")
    return found


class SourceScanTests(unittest.TestCase):
    """Each lock is assigned exactly once, to False, and nothing else can set it."""

    @classmethod
    def setUpClass(cls):
        folder = Path(service_app.__file__).parent
        cls.trees = {path.name: ast.parse(path.read_text(encoding="utf-8"))
                     for path in sorted(folder.glob("*.py")) if not path.name.startswith("test_")}

    def test_both_locks_are_closed(self):
        self.assertIs(service_app.SUGGESTIONS_BUILT_IN, False)
        self.assertIs(applier.APPLY_ENABLED, False)

    def test_each_lock_is_assigned_exactly_once_to_false(self):
        for name, home in LOCKS.items():
            with self.subTest(name=name):
                ways = {file: _ways_to_set(tree, name) for file, tree in self.trees.items()}
                self.assertEqual({file: found for file, found in ways.items() if found}, {home: ["assigned"]})
                statements = [node for node in self.trees[home].body if isinstance(node, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]
                self.assertEqual(len(statements), 1)             # at module level, not inside a branch or function
                (statement,) = statements
                self.assertEqual(len(statement.targets), 1)
                self.assertIsInstance(statement.value, ast.Constant)
                self.assertIs(statement.value.value, False)      # a literal: not read from the environment or a file

    def test_the_scan_sees_the_ways_around_a_lock(self):
        for source, expected in (
                ("LOCK = False\nLOCK = True\n", ["assigned", "assigned"]),
                ("import mod\nmod.LOCK = True\n", ["assigned through a module"]),
                ("from mod import LOCK\n", ["imported by name"]),
                ("import os\nvalue = os.environ.get('LOCK')\n", ["named in a string"]),
                ("import mod\nsetattr(mod, 'LOCK', True)\n", ["named in a string"]),
                ("def build(LOCK=True):\n    return LOCK\n", ["argument"]),
                ("def build():\n    global LOCK\n    LOCK = True\n", ["assigned", "global"]),
                ("for LOCK in (True,):\n    pass\n", ["assigned"]),
                ("LOCK = False\nLOCK |= True\n", ["assigned", "assigned"]),
                ("try:\n    pass\nexcept Exception as LOCK:\n    pass\n", ["bound by a statement"]),
                ("match True:\n    case LOCK:\n        pass\n", ["bound by a statement"]),
                ("class Settings:\n    LOCK = True\n", ["assigned"]),
                ('"""LOCK is described here."""\nimport mod\nopen_now = mod.LOCK\n', []),
        ):
            with self.subTest(source=source):
                self.assertEqual(sorted(_ways_to_set(ast.parse(source), "LOCK")), sorted(expected))


if __name__ == "__main__":
    unittest.main()
