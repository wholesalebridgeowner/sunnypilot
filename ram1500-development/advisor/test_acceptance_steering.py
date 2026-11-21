"""Acceptance: steering-on time, forced-zero time and driving time on the private dataset.

Replays every drive's decimated-log tables through the pipeline with the
measures of this build and compares what the summaries and the drive-end
totals say with the expected-numbers file beside the dataset. Skipped when the
dataset is not on this machine.
"""
import unittest

import acceptance_data

SECTION = "steering"


class SteeringAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(SECTION)
        cls.tolerance = cls.want["tolerance"]
        cls.runs = {}
        for drive in range(1, cls.want["drives"] + 1):
            run = acceptance_data.qlog_run(drive)
            if run is not None:
                cls.runs[drive] = run

    def minutes(self, drive, key):
        return sum(s[key] for s in self.runs[drive]["summaries"]) / 60.0

    def total(self, key, drives):
        return sum(self.minutes(d, key) for d in drives if d in self.runs)

    def per_drive(self, key, expected_key, scale, tolerance_key):
        for drive, value in self.want[expected_key].items():
            with self.subTest(drive=int(drive)):
                self.assertAlmostEqual(self.minutes(int(drive), key) * scale, value,
                                       delta=self.tolerance[tolerance_key] + 1e-9)

    def test_every_drive_has_a_table_and_no_measure_failed(self):
        self.assertEqual(len(self.runs), self.want["drives"])
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                self.assertEqual(run["end"]["failed"], [])
                self.assertEqual(run["end"]["windows"], len(run["summaries"]))

    def test_lat_on_time(self):
        self.per_drive("latOnS", "latOnMin", 1.0, "minPerDrive")
        self.assertAlmostEqual(self.total("latOnS", self.want["steeringDrives"]), self.want["latOnMinSteeringDrives"],
                               delta=self.tolerance["minTotal"])
        self.assertAlmostEqual(self.total("latOnS", self.runs), self.want["latOnMinAllDrives"],
                               delta=self.tolerance["minTotal"])

    def test_steering_on_time(self):
        self.per_drive("steeringS", "steeringMin", 1.0, "minPerDrive")
        self.assertAlmostEqual(self.total("steeringS", self.want["steeringDrives"]),
                               self.want["steeringMinSteeringDrives"], delta=self.tolerance["minTotal"])

    def test_forced_zero_time(self):
        self.per_drive("forcedZeroS", "forcedZeroS", 60.0, "forcedZeroS")
        self.assertAlmostEqual(self.total("forcedZeroS", self.want["steeringDrives"]) * 60.0,
                               self.want["forcedZeroSSteeringDrives"], delta=self.tolerance["forcedZeroS"])

    def test_steering_on_is_lat_on_less_forced_zero(self):
        for drive in self.runs:
            for s in self.runs[drive]["summaries"]:
                self.assertAlmostEqual(s["steeringS"] + s["forcedZeroS"], s["latOnS"], delta=0.11)

    def test_overlays(self):
        drives = self.want["steeringDrives"]
        self.assertAlmostEqual(self.total("pressedS", drives), self.want["pressedMinSteeringDrives"],
                               delta=self.tolerance["minTotal"])
        self.assertAlmostEqual(self.total("laneChangeS", drives), self.want["laneChangeMinSteeringDrives"],
                               delta=self.tolerance["minTotal"])
        for drive, run in self.runs.items():
            for s in run["summaries"]:                                 # driver steering is pressed time less the blips
                self.assertLessEqual(s["driverS"], s["pressedS"] + 0.11)
                self.assertLessEqual(s["pressedS"], s["latOnS"] + 0.11)
                self.assertLessEqual(s["laneChangeS"], s["latOnS"] + 0.11)

    def test_driving_time(self):
        self.per_drive("movingS", "movingMin", 1.0, "minOneDecimal")
        self.assertAlmostEqual(self.total("movingS", self.runs), self.want["movingMinAllDrives"],
                               delta=self.tolerance["minOneDecimal"])
        self.assertAlmostEqual(self.total("cruiseS", self.runs), self.want["cruiseMinAllDrives"],
                               delta=self.tolerance["minOneDecimal"])
        self.assertAlmostEqual(self.total("windowS", self.runs), self.want["windowMinAllDrives"],
                               delta=self.tolerance["minOneDecimal"])

    def test_drive_end_totals_agree_with_the_summaries(self):
        for drive, run in self.runs.items():
            with self.subTest(drive=drive):
                windows = len(run["summaries"])
                for key in ("steeringS", "movingS"):                   # each summary is rounded to 0.1 s
                    self.assertAlmostEqual(run["end"]["totals"][key], self.minutes(drive, key) * 60.0,
                                           delta=0.05 * windows + 0.051)

    def test_speed_means(self):
        for drive, run in self.runs.items():
            for s in run["summaries"]:
                self.assertIsNotNone(s["vMean"])
                self.assertEqual(s["vMeanSteering"] is None, s["steeringS"] == 0.0)

    def test_forced_zero_fallback_across_a_long_hole(self):
        case = self.want["holeWithoutAlerts"]
        run = acceptance_data.qlog_run(case["drive"], without=("onroadEvents",))
        forced = sum(s["forcedZeroS"] for s in run["summaries"])
        lat_on = sum(s["latOnS"] for s in run["summaries"])
        fallback = sum(s["fzFallbackS"] for s in run["summaries"])
        self.assertAlmostEqual(forced, case["forcedZeroS"], delta=self.tolerance["forcedZeroS"])
        self.assertGreater(lat_on, 0.0)
        self.assertAlmostEqual(fallback, lat_on, delta=0.11 * len(run["summaries"]))    # every second was a fallback second


if __name__ == "__main__":
    unittest.main()
