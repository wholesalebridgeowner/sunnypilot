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
