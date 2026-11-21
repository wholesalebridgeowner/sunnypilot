"""Acceptance: steering warnings, command-at-limit time, its split and the limit events on the private dataset.

Replays every drive's decimated-log tables through the pipeline with the
measures of this build and compares the summaries and the limit_event records
with the expected-numbers file beside the dataset. Skipped when the dataset is
not on this machine.
"""
import unittest

import acceptance_data
from shared_rules import LIMIT_EVENT_MIN_S

SECTION = "limit"
SPLIT = ("limitDriverS", "limitLaneChangeS", "limitNoneS")
EVENT_TOLERANCE = {"limitS": "eventS", "vMeanMs": "eventSpeedMs", "vAtMaxMs": "eventSpeedMs", "demandMax": "eventMs2",
                   "achievedAtMax": "eventMs2", "achievedMean": "eventMs2", "driverTorqueMean": "eventTorque",
                   "epsOverCmd": "eventRatio"}


class LimitAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(SECTION)
        cls.tolerance = cls.want["tolerance"]
        cls.runs = {}
        for drive in range(1, cls.want["drives"] + 1):
            run = acceptance_data.qlog_run(drive)
            if run is not None:
                cls.runs[drive] = run

    def seconds(self, drive, key):
        return sum(s[key] for s in self.runs[drive]["summaries"])

    def total(self, key):
        return sum(self.seconds(d, key) for d in self.runs)

    def events(self, drive):
        return self.runs[drive]["records"].get("limit_event", [])

    def near(self, got, want, tolerance_key):
        self.assertAlmostEqual(got, want, delta=self.tolerance[tolerance_key] + 1e-9)

    def test_every_drive_has_a_table(self):
        self.assertEqual(len(self.runs), self.want["drives"])

    def test_limit_time(self):
        for drive in self.runs:
            with self.subTest(drive=drive):                            # nothing on a drive the table does not name
                self.near(self.seconds(drive, "limitS"), self.want["limitS"].get(str(drive), 0.0), "sPerDrive")
        self.near(self.total("limitS"), self.want["limitSAllDrives"], "sTotal")

    def test_asked_for_limit_time(self):
        self.near(self.total("askS"), self.want["askSAllDrives"], "sTotal")
        self.near(self.total("askNoneS"), self.want["askNoneSAllDrives"], "sTotal")

    def test_warnings(self):
        got = {drive: self.seconds(drive, "warnEvents") for drive in self.runs}
        self.assertEqual({drive: n for drive, n in got.items() if n},
                         {int(drive): n for drive, n in self.want["warnEvents"].items()})
        for drive, n in got.items():
            with self.subTest(drive=drive):
                self.assertEqual(self.seconds(drive, "warnS") > 0, n > 0)

    def test_split(self):
        for drive in self.runs:
            want = self.want["split"].get(str(drive), [0.0, 0.0, 0.0])
            for key, value in zip(SPLIT, want):
                with self.subTest(drive=drive, key=key):
                    self.near(self.seconds(drive, key), value, "splitSPerDrive")
            with self.subTest(drive=drive):                            # the three parts are the whole
                self.assertAlmostEqual(sum(self.seconds(drive, key) for key in SPLIT), self.seconds(drive, "limitS"),
                                       delta=0.051 * len(self.runs[drive]["summaries"]))
        for key, value in zip(SPLIT, self.want["splitAllDrives"]):
            with self.subTest(key=key):
                self.near(self.total(key), value, "sTotal")

    def test_unexpected(self):
        self.near(self.total("limitUnexpectedS"), self.want["limitUnexpectedSAllDrives"], "sTotal")
        labels = [e["label"] for drive in self.runs for e in self.events(drive)]
        self.assertEqual(labels.count("unexpected"), self.want["unexpectedEvents"])

    def test_limit_events(self):
        self.assertEqual({drive: len(self.events(drive)) for drive in self.runs if self.events(drive)},
                         {int(drive): len(events) for drive, events in self.want["events"].items()})
        for drive, events in self.want["events"].items():
            for i, (got, want) in enumerate(zip(self.events(int(drive)), events)):
                with self.subTest(drive=int(drive), event=i):
                    self.assertEqual(set(got), set(want))
                    self.assertEqual((got["warned"], got["label"]), (want["warned"], want["label"]))
                    for key, tolerance_key in EVENT_TOLERANCE.items():
                        self.near(got[key], want[key], tolerance_key)

    def test_no_record_under_the_minimum_length(self):
        for drive in self.runs:
            for event in self.events(drive):
                self.assertGreaterEqual(event["limitS"], LIMIT_EVENT_MIN_S)

    def test_no_measure_failed(self):
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertEqual(run["end"]["failed"], [])


if __name__ == "__main__":
    unittest.main()
