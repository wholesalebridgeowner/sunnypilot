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
