"""Acceptance: time base, stream joins and shared flags on the private dataset.

Runs every drive's decimated-log tables through the Pipeline with a probe that
only sums the shared flags, and compares the sums with the expected-numbers
file beside the dataset. Skipped when the dataset is not on this machine.
"""
import unittest

import acceptance_data
import npz_input
from measure_api import Measure
from pipeline import Pipeline, PipelineConfig

SECTION = "foundations"
KEYS = ("windowS", "latOnS", "steeringS", "forcedZeroS", "eligibleS", "straightEligibleS")


class FlagSums(Measure):
    name = "flagSums"
    owner_facing = True

    def __init__(self):
        self.s = dict.fromkeys(KEYS, 0.0)
        self.gap_samples = 0

    def on_sample(self, ctx, g):
        dt = g.sample.dt
        self.s["windowS"] += dt
        self.gap_samples += g.sample.gap
        if g.lat_on:
            self.s["latOnS"] += dt
        if g.steering:
            self.s["steeringS"] += dt
        if g.forced_zero:
            self.s["forcedZeroS"] += dt

    def on_evaluate(self, ctx, g):
        if g.eligible:
            self.s["eligibleS"] += g.sample.dt
        if g.straight_eligible:
            self.s["straightEligibleS"] += g.sample.dt


def run_drive(drive: int, without=()):
    """Sums of one drive, or None when the drive has no decimated-log table."""
    path = acceptance_data.npz_path(drive, "q")
    if path is None:
        return None
    probe = FlagSums()
    pipeline = Pipeline(PipelineConfig.for_decimated_log(measures=(lambda: probe,)))
    summaries = 0
    for batch in npz_input.decimated_batches(npz_input.load(path), without=without):
        summaries += sum(kind == "summary" for kind, _ in pipeline.ingest_tick(batch))
    summaries += sum(kind == "summary" for kind, _ in pipeline.end())
    end = pipeline.drive_end_fields()
    return {"sums": probe.s, "gapSamples": probe.gap_samples, "windows": summaries, "end": end}


class FoundationsAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(SECTION)
        cls.got = {}
        for drive in range(1, cls.want["drives"] + 1):
            result = run_drive(drive)
            if result is not None:
                cls.got[drive] = result

    def per_drive(self, key, expected_key, scale, tolerance_key):
        tolerance = self.want["tolerance"][tolerance_key]
        for drive, value in self.want[expected_key].items():
            with self.subTest(drive=int(drive)):
                self.assertAlmostEqual(self.got[int(drive)]["sums"][key] / scale, value, delta=tolerance)

    def total(self, key, drives, scale):
        return sum(self.got[d]["sums"][key] for d in drives if d in self.got) / scale

    def test_every_drive_has_a_table(self):
        self.assertEqual(len(self.got), self.want["drives"])

    def test_time_base(self):
        self.assertAlmostEqual(self.total("windowS", self.got, 60.0), self.want["windowMinAllDrives"],
                               delta=self.want["tolerance"]["windowMinAllDrives"])
        self.assertEqual({d: r["gapSamples"] for d, r in self.got.items() if r["gapSamples"]},
                         {int(d): n for d, n in self.want["gapSamples"].items()})
        for drive, result in self.got.items():
            with self.subTest(drive=drive):
                self.assertEqual(result["end"]["windows"], result["windows"])
                self.assertAlmostEqual(result["end"]["totals"]["windowS"], result["sums"]["windowS"], delta=0.051)

    def test_lat_on_time(self):
        self.per_drive("latOnS", "latOnMin", 60.0, "minPerDrive")
        self.assertAlmostEqual(self.total("latOnS", self.want["steeringDrives"], 60.0),
                               self.want["latOnMinSteeringDrives"], delta=self.want["tolerance"]["minTotal"])
        self.assertAlmostEqual(self.total("latOnS", self.got, 60.0), self.want["latOnMinAllDrives"],
                               delta=self.want["tolerance"]["minTotal"])

    def test_steering_on_time(self):
        self.per_drive("steeringS", "steeringMin", 60.0, "minPerDrive")
        self.assertAlmostEqual(self.total("steeringS", self.want["steeringDrives"], 60.0),
                               self.want["steeringMinSteeringDrives"], delta=self.want["tolerance"]["minTotal"])

    def test_forced_zero_time(self):
        self.per_drive("forcedZeroS", "forcedZeroS", 1.0, "forcedZeroS")
        self.assertAlmostEqual(self.total("forcedZeroS", self.want["steeringDrives"], 1.0),
                               self.want["forcedZeroSSteeringDrives"], delta=self.want["tolerance"]["forcedZeroS"])

    def test_forced_zero_fallback_across_a_long_hole(self):
        case = self.want["holeWithoutAlerts"]
        result = run_drive(case["drive"], without=("onroadEvents",))
        self.assertAlmostEqual(result["sums"]["forcedZeroS"], case["forcedZeroS"],
                               delta=self.want["tolerance"]["forcedZeroS"])
        self.assertGreater(result["sums"]["latOnS"], 0.0)

    def test_eligible_time(self):
        self.assertAlmostEqual(self.total("eligibleS", self.want["steeringDrives"], 60.0),
                               self.want["eligibleMinSteeringDrives"], delta=self.want["tolerance"]["gateMinTotal"])

    def test_straight_eligible_time(self):
        self.per_drive("straightEligibleS", "straightEligibleMin", 60.0, "gateMinPerDrive")
        self.assertAlmostEqual(self.total("straightEligibleS", self.want["steeringDrives"], 60.0),
                               self.want["straightEligibleMinSteeringDrives"],
                               delta=self.want["tolerance"]["gateMinTotal"])


if __name__ == "__main__":
    unittest.main()
