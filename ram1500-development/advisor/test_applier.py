"""Applier as it is built: it applies nothing, and still puts back what an earlier trial replaced.

The rules of the apply path itself are tested in test_dormant_path.py, with the lock patched open.
"""
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


class ApplierFixture(unittest.TestCase):
    """Fake settings, with a journal and a nonce ledger in a temporary folder."""

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

    def leave_trial_open(self):
        """The state an earlier version left behind: trial values in force and the journal open."""
        self.applier.journal.open_trial(suggestion_id="s0", written=dict(LIVE),
                                        previous={key: START[key] for key in LIVE})
        self.params.values.update(LIVE)


class ApplyDisabledTests(ApplierFixture):
    def test_valid_approval_in_park_is_refused(self):
        self.assertEqual(self.apply(self.approval()), "refused:apply_disabled")
        self.assertEqual(self.apply(self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly")),
                         "refused:apply_disabled")
        self.assertEqual(self.params.writes, [])
        self.assertEqual(self.params.values, START)
        self.assertFalse(self.applier.journal.is_open())

    def test_refusal_comes_before_every_other_check(self):
        forged = self.approval()
        forged["sig"] = "0" * 64
        moving = VehicleState(in_park=False, speed_ms=20.0, engaged=True)
        out_of_range = self.approval(change={"LagdToggle": False, "LagdToggleDelay": 0.4})
        for approval, state in ((forged, PARKED), (self.approval(), moving), (out_of_range, PARKED),
                                ("not an approval", PARKED)):
            with self.subTest(approval=approval, state=state):
                self.assertEqual(self.apply(approval, state=state), "refused:apply_disabled")
        self.assertEqual(self.params.writes, [])

    def test_refused_approval_is_not_marked_used(self):
        self.apply(self.approval())
        self.assertEqual(self.applier.nonces.used(), set())
        self.assertFalse(self.applier.nonces.path.exists())


class RestoreTests(ApplierFixture):
    """Restore still runs: a trial left open by an earlier version is put back."""

    def test_restore_puts_previous_values_back_once(self):
        self.leave_trial_open()
        self.assertEqual(self.applier.restore(onroad=False), "restored")
        self.assertEqual(self.params.values, START)
        self.assertEqual(sorted(self.params.writes), [("LagdToggle", True), ("LagdToggleDelay", 0.2)])
        self.assertFalse(self.applier.journal.is_open())
        self.assertEqual(self.applier.restore(onroad=False), "nothing_to_restore")
        self.assertEqual(len(self.params.writes), 2)

    def test_restore_waits_while_driving(self):
        self.leave_trial_open()
        self.assertEqual(self.applier.restore(onroad=True), "deferred_until_drive_end")
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.1)
        self.assertTrue(self.applier.journal.is_open())

    def test_owner_change_wins(self):
        self.leave_trial_open()
        self.params.values["LagdToggle"] = True
        self.assertEqual(self.applier.restore(onroad=False), "owner_changed:LagdToggle")
        self.assertEqual(self.params.writes, [])
        self.assertFalse(self.applier.journal.is_open())

    def test_restore_after_agent_restart(self):
        self.leave_trial_open()
        fresh = Applier(self.params, Journal(self.applier.journal.path),
                        NonceLedger(self.applier.nonces.path), KEY)
        self.assertEqual(fresh.restore(onroad=False), "restored")
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)

    def test_nothing_to_restore(self):
        self.assertEqual(self.applier.restore(onroad=False), "nothing_to_restore")
        self.assertEqual(self.params.writes, [])


if __name__ == "__main__":
    unittest.main()
