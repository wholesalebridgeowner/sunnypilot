"""Acceptance: braking episodes on the private dataset.

Replays every drive's decimated-log tables through the pipeline with the
measures of this build and compares the brake records and the per-window
counts with the expected-numbers file beside the dataset. Skipped when the
dataset is not on this machine.
"""
import unittest

import acceptance_data

SECTION = "brake"
HARD_MS, VERY_HARD_MS = 3.0, 4.0            # grades by the speed lost in one second (cut by the service)
BANDS = (("2.0-2.5", 2.0, 2.5), ("2.5-3.0", 2.5, 3.0), ("3.0-4.0", 3.0, 4.0), ("4.0+", 4.0, 1e9))
MPH = 2.23694


def kind(record) -> str:
    """Who braked, as the tables name it."""
    if record["who"] != "driver":
        return record["who"]
    return "driver after cruise" if record["tookOverFromCruise"] else "driver"


class BrakeAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(SECTION)
        cls.tolerance = cls.want["tolerance"]
        cls.records = {}
        cls.runs = {}
        for drive in range(1, cls.want["drives"] + 1):
            run = acceptance_data.qlog_run(drive)
            if run is not None:
                cls.runs[drive] = run
                cls.records[drive] = run["records"].get("brake", [])
        cls.all = [r for drive in cls.records for r in cls.records[drive]]
        cls.hard = {drive: [r for r in records if r["peakDrop1s"] >= HARD_MS] for drive, records in cls.records.items()}

    def per_drive(self, counts):
        return {str(drive): n for drive, n in counts.items() if n}

    def test_every_drive_has_a_table_and_no_measure_failed(self):
        self.assertEqual(len(self.runs), self.want["drives"])
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertEqual(run["end"]["failed"], [])

    def test_episodes_by_grade(self):
        peaks = [r["peakDrop1s"] for r in self.all]
        self.assertEqual(len(peaks), self.want["episodes"])
        self.assertEqual(sum(1 for p in peaks if p < HARD_MS), self.want["firm"])
        self.assertEqual(sum(1 for p in peaks if HARD_MS <= p < VERY_HARD_MS), self.want["hard"])
        self.assertEqual(sum(1 for p in peaks if p >= VERY_HARD_MS), self.want["veryHard"])
        self.assertGreaterEqual(min(peaks), 2.0)                       # nothing under the opening drop is sent

    def test_episodes_per_drive(self):
        self.assertEqual(self.per_drive({d: len(r) for d, r in self.records.items()}), self.want["episodesPerDrive"])

    def test_the_window_counts_add_up_to_the_records(self):
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertEqual(sum(s["brakeEpisodes"] for s in run["summaries"]), len(self.records[drive]))

    def test_hard_stops_per_drive_and_by_who(self):
        self.assertEqual(self.per_drive({d: len(r) for d, r in self.hard.items()}), self.want["hardPerDrive"])
        hard = [r for records in self.hard.values() for r in records]
        got = {name: sum(1 for r in hard if kind(r) == name) for name in ("driver", "driver after cruise", "cruise",
                                                                           "other")}
        self.assertEqual(got, self.want["hardByWho"])

    def test_grade_by_who(self):
        for band, low, high in BANDS:
            in_band = [r for r in self.all if low <= r["peakDrop1s"] < high]
            got = [sum(1 for r in in_band if kind(r) == name) for name in ("driver", "driver after cruise", "cruise")]
            with self.subTest(band=band):
                self.assertEqual(got, self.want["gradeByWho"][band])

    def test_cruise_braked_episodes(self):
        got = {d: sum(1 for r in records if r["who"] == "cruise") for d, records in self.records.items()}
        self.assertEqual(self.per_drive(got), self.want["cruisePerDrive"])

    def test_no_episode_is_braked_by_neither(self):
        self.assertEqual(sum(1 for r in self.all if r["who"] == "other"), self.want["otherEpisodes"])

    def test_took_over_is_only_set_for_the_driver(self):
        self.assertTrue(all(r["who"] == "driver" for r in self.all if r["tookOverFromCruise"]))

    def test_the_hard_stops_one_by_one(self):
        for drive, stops in self.want["hardStops"].items():
            got = self.hard[int(drive)]
            self.assertEqual(len(got), len(stops))
            for i, (record, (peak1, peak2, who, start_mph)) in enumerate(zip(got, stops)):
                with self.subTest(drive=int(drive), stop=i):
                    self.assertAlmostEqual(record["peakDrop1s"], peak1, delta=self.tolerance["peakMs"] + 1e-9)
                    self.assertAlmostEqual(record["peakDrop2s"], peak2, delta=self.tolerance["peakMs"] + 1e-9)
                    self.assertEqual(kind(record), who)
                    self.assertAlmostEqual(record["speedMs"] * MPH, start_mph, delta=self.tolerance["startMph"])

    def test_a_start_up_speed_step_creates_no_episode(self):
        step = self.want["startUpStep"]
        records = self.records[step["drive"]]
        self.assertEqual(len(records), step["episodes"])
        self.assertTrue(all(r["peakDrop1s"] < HARD_MS for r in records))

    def test_lead_context_is_whole_or_absent(self):
        for r in self.all:
            self.assertEqual(r["leadGapS"] is None, r["leadDistanceM"] is None)
        with_lead = sum(1 for records in self.hard.values() for r in records if r["leadGapS"] is not None)
        self.assertEqual(with_lead, self.want["hardWithLeadContext"])


if __name__ == "__main__":
    unittest.main()
