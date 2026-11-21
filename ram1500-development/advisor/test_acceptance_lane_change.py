"""Acceptance: lane-change counts on the private dataset.

Replays every drive's decimated-log tables through the pipeline with the
measures of this build and compares the per-window counts and the lane_change
records with the expected-numbers file beside the dataset. Skipped when the
dataset is not on this machine.

A decimated log holds the model stream at 2 Hz, so the settle figures are not
measured here (every completed lane change reads slow_stream). The full-rate
rows of the table need the replay tool's tick modes.
"""
import unittest

import acceptance_data

SECTION = "laneChange"
COUNTS = ("lcStarts", "lcCompleted", "lcCancelled", "lcBlinkerOnly")


class LaneChangeAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(SECTION)["decimated"]
        cls.runs = {}
        for drive in range(1, cls.want["drives"] + 1):
            run = acceptance_data.qlog_run(drive)
            if run is not None:
                cls.runs[drive] = run

    def count(self, drive, key):
        return sum(s[key] for s in self.runs[drive]["summaries"])

    def records(self, drive):
        return self.runs[drive]["records"].get("lane_change", [])

    def test_every_drive_has_a_table_and_no_measure_failed(self):
        self.assertEqual(len(self.runs), self.want["drives"])
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertEqual(run["end"]["failed"], [])

    def test_counts_per_drive(self):
        for drive in self.runs:
            with self.subTest(drive=drive):                            # nothing on a drive the table does not name
                self.assertEqual([self.count(drive, key) for key in COUNTS],
                                 self.want["perDrive"].get(str(drive), [0, 0, 0, 0]))

    def test_counts_in_all(self):
        self.assertEqual([sum(self.count(d, key) for d in self.runs) for key in COUNTS], self.want["total"])

    def test_no_attempt_is_truncated(self):
        self.assertEqual(sum(self.count(d, "lcTruncated") for d in self.runs), self.want["truncated"])
        self.assertEqual(sum(run["end"]["truncatedLaneChanges"] for run in self.runs.values()), self.want["truncated"])

    def test_completed_with_driver_steering_detected(self):
        got = {str(d): self.count(d, "lcHandsOn") for d in self.runs if self.count(d, "lcHandsOn")}
        self.assertEqual(got, self.want["handsOnPerDrive"])

    def test_one_record_per_attempt(self):
        for drive in self.runs:
            records = self.records(drive)
            with self.subTest(drive=drive):
                self.assertEqual(len(records), self.count(drive, "lcStarts"))
                self.assertEqual(sum(1 for r in records if r["outcome"] == "completed"),
                                 self.count(drive, "lcCompleted"))
                self.assertEqual(sum(1 for r in records if r["outcome"] == "completed" and r["handsOn"]),
                                 self.count(drive, "lcHandsOn"))

    def test_every_completed_lane_change_has_a_crossing(self):
        completed = [r for d in self.runs for r in self.records(d) if r["outcome"] == "completed"]
        self.assertEqual(sum(1 for r in completed if r["crossed"]), self.want["completedWithCrossing"])
        self.assertTrue(all(r["settleState"] == "slow_stream" for r in completed if r["crossed"]))

    def test_no_cancelled_attempt_reached_the_finishing_phase(self):
        cancelled = [r for d in self.runs for r in self.records(d) if r["outcome"] == "cancelled"]
        self.assertEqual(sum(1 for r in cancelled if r["startingS"] is not None),
                         self.want["cancelledThatReachedFinishing"])
        self.assertTrue(all(r["settleState"] == "no_crossing" for r in cancelled))

    def test_every_record_names_a_direction(self):
        for drive in self.runs:
            for record in self.records(drive):
                self.assertIn(record["dir"], ("L", "R"))
                self.assertIsNotNone(record["pushTorqueMax"])


if __name__ == "__main__":
    unittest.main()
