# Drive Advisor Core (offline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and test, entirely offline, the Drive Advisor's decision core: the settings guard, signed one-time approvals, the trial journal, the single settings writer, per-minute driving metrics, and the live and nightly suggestion rules.

**Architecture:** Small, pure-Python modules in `ram1500-development/advisor/`, one responsibility each, with flat imports (the pattern used by the other Ram tools). Nothing here touches a device, a network or a real settings store: settings go through any object with `get(key)` and `put(key, value)`, so the Comma adapter (plan 2) and the tests share the same code. This is rollout step 1 of the design in `ram1500-development/specs/2026-11-20-drive-advisor-design.md`.

**Tech Stack:** Python 3.12 standard library only (`unittest`, `hmac`, `hashlib`, `json`, `dataclasses`). The replay tool in Task 8 also uses `pycapnp` and `zstandard` and the Cap'n Proto schema of the installed build `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`.

**Conventions for every task**
- Work in the worktree on branch `ram1500-drive-advisor`. Run all commands from the repository root.
- Test command (Linux or the Comma): `python3 -m unittest discover -s ram1500-development/advisor -v`. On the owner's Windows PC use the project virtualenv's `python.exe` with the same arguments.
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never commit addresses, host names, user names, topics, keys or Railway IDs.
- Do not add files under `.github/workflows/`: the push token has no workflow scope.

**Follow-on plans (not part of this plan; each starts only on the owner's go-ahead)**
- Plan 2, Comma agent runtime: params adapter, listener loop reusing `device_telemetry.py`, journal location in `/data/ram-diag`, one-line `/data/continue.sh` hook with backup and uninstall, summary uplink queue. Rollout steps 2 and 3.
- Plan 3, Railway services: summary ingest, approval service, ntfy notifier, nightly scheduler using `nightly_recommendation`. Rollout steps 4 and 5.
- Plan 4, live suggestions end to end. Rollout step 6.

---

## File structure

| File | Responsibility |
|---|---|
| `ram1500-development/advisor/settings_guard.py` | Allowlist, fixed safe ranges, trial values. The safety boundary. |
| `ram1500-development/advisor/approval.py` | Sign and verify one-time approvals (HMAC-SHA256, 10-minute expiry). |
| `ram1500-development/advisor/journal.py` | Trial journal (values to restore) and used-approval ledger, both crash-safe. |
| `ram1500-development/advisor/applier.py` | The only settings writer; enforces every rule. |
| `ram1500-development/advisor/metrics.py` | `Sample` record and `MinuteSummary` per-minute metrics. |
| `ram1500-development/advisor/samples.py` | `SampleBuilder`: turns sunnypilot messages into `Sample`s. |
| `ram1500-development/advisor/rules.py` | Live suggestion rule and nightly recommendation. |
| `ram1500-development/advisor/replay_report.py` | Offline tool: Q-log files to per-minute summaries and suggestions. |
| `ram1500-development/advisor/test_*.py` | One test file per module. |

---

### Task 1: Settings guard

**Files:**
- Create: `ram1500-development/advisor/settings_guard.py`
- Test: `ram1500-development/advisor/test_settings_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Settings guard: only allowlisted settings, only inside the fixed safe ranges."""
import unittest

from settings_guard import GuardError, trial_values, validate_change

LEARNED, BASE = 0.25, 0.1


class TrialValuesTests(unittest.TestCase):
    def test_one_step_shorter(self):
        self.assertEqual(trial_values(LEARNED, BASE, -1), {"LagdToggle": False, "LagdToggleDelay": 0.1})

    def test_two_steps_longer(self):
        self.assertEqual(trial_values(LEARNED, BASE, 2)["LagdToggleDelay"], 0.25)

    def test_only_steps_of_one_or_two_allowed(self):
        for step in (0, 3, -3, 1.5):
            with self.subTest(step=step), self.assertRaises(GuardError):
                trial_values(LEARNED, BASE, step)

    def test_total_never_below_minimum(self):
        with self.assertRaises(GuardError):
            trial_values(0.12, 0.1, -1)

    def test_total_never_above_maximum(self):
        with self.assertRaises(GuardError):
            trial_values(0.48, 0.1, 1)

    def test_software_delay_never_negative(self):
        with self.assertRaises(GuardError):
            trial_values(0.15, 0.2, -1)


class ValidateChangeTests(unittest.TestCase):
    def check(self, change, mode="live"):
        validate_change(change, mode=mode, learned_delay_s=LEARNED, base_actuator_delay_s=BASE)

    def test_unknown_key_refused(self):
        with self.assertRaises(GuardError):
            self.check({"SteerMax": 400}, mode="nightly")

    def test_live_cannot_change_neural_steering(self):
        with self.assertRaises(GuardError):
            self.check({"NeuralNetworkLateralControl": True})

    def test_nightly_can_change_neural_steering(self):
        self.check({"NeuralNetworkLateralControl": True}, mode="nightly")

    def test_nightly_can_turn_learning_back_on(self):
        self.check({"LagdToggle": True}, mode="nightly")

    def test_live_must_set_both_delay_keys(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggleDelay": 0.1})

    def test_fixed_delay_requires_learning_off(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": True, "LagdToggleDelay": 0.1})

    def test_off_grid_value_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": False, "LagdToggleDelay": 0.12})

    def test_more_than_point_one_from_learned_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": False, "LagdToggleDelay": 0.30})

    def test_non_bool_toggle_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": 0, "LagdToggleDelay": 0.1})

    def test_nan_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": False, "LagdToggleDelay": float("nan")})

    def test_unknown_mode_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": True}, mode="remote")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'settings_guard'`

- [ ] **Step 3: Write the implementation**

```python
"""Allowlist and fixed safe ranges for Drive Advisor setting changes.

These constants are the safety boundary. Nothing received over the network
can widen them; the Comma agent imports them directly.
"""
from __future__ import annotations

import math

STEP_S = 0.05
MAX_OFFSET_S = 0.10
MIN_TOTAL_S = 0.10
MAX_TOTAL_S = 0.50
LIVE_KEYS = frozenset({"LagdToggle", "LagdToggleDelay"})
NIGHTLY_KEYS = LIVE_KEYS | {"NeuralNetworkLateralControl"}
BOOL_KEYS = frozenset({"LagdToggle", "NeuralNetworkLateralControl"})
MODES = ("live", "nightly")
_EPS = 1e-9


class GuardError(ValueError):
    """A requested change is outside the allowlist or the safe range."""


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trial_values(learned_delay_s: float, base_actuator_delay_s: float, step: int) -> dict:
    """Settings for a delay trial `step` x 0.05 s away from the learned delay."""
    if step not in (-2, -1, 1, 2):
        raise GuardError("step_out_of_range")
    if not (_finite(learned_delay_s) and _finite(base_actuator_delay_s)) or base_actuator_delay_s < 0:
        raise GuardError("invalid_delay_inputs")
    total = round(learned_delay_s + step * STEP_S, 3)
    change = {"LagdToggle": False, "LagdToggleDelay": round(total - base_actuator_delay_s, 3)}
    validate_change(change, mode="live", learned_delay_s=learned_delay_s,
                    base_actuator_delay_s=base_actuator_delay_s)
    return change


def validate_change(change: dict, *, mode: str, learned_delay_s: float,
                    base_actuator_delay_s: float) -> None:
    """Raise GuardError unless `change` is allowed in `mode`."""
    if mode not in MODES:
        raise GuardError("unknown_mode")
    if not isinstance(change, dict) or not change:
        raise GuardError("empty_change")
    allowed = LIVE_KEYS if mode == "live" else NIGHTLY_KEYS
    for key, value in change.items():
        if key not in allowed:
            raise GuardError(f"key_not_allowed:{key}")
        if key in BOOL_KEYS and not isinstance(value, bool):
            raise GuardError(f"bool_required:{key}")
    if mode == "live" and set(change) != LIVE_KEYS:
        raise GuardError("live_change_must_set_both_delay_keys")
    if "LagdToggleDelay" not in change:
        return
    software = change["LagdToggleDelay"]
    if not _finite(software) or software < 0:
        raise GuardError("invalid_software_delay")
    if change.get("LagdToggle", False) is not False:
        raise GuardError("fixed_delay_requires_learning_off")
    if not (_finite(learned_delay_s) and _finite(base_actuator_delay_s)):
        raise GuardError("invalid_delay_inputs")
    total = base_actuator_delay_s + software
    if not (MIN_TOTAL_S - _EPS <= total <= MAX_TOTAL_S + _EPS):
        raise GuardError("total_delay_out_of_range")
    if abs(total - learned_delay_s) > MAX_OFFSET_S + _EPS:
        raise GuardError("delay_too_far_from_learned")
    steps = (total - learned_delay_s) / STEP_S
    if abs(steps - round(steps)) > 1e-6:
        raise GuardError("delay_not_on_step")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 17 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/settings_guard.py ram1500-development/advisor/test_settings_guard.py
git commit -m "advisor: settings allowlist and fixed safe delay ranges"
```

---

### Task 2: Signed one-time approvals

**Files:**
- Create: `ram1500-development/advisor/approval.py`
- Test: `ram1500-development/advisor/test_approval.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Approvals: signed, single-use, valid for 10 minutes."""
import unittest

from approval import ApprovalError, sign_approval, verify_approval

KEY = b"k" * 32
CHANGE = {"LagdToggle": False, "LagdToggleDelay": 0.1}


def make(**overrides):
    args = dict(suggestion_id="s1", mode="live", change=CHANGE, issued_at=1000.0, nonce="n" * 16)
    args.update(overrides)
    return sign_approval(KEY, **args)


class ApprovalTests(unittest.TestCase):
    def test_valid_approval_returns_fields(self):
        fields = verify_approval(KEY, make(), now=1100.0, used_nonces=set())
        self.assertEqual(fields["change"], CHANGE)
        self.assertEqual(fields["mode"], "live")

    def test_wrong_key_refused(self):
        with self.assertRaisesRegex(ApprovalError, "bad_signature"):
            verify_approval(b"x" * 32, make(), now=1100.0, used_nonces=set())

    def test_tampered_change_refused(self):
        approval = make()
        approval["change"] = {"LagdToggle": False, "LagdToggleDelay": 0.2}
        with self.assertRaisesRegex(ApprovalError, "bad_signature"):
            verify_approval(KEY, approval, now=1100.0, used_nonces=set())

    def test_expired_after_ten_minutes(self):
        with self.assertRaisesRegex(ApprovalError, "expired"):
            verify_approval(KEY, make(), now=1601.0, used_nonces=set())

    def test_future_issue_time_refused(self):
        with self.assertRaisesRegex(ApprovalError, "issued_in_future"):
            verify_approval(KEY, make(), now=900.0, used_nonces=set())

    def test_replay_refused(self):
        with self.assertRaisesRegex(ApprovalError, "already_used"):
            verify_approval(KEY, make(), now=1100.0, used_nonces={"n" * 16})

    def test_short_nonce_refused(self):
        with self.assertRaisesRegex(ApprovalError, "malformed"):
            verify_approval(KEY, make(nonce="abc"), now=1100.0, used_nonces=set())

    def test_not_a_dict_refused(self):
        with self.assertRaisesRegex(ApprovalError, "malformed"):
            verify_approval(KEY, "approve", now=1100.0, used_nonces=set())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_approval`, `ModuleNotFoundError: No module named 'approval'`

- [ ] **Step 3: Write the implementation**

```python
"""One-time approvals for Drive Advisor changes.

Railway signs an approval when the owner taps Approve; the Comma verifies it
with the shared key before the applier writes anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math

APPROVAL_TTL_S = 600
MAX_CLOCK_SKEW_S = 60
_FIELDS = ("suggestion_id", "mode", "change", "issued_at", "nonce")


class ApprovalError(ValueError):
    """An approval is malformed, forged, expired or already used."""


def _payload(fields: dict) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sign_approval(key: bytes, *, suggestion_id: str, mode: str, change: dict,
                  issued_at: float, nonce: str) -> dict:
    fields = {"suggestion_id": suggestion_id, "mode": mode, "change": change,
              "issued_at": issued_at, "nonce": nonce}
    return {**fields, "sig": hmac.new(key, _payload(fields), hashlib.sha256).hexdigest()}


def verify_approval(key: bytes, approval: dict, *, now: float, used_nonces: set) -> dict:
    """Return the approved fields, or raise ApprovalError."""
    if not isinstance(approval, dict):
        raise ApprovalError("malformed")
    fields = {name: approval.get(name) for name in _FIELDS}
    issued = fields["issued_at"]
    if not (isinstance(fields["suggestion_id"], str) and isinstance(fields["nonce"], str)
            and len(fields["nonce"]) >= 16 and isinstance(fields["change"], dict)
            and fields["mode"] in ("live", "nightly")
            and isinstance(issued, (int, float)) and not isinstance(issued, bool)
            and math.isfinite(issued)):
        raise ApprovalError("malformed")
    try:
        expected = hmac.new(key, _payload(fields), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        raise ApprovalError("malformed")
    signature = approval.get("sig")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ApprovalError("bad_signature")
    age = now - issued
    if age < -MAX_CLOCK_SKEW_S:
        raise ApprovalError("issued_in_future")
    if age > APPROVAL_TTL_S:
        raise ApprovalError("expired")
    if fields["nonce"] in used_nonces:
        raise ApprovalError("already_used")
    return fields
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 25 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/approval.py ram1500-development/advisor/test_approval.py
git commit -m "advisor: signed one-time approvals with 10-minute expiry"
```

---

### Task 3: Trial journal and used-approval ledger

**Files:**
- Create: `ram1500-development/advisor/journal.py`
- Test: `ram1500-development/advisor/test_journal.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Journal and nonce ledger survive restarts and never allow two open trials."""
import tempfile
import unittest
from pathlib import Path

from journal import Journal, JournalError, NonceLedger


class JournalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.journal = Journal(Path(folder.name) / "trial.json")

    def test_open_load_close(self):
        self.assertFalse(self.journal.is_open())
        self.journal.open_trial(suggestion_id="s1", previous={"LagdToggle": True},
                                written={"LagdToggle": False})
        self.assertTrue(self.journal.is_open())
        self.assertEqual(self.journal.load()["previous"], {"LagdToggle": True})
        self.journal.close()
        self.assertIsNone(self.journal.load())

    def test_second_trial_refused_while_open(self):
        self.journal.open_trial(suggestion_id="s1", previous={}, written={})
        with self.assertRaises(JournalError):
            self.journal.open_trial(suggestion_id="s2", previous={}, written={})

    def test_survives_a_new_instance(self):
        self.journal.open_trial(suggestion_id="s1", previous={"LagdToggleDelay": 0.2},
                                written={"LagdToggleDelay": 0.1})
        self.assertEqual(Journal(self.journal.path).load()["written"], {"LagdToggleDelay": 0.1})


class NonceLedgerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ledger = NonceLedger(Path(folder.name) / "nonces.json")

    def test_consumed_nonce_is_remembered_across_instances(self):
        self.ledger.consume("a" * 16)
        self.assertIn("a" * 16, NonceLedger(self.ledger.path).used())

    def test_keeps_only_the_most_recent(self):
        for i in range(NonceLedger.MAX + 5):
            self.ledger.consume(f"{i:016d}")
        used = self.ledger.used()
        self.assertEqual(len(used), NonceLedger.MAX)
        self.assertNotIn(f"{0:016d}", used)
        self.assertIn(f"{NonceLedger.MAX + 4:016d}", used)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_journal`, `ModuleNotFoundError: No module named 'journal'`

- [ ] **Step 3: Write the implementation**

```python
"""Crash-safe records for the applier.

Journal: what a live trial replaced, so it can always be put back.
NonceLedger: approvals already used, so none can be replayed after a restart.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class JournalError(RuntimeError):
    """A second trial was started while one is open."""


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)

    def is_open(self) -> bool:
        return self.path.exists()

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def open_trial(self, *, suggestion_id: str, previous: dict, written: dict) -> None:
        if self.is_open():
            raise JournalError("trial_already_open")
        _atomic_write(self.path, {"suggestion_id": suggestion_id, "previous": previous, "written": written})

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


class NonceLedger:
    MAX = 500

    def __init__(self, path: Path):
        self.path = Path(path)

    def _items(self) -> list:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return list(json.load(handle))

    def used(self) -> set:
        return set(self._items())

    def consume(self, nonce: str) -> None:
        items = [n for n in self._items() if n != nonce] + [nonce]
        _atomic_write(self.path, items[-self.MAX:])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 30 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/journal.py ram1500-development/advisor/test_journal.py
git commit -m "advisor: crash-safe trial journal and used-approval ledger"
```

---

### Task 4: Applier (the only settings writer)

**Files:**
- Create: `ram1500-development/advisor/applier.py`
- Test: `ram1500-development/advisor/test_applier.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Applier enforces every design rule before writing a setting."""
import tempfile
import unittest
from pathlib import Path

from applier import Applier, VehicleState
from approval import sign_approval
from journal import Journal, NonceLedger

KEY = b"k" * 32
PARKED = VehicleState(in_park=True, speed_ms=0.0, engaged=False)
LIVE = {"LagdToggle": False, "LagdToggleDelay": 0.1}
START = {"LagdToggle": True, "LagdToggleDelay": 0.2, "NeuralNetworkLateralControl": False}


class FakeParams:
    def __init__(self, values):
        self.values = dict(values)
        self.writes = []

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))


class ApplierTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.params = FakeParams(START)
        self.applier = Applier(self.params, Journal(Path(folder.name) / "trial.json"),
                               NonceLedger(Path(folder.name) / "nonces.json"), KEY)
        self.count = 0

    def approval(self, change=LIVE, mode="live"):
        self.count += 1
        return sign_approval(KEY, suggestion_id=f"s{self.count}", mode=mode, change=change,
                             issued_at=1000.0, nonce=f"{self.count:016d}")

    def apply(self, approval, state=PARKED):
        return self.applier.apply(approval, state, now=1010.0, learned_delay_s=0.25,
                                  base_actuator_delay_s=0.1)

    def test_live_trial_applies_in_park_and_journals_previous(self):
        self.assertEqual(self.apply(self.approval()), "applied")
        self.assertIs(self.params.values["LagdToggle"], False)
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.1)
        self.assertEqual(self.applier.journal.load()["previous"], {"LagdToggle": True, "LagdToggleDelay": 0.2})

    def test_refused_unless_parked(self):
        for state in (VehicleState(True, 0.5, False), VehicleState(False, 0.0, False),
                      VehicleState(True, 0.0, True)):
            with self.subTest(state=state):
                self.assertEqual(self.apply(self.approval(), state=state), "refused:not_parked")
        self.assertEqual(self.params.writes, [])

    def test_bad_signature_changes_nothing(self):
        approval = self.approval()
        approval["sig"] = "0" * 64
        self.assertEqual(self.apply(approval), "refused:bad_signature")
        self.assertEqual(self.params.writes, [])

    def test_out_of_range_refused_even_when_signed(self):
        bad = {"LagdToggle": False, "LagdToggleDelay": 0.4}
        self.assertEqual(self.apply(self.approval(change=bad)), "refused:delay_too_far_from_learned")

    def test_same_approval_cannot_be_used_twice(self):
        approval = self.approval()
        self.assertEqual(self.apply(approval), "applied")
        self.applier.restore(onroad=False)
        self.assertEqual(self.apply(approval), "refused:already_used")

    def test_one_trial_at_a_time(self):
        self.apply(self.approval())
        self.assertEqual(self.apply(self.approval()), "refused:trial_already_active")

    def test_at_most_two_live_trials_per_drive(self):
        for _ in range(2):
            self.assertEqual(self.apply(self.approval()), "applied")
            self.applier.restore(onroad=False)
        self.assertEqual(self.apply(self.approval()), "refused:live_trial_limit")
        self.applier.new_drive()
        self.assertEqual(self.apply(self.approval()), "applied")

    def test_restore_puts_previous_values_back(self):
        self.apply(self.approval())
        self.assertEqual(self.applier.restore(onroad=False), "restored")
        self.assertIs(self.params.values["LagdToggle"], True)
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.assertFalse(self.applier.journal.is_open())

    def test_restore_waits_while_driving(self):
        self.apply(self.approval())
        self.assertEqual(self.applier.restore(onroad=True), "deferred_until_drive_end")
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.1)

    def test_owner_change_wins(self):
        self.apply(self.approval())
        self.params.values["LagdToggle"] = True
        self.assertEqual(self.applier.check_owner_override(), "trial_ended_owner_changed")
        self.assertEqual(self.applier.restore(onroad=False), "nothing_to_restore")
        self.assertIs(self.params.values["LagdToggle"], True)

    def test_restore_after_agent_restart(self):
        self.apply(self.approval())
        fresh = Applier(self.params, Journal(self.applier.journal.path),
                        NonceLedger(self.applier.nonces.path), KEY)
        self.assertEqual(fresh.restore(onroad=False), "restored")
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)

    def test_nightly_change_is_kept_not_journaled(self):
        approval = self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly")
        self.assertEqual(self.apply(approval), "applied")
        self.assertIs(self.params.values["NeuralNetworkLateralControl"], True)
        self.assertFalse(self.applier.journal.is_open())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_applier`, `ModuleNotFoundError: No module named 'applier'`

- [ ] **Step 3: Write the implementation**

```python
"""The only Drive Advisor component that writes settings.

Order of checks: approval signature/expiry/replay, allowlist and range,
parked state, one change at a time, live-trial limit. The approval is marked
used and the journal is written BEFORE any setting changes, so a crash at any
point can be undone and no approval can be replayed.
"""
from __future__ import annotations

from dataclasses import dataclass

from approval import ApprovalError, verify_approval
from journal import Journal, NonceLedger
from settings_guard import GuardError, validate_change

MAX_LIVE_TRIALS_PER_DRIVE = 2
PARKED_SPEED_MS = 0.1


@dataclass(frozen=True)
class VehicleState:
    in_park: bool
    speed_ms: float
    engaged: bool

    def parked(self) -> bool:
        return self.in_park and abs(self.speed_ms) < PARKED_SPEED_MS and not self.engaged


class Applier:
    def __init__(self, params, journal: Journal, nonces: NonceLedger, key: bytes):
        self.params = params
        self.journal = journal
        self.nonces = nonces
        self.key = key
        self.live_trials_this_drive = 0

    def apply(self, approval: dict, state: VehicleState, *, now: float,
              learned_delay_s: float, base_actuator_delay_s: float) -> str:
        try:
            fields = verify_approval(self.key, approval, now=now, used_nonces=self.nonces.used())
        except ApprovalError as error:
            return f"refused:{error}"
        change, mode = fields["change"], fields["mode"]
        try:
            validate_change(change, mode=mode, learned_delay_s=learned_delay_s,
                            base_actuator_delay_s=base_actuator_delay_s)
        except GuardError as error:
            return f"refused:{error}"
        if not state.parked():
            return "refused:not_parked"
        if self.journal.is_open():
            return "refused:trial_already_active"
        if mode == "live" and self.live_trials_this_drive >= MAX_LIVE_TRIALS_PER_DRIVE:
            return "refused:live_trial_limit"
        self.nonces.consume(fields["nonce"])
        if mode == "live":
            previous = {key: self.params.get(key) for key in change}
            self.journal.open_trial(suggestion_id=fields["suggestion_id"], previous=previous, written=change)
            self.live_trials_this_drive += 1
        for key, value in change.items():
            self.params.put(key, value)
        return "applied"

    def _owner_changed(self, entry: dict) -> list:
        return sorted(key for key, value in entry["written"].items() if self.params.get(key) != value)

    def check_owner_override(self) -> str | None:
        """Call regularly during a trial; ends the trial if the owner changed a setting."""
        entry = self.journal.load()
        if entry is None or not self._owner_changed(entry):
            return None
        self.journal.close()
        return "trial_ended_owner_changed"

    def restore(self, *, onroad: bool) -> str:
        """At drive end, or at agent start while not driving: put back what a trial replaced."""
        entry = self.journal.load()
        if entry is None:
            return "nothing_to_restore"
        if onroad:
            return "deferred_until_drive_end"
        changed = self._owner_changed(entry)
        if changed:
            self.journal.close()
            return "owner_changed:" + ",".join(changed)
        for key, value in entry["previous"].items():
            self.params.put(key, value)
        self.journal.close()
        return "restored"

    def new_drive(self) -> None:
        self.live_trials_this_drive = 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 42 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/applier.py ram1500-development/advisor/test_applier.py
git commit -m "advisor: single settings writer enforcing park, approval, journal and limits"
```

---

### Task 5: Per-minute metrics

**Files:**
- Create: `ram1500-development/advisor/metrics.py`
- Test: `ram1500-development/advisor/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Per-minute metrics from normalized samples."""
import unittest

from metrics import MinuteSummary, Sample

HZ = 10.0


def sample(t, **overrides):
    values = dict(t=t, v_ego=25.0, a_ego=0.0, steering_rate_deg_s=0.0, steering_pressed=False,
                  lat_active=True, desired_curvature=0.0, actual_curvature=0.0, torque_at_limit=False,
                  lead_present=False, lead_distance_m=None, lane_change=False, alerts=frozenset())
    values.update(overrides)
    return Sample(**values)


class MinuteSummaryTests(unittest.TestCase):
    def summarize(self, samples):
        summary = MinuteSummary(start_t=samples[0].t)
        for s in samples:
            summary.add(s)
        return summary.finish(HZ)

    def test_steady_straight_driving_has_no_weaving(self):
        out = self.summarize([sample(i / HZ) for i in range(600)])
        self.assertEqual(out["engagedMinutes"], 1.0)
        self.assertEqual(out["weaveReversals"], 0)
        self.assertEqual(out["weavePerStraightMinute"], 0.0)

    def test_side_to_side_steering_counts_reversals(self):
        rates = [5.0 if (i // 10) % 2 == 0 else -5.0 for i in range(600)]
        out = self.summarize([sample(i / HZ, steering_rate_deg_s=r) for i, r in enumerate(rates)])
        self.assertEqual(out["weaveReversals"], 59)
        self.assertAlmostEqual(out["weavePerStraightMinute"], 59.0)

    def test_small_steering_rates_are_ignored(self):
        rates = [1.0 if i % 2 else -1.0 for i in range(600)]
        out = self.summarize([sample(i / HZ, steering_rate_deg_s=r) for i, r in enumerate(rates)])
        self.assertEqual(out["weaveReversals"], 0)

    def test_curve_relative_error(self):
        out = self.summarize([sample(i / HZ, desired_curvature=0.01, actual_curvature=0.008)
                              for i in range(600)])
        self.assertAlmostEqual(out["curveRelError"], 0.2, places=3)
        self.assertEqual(out["curveSamples"], 600)
        self.assertIsNone(out["weavePerStraightMinute"])

    def test_warning_lane_change_and_hard_brake_count_once(self):
        out = self.summarize([
            sample(0.0),
            sample(0.1, alerts=frozenset({"steerSaturated"}), lane_change=True, a_ego=-4.0,
                   lead_present=True, lead_distance_m=12.34),
            sample(0.2, alerts=frozenset({"steerSaturated"}), lane_change=True, a_ego=-4.5,
                   lead_present=True, lead_distance_m=11.0),
            sample(0.3),
        ])
        self.assertEqual(out["steerWarningEpisodes"], 1)
        self.assertEqual(out["laneChanges"], 1)
        self.assertEqual(out["hardBrakes"], [{"leadPresent": True, "leadDistanceM": 12.3, "speedMs": 25.0}])

    def test_disengaged_driving_does_not_count_for_steering(self):
        out = self.summarize([sample(i / HZ, lat_active=False,
                                     steering_rate_deg_s=5.0 if i % 20 < 10 else -5.0) for i in range(600)])
        self.assertEqual(out["engagedMinutes"], 0.0)
        self.assertEqual(out["weaveReversals"], 0)
        self.assertIsNone(out["torqueLimitFraction"])

    def test_overrides_and_torque_limit(self):
        out = self.summarize([sample(i / HZ, steering_pressed=(10 <= i < 20), torque_at_limit=(i < 60))
                              for i in range(600)])
        self.assertEqual(out["overrideEpisodes"], 1)
        self.assertAlmostEqual(out["torqueLimitFraction"], 0.1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_metrics`, `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: Write the implementation**

```python
"""Per-minute Drive Advisor metrics. Counts and numbers only: nothing that
identifies the driver, the place or the vehicle."""
from __future__ import annotations

from dataclasses import dataclass

STRAIGHT_CURVATURE = 0.002   # 1/m: radius 500 m or more counts as straight
CURVE_CURVATURE = 0.002
WEAVE_RATE_DEG_S = 2.0
WEAVE_MIN_SPEED_MS = 15.0
HARD_BRAKE_MPS2 = -3.0
STEER_WARNING = "steerSaturated"


@dataclass(frozen=True)
class Sample:
    t: float
    v_ego: float
    a_ego: float
    steering_rate_deg_s: float
    steering_pressed: bool
    lat_active: bool
    desired_curvature: float | None
    actual_curvature: float | None
    torque_at_limit: bool
    lead_present: bool
    lead_distance_m: float | None
    lane_change: bool
    alerts: frozenset = frozenset()


class MinuteSummary:
    def __init__(self, start_t: float):
        self.start_t = start_t
        self.samples = 0
        self.engaged = 0
        self.straight_engaged = 0
        self.reversals = 0
        self.curve_err_sum = 0.0
        self.curve_n = 0
        self.torque_limit = 0
        self.steer_warnings = 0
        self.lane_changes = 0
        self.overrides = 0
        self.hard_brakes = []
        self.speed_sum = 0.0
        self._rate_sign = 0
        self._prev = None

    def add(self, s: Sample) -> None:
        prev = self._prev
        self.samples += 1
        self.speed_sum += s.v_ego
        if STEER_WARNING in s.alerts and (prev is None or STEER_WARNING not in prev.alerts):
            self.steer_warnings += 1
        if s.lane_change and (prev is None or not prev.lane_change):
            self.lane_changes += 1
        if s.a_ego <= HARD_BRAKE_MPS2 and (prev is None or prev.a_ego > HARD_BRAKE_MPS2):
            self.hard_brakes.append({
                "leadPresent": s.lead_present,
                "leadDistanceM": None if s.lead_distance_m is None else round(s.lead_distance_m, 1),
                "speedMs": round(s.v_ego, 1),
            })
        if s.lat_active:
            self._add_engaged(s, prev)
        self._prev = s

    def _add_engaged(self, s: Sample, prev: Sample | None) -> None:
        self.engaged += 1
        if s.steering_pressed and (prev is None or not prev.steering_pressed):
            self.overrides += 1
        if s.torque_at_limit:
            self.torque_limit += 1
        dc = s.desired_curvature
        if dc is None or s.lane_change:
            return
        if abs(dc) < STRAIGHT_CURVATURE and s.v_ego >= WEAVE_MIN_SPEED_MS:
            self.straight_engaged += 1
            if abs(s.steering_rate_deg_s) >= WEAVE_RATE_DEG_S:
                sign = 1 if s.steering_rate_deg_s > 0 else -1
                if self._rate_sign and sign != self._rate_sign:
                    self.reversals += 1
                self._rate_sign = sign
        if abs(dc) >= CURVE_CURVATURE and s.actual_curvature is not None:
            self.curve_err_sum += abs(dc - s.actual_curvature) / abs(dc)
            self.curve_n += 1

    def finish(self, sample_hz: float) -> dict:
        per_minute = sample_hz * 60.0
        straight_minutes = self.straight_engaged / per_minute
        return {
            "startT": round(self.start_t, 3),
            "samples": self.samples,
            "engagedMinutes": round(self.engaged / per_minute, 2),
            "straightMinutes": round(straight_minutes, 2),
            "weaveReversals": self.reversals,
            "weavePerStraightMinute": (round(self.reversals / straight_minutes, 2)
                                       if straight_minutes >= 0.25 else None),
            "curveSamples": self.curve_n,
            "curveRelError": round(self.curve_err_sum / self.curve_n, 4) if self.curve_n else None,
            "torqueLimitFraction": round(self.torque_limit / self.engaged, 4) if self.engaged else None,
            "steerWarningEpisodes": self.steer_warnings,
            "laneChanges": self.lane_changes,
            "overrideEpisodes": self.overrides,
            "hardBrakes": self.hard_brakes,
            "speedMeanMs": round(self.speed_sum / self.samples, 1) if self.samples else None,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 49 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/metrics.py ram1500-development/advisor/test_metrics.py
git commit -m "advisor: per-minute steering, curve, lane-change and braking metrics"
```

---

### Task 6: Sample builder (sunnypilot messages to samples)

**Files:**
- Create: `ram1500-development/advisor/samples.py`
- Test: `ram1500-development/advisor/test_samples.py`

Field paths at the installed build: `carState.vEgo/aEgo/steeringRateDeg/steeringPressed`, `carControl.latActive`, `controlsState.desiredCurvature/curvature`, `carOutput.actuatorsOutput.torqueOutputCan` (Ram steering limit 350), `radarState.leadOne.status/dRel`, `drivingModelData.meta.laneChangeState`, `onroadEvents[].name`.

- [ ] **Step 1: Write the failing tests**

```python
"""SampleBuilder joins the latest fresh messages onto each carState sample."""
import unittest
from types import SimpleNamespace as ns

from samples import SampleBuilder

CS = ns(vEgo=20.0, aEgo=-0.5, steeringRateDeg=3.0, steeringPressed=False)
T0 = 1_000_000_000


class SampleBuilderTests(unittest.TestCase):
    def test_builds_sample_from_latest_fresh_messages(self):
        b = SampleBuilder(sample_hz=10.0)
        b.ingest("carControl", T0, ns(latActive=True))
        b.ingest("controlsState", T0, ns(desiredCurvature=0.01, curvature=0.009))
        b.ingest("carOutput", T0, ns(actuatorsOutput=ns(torqueOutputCan=-345)))
        b.ingest("radarState", T0, ns(leadOne=ns(status=True, dRel=30.0)))
        b.ingest("drivingModelData", T0, ns(meta=ns(laneChangeState="laneChangeStarting")))
        b.ingest("onroadEvents", T0, [ns(name="steerSaturated")])
        s = b.ingest("carState", T0 + 50_000_000, CS)
        self.assertTrue(s.lat_active)
        self.assertTrue(s.torque_at_limit)
        self.assertTrue(s.lane_change)
        self.assertEqual(s.lead_distance_m, 30.0)
        self.assertEqual(s.desired_curvature, 0.01)
        self.assertEqual(s.alerts, frozenset({"steerSaturated"}))

    def test_rate_limited_to_sample_hz(self):
        b = SampleBuilder(sample_hz=10.0)
        self.assertIsNotNone(b.ingest("carState", 0, CS))
        self.assertIsNone(b.ingest("carState", 50_000_000, CS))
        self.assertIsNotNone(b.ingest("carState", 100_000_000, CS))

    def test_stale_messages_are_ignored(self):
        b = SampleBuilder(sample_hz=10.0)
        b.ingest("carControl", 0, ns(latActive=True))
        self.assertFalse(b.ingest("carState", 2_000_000_000, CS).lat_active)

    def test_lane_change_off_and_no_lead(self):
        b = SampleBuilder(sample_hz=10.0)
        b.ingest("drivingModelData", 0, ns(meta=ns(laneChangeState="off")))
        b.ingest("radarState", 0, ns(leadOne=ns(status=False, dRel=0.0)))
        s = b.ingest("carState", 10, CS)
        self.assertFalse(s.lane_change)
        self.assertFalse(s.lead_present)
        self.assertIsNone(s.lead_distance_m)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_samples`, `ModuleNotFoundError: No module named 'samples'`

- [ ] **Step 3: Write the implementation**

```python
"""Turn sunnypilot messages (live msgq or decoded logs) into metrics Samples.

Works on any objects with the cereal attribute names, so tests use
SimpleNamespace and the Comma agent uses the real decoded messages.
"""
from __future__ import annotations

import math

from metrics import Sample

FRESH_NS = 500_000_000
LANE_CHANGE_OFF = {"off", "None", ""}
RAM_STEER_LIMIT = 350


def get(obj, *names):
    for name in names:
        if obj is None:
            return None
        try:
            obj = obj[name] if isinstance(obj, dict) else getattr(obj, name)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
    return obj


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class SampleBuilder:
    def __init__(self, sample_hz: float = 10.0, steer_limit: int = RAM_STEER_LIMIT):
        self.interval_ns = int(1e9 / sample_hz)
        self.steer_limit = steer_limit
        self.latest = {}
        self.alerts = frozenset()
        self.last_sample_ns = None

    def _fresh(self, service: str, t_ns: int):
        item = self.latest.get(service)
        if item is None or not 0 <= t_ns - item[0] <= FRESH_NS:
            return None
        return item[1]

    def ingest(self, service: str, t_ns: int, msg) -> Sample | None:
        if service == "onroadEvents":
            events = msg if isinstance(msg, (list, tuple)) else (get(msg, "onroadEvents") or [])
            self.alerts = frozenset(str(get(e, "name")) for e in events if get(e, "name") is not None)
            return None
        if service != "carState":
            self.latest[service] = (t_ns, msg)
            return None
        if self.last_sample_ns is not None and t_ns - self.last_sample_ns < self.interval_ns:
            return None
        self.last_sample_ns = t_ns
        control = self._fresh("carControl", t_ns)
        controls_state = self._fresh("controlsState", t_ns)
        torque = _number(get(self._fresh("carOutput", t_ns), "actuatorsOutput", "torqueOutputCan"))
        lead = get(self._fresh("radarState", t_ns), "leadOne")
        lead_present = get(lead, "status") is True
        lane_state = str(get(self._fresh("drivingModelData", t_ns), "meta", "laneChangeState"))
        return Sample(
            t=t_ns / 1e9,
            v_ego=_number(get(msg, "vEgo")) or 0.0,
            a_ego=_number(get(msg, "aEgo")) or 0.0,
            steering_rate_deg_s=_number(get(msg, "steeringRateDeg")) or 0.0,
            steering_pressed=get(msg, "steeringPressed") is True,
            lat_active=get(control, "latActive") is True,
            desired_curvature=_number(get(controls_state, "desiredCurvature")),
            actual_curvature=_number(get(controls_state, "curvature")),
            torque_at_limit=torque is not None and abs(torque) >= 0.98 * self.steer_limit,
            lead_present=lead_present,
            lead_distance_m=_number(get(lead, "dRel")) if lead_present else None,
            lane_change=lane_state not in LANE_CHANGE_OFF,
            alerts=self.alerts,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 53 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/samples.py ram1500-development/advisor/test_samples.py
git commit -m "advisor: build samples from sunnypilot messages with freshness checks"
```

---

### Task 7: Suggestion rules (live and nightly)

**Files:**
- Create: `ram1500-development/advisor/rules.py`
- Test: `ram1500-development/advisor/test_rules.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Live suggestion rule and nightly recommendation."""
import unittest

from rules import live_suggestion, nightly_recommendation

TRIAL_CHANGE = {"LagdToggle": False, "LagdToggleDelay": 0.1}


def minute(**overrides):
    values = {"engagedMinutes": 1.0, "straightMinutes": 1.0, "weavePerStraightMinute": 1.0,
              "curveSamples": 0, "curveRelError": None, "steerWarningEpisodes": 0,
              "laneChanges": 0, "overrideEpisodes": 0, "hardBrakes": [], "speedMeanMs": 25.0}
    values.update(overrides)
    return values


class LiveSuggestionTests(unittest.TestCase):
    def suggest(self, rows, trials=0, active=False):
        return live_suggestion(rows, trials_this_drive=trials, trial_active=active)

    def test_needs_five_engaged_minutes(self):
        self.assertIsNone(self.suggest([minute(weavePerStraightMinute=10.0)] * 4))

    def test_weaving_suggests_shorter_delay(self):
        s = self.suggest([minute(weavePerStraightMinute=10.0)] * 5)
        self.assertEqual((s["step"], s["reason"]), (-1, "weaving_on_straight_road"))

    def test_late_curves_suggest_longer_delay(self):
        rows = [minute(straightMinutes=0.0, weavePerStraightMinute=None, curveSamples=600,
                       curveRelError=0.3)] * 5
        s = self.suggest(rows)
        self.assertEqual((s["step"], s["reason"]), (1, "late_in_curves"))

    def test_conflicting_signals_suggest_nothing(self):
        rows = [minute(weavePerStraightMinute=10.0, curveSamples=600, curveRelError=0.3)] * 5
        self.assertIsNone(self.suggest(rows))

    def test_nothing_during_a_trial_or_after_two(self):
        rows = [minute(weavePerStraightMinute=10.0)] * 5
        self.assertIsNone(self.suggest(rows, active=True))
        self.assertIsNone(self.suggest(rows, trials=2))

    def test_only_the_last_ten_minutes_count(self):
        rows = [minute(weavePerStraightMinute=10.0)] * 5 + [minute()] * 10
        self.assertIsNone(self.suggest(rows))


class NightlyTests(unittest.TestCase):
    def baseline(self, warnings=0):
        return {"trial": None, "summaries": [minute(weavePerStraightMinute=10.0,
                                                    steerWarningEpisodes=warnings)] * 10}

    def trial(self, minutes=6, weave=7.0, warnings=0):
        return {"trial": {"reason": "weaving_on_straight_road", "change": TRIAL_CHANGE},
                "summaries": [minute(weavePerStraightMinute=weave, steerWarningEpisodes=warnings,
                                     speedMeanMs=26.0)] * minutes}

    def test_improved_trial_is_recommended_to_keep(self):
        report = nightly_recommendation([self.baseline(), self.trial()], nnlc_on=False)
        self.assertEqual(report["suggestion"], {"kind": "keep_trial", "change": TRIAL_CHANGE,
                                                "reason": "weaving_on_straight_road_improved"})

    def test_trial_with_more_warnings_is_not_kept(self):
        report = nightly_recommendation([self.baseline(), self.trial(warnings=1)], nnlc_on=True)
        self.assertIsNone(report["suggestion"])
        self.assertFalse(report["trials"][0]["keep"])

    def test_short_trial_is_not_judged(self):
        report = nightly_recommendation([self.baseline(), self.trial(minutes=4)], nnlc_on=True)
        self.assertEqual(report["trials"][0]["why"], "not_enough_trial_driving")
        self.assertIsNone(report["suggestion"])

    def test_frequent_warnings_suggest_neural_steering_when_off(self):
        drives = [{"trial": None, "summaries": [minute(steerWarningEpisodes=1)] + [minute()] * 9}]
        report = nightly_recommendation(drives, nnlc_on=False)
        self.assertEqual(report["suggestion"]["change"], {"NeuralNetworkLateralControl": True})
        self.assertIsNone(nightly_recommendation(drives, nnlc_on=True)["suggestion"])

    def test_braking_advice_after_close_hard_stops(self):
        brakes = [{"leadPresent": True, "leadDistanceM": 9.0, "speedMs": 20.0}]
        drives = [{"trial": None, "summaries": [minute(hardBrakes=brakes)] * 2}]
        report = nightly_recommendation(drives, nnlc_on=True)
        self.assertEqual(report["hardBrakes"], 2)
        self.assertEqual(report["closeLeadHardBrakes"], 2)
        self.assertIn("following-distance", report["brakingAdvice"])

    def test_at_most_one_suggestion(self):
        report = nightly_recommendation([self.baseline(), self.trial(), self.trial()], nnlc_on=False)
        self.assertEqual(report["suggestion"]["kind"], "keep_trial")
        self.assertEqual(len(report["trials"]), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_rules`, `ModuleNotFoundError: No module named 'rules'`

- [ ] **Step 3: Write the implementation**

```python
"""Drive Advisor suggestion rules. First version: simple thresholds, tuned in
rollout step 1 against the owner's stored drives."""
from __future__ import annotations

MIN_ENGAGED_MINUTES = 5.0
LOOKBACK_MINUTES = 10
WEAVE_HIGH_PER_MIN = 6.0
CURVE_ERR_HIGH = 0.25
KEEP_MIN_IMPROVEMENT = 0.15
COMPARABLE_SPEED_MS = 5.0
NNLC_WARNINGS_PER_ENGAGED_HOUR = 2.0
CLOSE_LEAD_M = 15.0
CLOSE_HARD_BRAKES_FOR_ADVICE = 2
MAX_LIVE_TRIALS_PER_DRIVE = 2
TARGETS = {
    "weaving_on_straight_road": ("weavePerStraightMinute", "straightMinutes"),
    "late_in_curves": ("curveRelError", "curveSamples"),
}
BRAKING_ADVICE = ("Several hard stops happened with a car close ahead. A longer following-distance "
                  "setting on the steering wheel gives the truck's cruise control more room.")


def _weighted(rows: list, key: str, weight_key: str) -> float | None:
    num = den = 0.0
    for row in rows:
        value, weight = row.get(key), row.get(weight_key)
        if value is None or not weight:
            continue
        num += value * weight
        den += weight
    return num / den if den else None


def _engaged(rows: list) -> float:
    return sum(row.get("engagedMinutes") or 0.0 for row in rows)


def _warnings_per_engaged_minute(rows: list) -> float | None:
    engaged = _engaged(rows)
    return sum(row.get("steerWarningEpisodes", 0) for row in rows) / engaged if engaged else None


def live_suggestion(summaries: list, *, trials_this_drive: int, trial_active: bool) -> dict | None:
    if trial_active or trials_this_drive >= MAX_LIVE_TRIALS_PER_DRIVE:
        return None
    recent = summaries[-LOOKBACK_MINUTES:]
    if _engaged(recent) < MIN_ENGAGED_MINUTES:
        return None
    weave = _weighted(recent, *TARGETS["weaving_on_straight_road"])
    curve = _weighted(recent, *TARGETS["late_in_curves"])
    weave_high = weave is not None and weave >= WEAVE_HIGH_PER_MIN
    curve_high = curve is not None and curve >= CURVE_ERR_HIGH
    if weave_high == curve_high:
        return None
    if weave_high:
        return {"kind": "live_delay_trial", "step": -1, "reason": "weaving_on_straight_road",
                "metric": round(weave, 2)}
    return {"kind": "live_delay_trial", "step": 1, "reason": "late_in_curves", "metric": round(curve, 3)}


def _judge_trial(trial: dict, trial_rows: list, baseline_rows: list) -> dict:
    reason = trial.get("reason")
    if reason not in TARGETS:
        return {"reason": reason, "keep": False, "why": "unknown_trial_reason"}
    if _engaged(trial_rows) < MIN_ENGAGED_MINUTES:
        return {"reason": reason, "keep": False, "why": "not_enough_trial_driving"}
    speeds = [r["speedMeanMs"] for r in trial_rows if r.get("speedMeanMs") is not None]
    mean_speed = sum(speeds) / len(speeds) if speeds else None
    comparable = [r for r in baseline_rows if r.get("engagedMinutes") and mean_speed is not None
                  and r.get("speedMeanMs") is not None
                  and abs(r["speedMeanMs"] - mean_speed) <= COMPARABLE_SPEED_MS]
    key, weight_key = TARGETS[reason]
    base, tried = _weighted(comparable, key, weight_key), _weighted(trial_rows, key, weight_key)
    if not base or tried is None:
        return {"reason": reason, "keep": False, "why": "no_comparable_driving"}
    improvement = (base - tried) / base
    base_warn = _warnings_per_engaged_minute(comparable) or 0.0
    trial_warn = _warnings_per_engaged_minute(trial_rows) or 0.0
    keep = improvement >= KEEP_MIN_IMPROVEMENT and trial_warn <= base_warn + 1e-9
    return {"reason": reason, "keep": keep, "improvement": round(improvement, 3),
            "why": "improved" if keep else ("more_warnings" if trial_warn > base_warn else "not_enough_improvement")}


def nightly_recommendation(drives: list, *, nnlc_on: bool) -> dict:
    rows = [r for d in drives for r in d["summaries"]]
    engaged = _engaged(rows)
    warnings = sum(r.get("steerWarningEpisodes", 0) for r in rows)
    brakes = [b for r in rows for b in r.get("hardBrakes", [])]
    close = [b for b in brakes if b.get("leadPresent") and b.get("leadDistanceM") is not None
             and b["leadDistanceM"] <= CLOSE_LEAD_M]
    report = {
        "drives": len(drives),
        "engagedMinutes": round(engaged, 1),
        "steerWarnings": warnings,
        "steerWarningsPerEngagedHour": round(warnings / engaged * 60, 2) if engaged else None,
        "laneChanges": sum(r.get("laneChanges", 0) for r in rows),
        "overrideEpisodes": sum(r.get("overrideEpisodes", 0) for r in rows),
        "hardBrakes": len(brakes),
        "closeLeadHardBrakes": len(close),
        "brakingAdvice": BRAKING_ADVICE if len(close) >= CLOSE_HARD_BRAKES_FOR_ADVICE else None,
        "trials": [],
        "suggestion": None,
    }
    baseline = [r for d in drives if not d.get("trial") for r in d["summaries"]]
    for drive in drives:
        if not drive.get("trial"):
            continue
        verdict = _judge_trial(drive["trial"], drive["summaries"], baseline)
        report["trials"].append(verdict)
        if verdict["keep"] and report["suggestion"] is None:
            report["suggestion"] = {"kind": "keep_trial", "change": drive["trial"]["change"],
                                    "reason": f"{verdict['reason']}_improved"}
    rate = report["steerWarningsPerEngagedHour"]
    if report["suggestion"] is None and not nnlc_on and rate is not None and rate >= NNLC_WARNINGS_PER_ENGAGED_HOUR:
        report["suggestion"] = {"kind": "nightly_setting", "change": {"NeuralNetworkLateralControl": True},
                                "reason": "frequent_steering_warnings"}
    return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 65 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/rules.py ram1500-development/advisor/test_rules.py
git commit -m "advisor: live delay-trial rule and nightly keep/neural-steering recommendation"
```

---

### Task 8: Offline replay tool

**Files:**
- Create: `ram1500-development/advisor/replay_report.py`
- Test: `ram1500-development/advisor/test_replay_report.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Event streams become one summary per minute."""
import unittest
from types import SimpleNamespace as ns

from replay_report import summaries_from_events

CS = ns(vEgo=25.0, aEgo=0.0, steeringRateDeg=0.0, steeringPressed=False)


class ReplayTests(unittest.TestCase):
    def test_one_summary_per_minute_plus_remainder(self):
        events = []
        for i in range(1250):  # 125 s at 10 Hz
            t = i * 100_000_000
            events.append(("carControl", t, ns(latActive=True)))
            events.append(("controlsState", t, ns(desiredCurvature=0.0, curvature=0.0)))
            events.append(("carState", t, CS))
        summaries = summaries_from_events(events, sample_hz=10.0)
        self.assertEqual([s["samples"] for s in summaries], [600, 600, 50])
        self.assertEqual(summaries[0]["engagedMinutes"], 1.0)

    def test_no_events_no_summaries(self):
        self.assertEqual(summaries_from_events([], sample_hz=10.0), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: ERROR in `test_replay_report`, `ModuleNotFoundError: No module named 'replay_report'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Offline Drive Advisor replay: Q-log files -> per-minute summaries and suggestions.

Usage (decoded with the installed build's schema; prints numbers only, never
route names, locations or identifiers):
  python3 replay_report.py --schema-dir SCHEMA_DIR qlog1.zst [qlog2.zst ...]
"""
from __future__ import annotations

import argparse
import json

from metrics import MinuteSummary
from rules import live_suggestion
from samples import SampleBuilder

SERVICES = ("carState", "carControl", "controlsState", "carOutput", "radarState",
            "drivingModelData", "onroadEvents")


def summaries_from_events(events, *, sample_hz: float = 10.0, window_s: float = 60.0) -> list:
    builder = SampleBuilder(sample_hz=sample_hz)
    summaries, current = [], None
    for service, t_ns, msg in events:
        sample = builder.ingest(service, t_ns, msg)
        if sample is None:
            continue
        if current is not None and sample.t - current.start_t >= window_s:
            summaries.append(current.finish(sample_hz))
            current = None
        if current is None:
            current = MinuteSummary(start_t=sample.t)
        current.add(sample)
    if current is not None and current.samples:
        summaries.append(current.finish(sample_hz))
    return summaries


def _qlog_events(paths, schema_dir):
    import io
    import os

    import capnp
    import zstandard

    log = capnp.load(os.path.join(schema_dir, "log.capnp"), imports=[schema_dir])
    for path in paths:
        with open(path, "rb") as handle:
            data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(handle.read())).read()
        for event in log.Event.read_multiple_bytes(data):
            kind = event.which()
            if kind in SERVICES:
                body = getattr(event, kind)
                yield kind, event.logMonoTime, (list(body) if kind == "onroadEvents" else body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", required=True)
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("qlogs", nargs="+")
    args = parser.parse_args()
    summaries = summaries_from_events(_qlog_events(args.qlogs, args.schema_dir), sample_hz=args.sample_hz)
    for i, summary in enumerate(summaries):
        summary = {k: v for k, v in summary.items() if k != "startT"}
        suggestion = live_suggestion(summaries[: i + 1], trials_this_drive=0, trial_active=False)
        print(json.dumps({"minute": i, "summary": summary, "liveSuggestion": suggestion}, sort_keys=True))


if __name__ == "__main__":
    main()
```

Note: Q-logs sample `carState` at 10 Hz, so `--sample-hz 10` matches them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 67 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add ram1500-development/advisor/replay_report.py ram1500-development/advisor/test_replay_report.py
git commit -m "advisor: offline replay tool from Q-logs to per-minute summaries"
```

---

### Task 9: Full check and hand-off

- [ ] **Step 1: Run the whole suite**

Run: `python3 -m unittest discover -s ram1500-development/advisor -v`
Expected: 67 tests, `OK`

- [ ] **Step 2: Privacy check of the new files**

Run: `git grep -nE '10\.0\.0\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|ntfy\.sh/[A-Za-z0-9]|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-' -- ram1500-development/advisor ram1500-development/specs ram1500-development/plans`
Expected: no output

- [ ] **Step 3: Ask the owner before pushing**

Pushing publishes the branch on the public fork. Push only after the owner says yes:

```bash
git push -u origin ram1500-drive-advisor
```

- [ ] **Step 4: Rollout step 1 on stored drives (owner go-ahead required)**

Reading drives from the private log bucket needs the owner's explicit go-ahead at that time. With it, download the newest engaged drive's Q-logs to a private scratch folder (never into the repository), run `replay_report.py` with the `1f7233cb` schema, and report only the printed numbers. Adjust the thresholds at the top of `rules.py` only if known calm drives produce suggestions or known weaving drives produce none, and add a regression test for each adjustment.
