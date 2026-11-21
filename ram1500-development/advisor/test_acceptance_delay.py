"""Acceptance: the learned steering delay on the private dataset.

Replays every drive's decimated-log tables through the pipeline with the
measures of this build and compares the delay block of the summaries with the
expected-numbers file beside the dataset. The tables hold the learned value
only (no status, no log header), so the status and the settings of a drive are
not checked here: that needs the raw logs. Skipped when the dataset is not on
this machine.
"""
import unittest

import acceptance_data

SECTION = "delay"


class DelayAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(SECTION)
        cls.tolerance = cls.want["tolerance"]
        cls.runs = {}
        for drive in range(1, cls.want["drives"] + 1):
            run = acceptance_data.qlog_run(drive)
            if run is not None:
                cls.runs[drive] = run

    def blocks(self, drive):
        return [s["delay"] for s in self.runs[drive]["summaries"] if "delay" in s]

    def test_every_drive_has_a_table_and_the_measure_did_not_fail(self):
        self.assertEqual(len(self.runs), self.want["drives"])
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertNotIn("delay", run["end"]["failed"])

    def test_smallest_and_largest_value_of_each_drive(self):
        for drive, (low, high) in self.want["rangePerDrive"].items():
            with self.subTest(drive=int(drive)):
                blocks = self.blocks(int(drive))
                self.assertAlmostEqual(min(b["minS"] for b in blocks), low, delta=self.tolerance)
                self.assertAlmostEqual(max(b["maxS"] for b in blocks), high, delta=self.tolerance)

    def test_last_value_of_a_drive(self):
        for drive, value in self.want["lastValue"].items():
            with self.subTest(drive=int(drive)):
                blocks = self.blocks(int(drive))
                self.assertAlmostEqual(blocks[-1]["valueS"], value, delta=self.tolerance)
                low, high = self.want["rangePerDrive"][drive]
                self.assertTrue(low - self.tolerance <= blocks[-1]["valueS"] <= high + self.tolerance)

    def test_every_value_of_every_drive_lies_in_the_stated_band_and_none_is_the_unlearned_default(self):
        low, high = self.want["allDrives"]
        seen = []
        for drive in self.runs:
            for block in self.blocks(drive):
                for key in ("valueS", "minS", "maxS"):
                    self.assertTrue(low - self.tolerance <= block[key] <= high + self.tolerance, (drive, key))
                    self.assertNotAlmostEqual(block[key], self.want["unlearnedDefault"], delta=0.01)
                seen.extend((block["minS"], block["maxS"]))
        self.assertAlmostEqual(min(seen), low, delta=self.tolerance)   # the band is as tight as stated
        self.assertAlmostEqual(max(seen), high, delta=self.tolerance)

    def test_the_block_is_there_from_the_first_window_on_and_holds_together(self):
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertTrue(all("delay" in s for s in run["summaries"]))
                for block in self.blocks(drive):
                    self.assertLessEqual(block["minS"], block["valueS"])
                    self.assertLessEqual(block["valueS"], block["maxS"])
                    self.assertEqual((block["status"], block["blocks"]), (None, None))     # not in the tables

    def test_without_a_status_no_value_is_trusted_as_the_learned_delay(self):
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertEqual((run["end"]["delay"]["learnedS"], run["end"]["delay"]["outOfBand"]), (None, False))


if __name__ == "__main__":
    unittest.main()
