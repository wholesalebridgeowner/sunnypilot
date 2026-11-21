"""Acceptance: the four log-only quality measures on the private dataset.

Lane sway, wheel swings, lateral-motion RMS and curve sums, each in two tiers,
on the decimated-log tables of the steering drives the expected-numbers file
names. Skipped when the dataset is not on this machine.

  Tier 1, port check. The analysts' own gates and booking (port_adapters.py)
  around the shipped cores must give the verified numbers again. This shows
  that the cores were ported faithfully.

  Tier 2, shared gates. What the shipped pipeline sends, turned into values
  as the service will (quality_values.py), against the numbers frozen in the
  expected-numbers file after the port check had passed.

No drive number is written here: every expected value comes from the file
beside the dataset. The rows of the tables that need full-rate logs seen at
other loop periods wait for the replay tool's tick mode.
"""
import math
import unittest

import acceptance_data
import npz_input
import port_adapters
import quality_values as qv
from pipeline import Pipeline, PipelineConfig


def percentile(values, q):
    """Linear-interpolated percentile, as numpy's default."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100.0
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def median(values):
    return percentile(values, 50)


_PORT = {}


def port_run(drive: int) -> dict:
    """The four adapters over one drive's decimated-log tables, in one pass. Kept: do not change the result."""
    if drive not in _PORT:
        path = acceptance_data.npz_path(drive, "q")
        if path is None:
            raise unittest.SkipTest(f"no decimated-log table for drive {drive}")
        tables = npz_input.load(path)
        chunk_of = {int(round(row[0] * 1e9)): int(row[1]) for row in tables["cs"]}   # the log chunk of each Sample
        lane = port_adapters.AnalystLaneSway()
        swings = port_adapters.AnalystSwings(window_key=chunk_of.__getitem__)
        ay = port_adapters.AnalystLateralRms()
        curve = port_adapters.AnalystCurve()
        adapters = (lane, swings, ay, curve)
        pipeline = Pipeline(PipelineConfig.for_decimated_log(measures=tuple((lambda m=m: m) for m in adapters)))
        for batch in npz_input.decimated_batches(tables):
            pipeline.ingest_tick(batch)
        pipeline.end()
        if pipeline.failed:
            raise AssertionError(f"adapters failed on drive {drive}: {pipeline.failed}")
        _PORT[drive] = {"lane": lane.booked(), "swings": swings.booked(), "ay": ay.booked(),
                        "curve": curve.booked(), "lag": curve.lag_booked()}
    return _PORT[drive]


class QualityAcceptance:
    """Shared by the four measures: the expected section, the port runs and the pipeline runs of its drives."""
    SECTION = ""
    BLOCK = ""

    @classmethod
    def setUpClass(cls):
        cls.want = acceptance_data.expected(cls.SECTION)
        cls.drives = cls.want["drives"]
        cls.t1, cls.t2 = cls.want["tier1"], cls.want["tier2"]
        cls.port = [port_run(drive) for drive in cls.drives]
        cls.runs = [acceptance_data.qlog_run(drive) for drive in cls.drives]
        cls.sent = [[s[cls.BLOCK] for s in run["summaries"] if cls.BLOCK in s] for run in cls.runs]

    def near(self, got, want, tolerance, what=""):
        """`want` is a number, or [low, high] where two verified versions of the measure differ."""
        low, high = (want, want) if not isinstance(want, list) else want
        self.assertTrue(low - tolerance - 1e-9 <= got <= high + tolerance + 1e-9,
                        f"{what}: {got} is not within {tolerance} of {want}")

    def each(self, got, want, tolerance, what):
        self.assertEqual(len(got), len(want))
        for i, (g, w) in enumerate(zip(got, want)):
            with self.subTest(what=what, position=i):
                self.near(g, w, tolerance, what)

    def test_no_measure_failed(self):
        for run in self.runs:
            self.assertEqual(run["end"]["failed"], [])


class LaneSwayAcceptance(QualityAcceptance, unittest.TestCase):
    SECTION, BLOCK = "laneSway", "lane"

    def test_tier1_per_minute_values(self):
        per_drive = [[v for v in map(qv.lane_sway_sd, run["lane"].values()) if v is not None] for run in self.port]
        values = [v for drive in per_drive for v in drive]
        tolerance = self.t1["tolerance"]
        self.near(len(values), self.t1["qualifyingMinutes"], 0, "qualifying minutes")
        self.near(median(values), self.t1["median"], tolerance["value"], "median")
        self.near(percentile(values, 90), self.t1["p90"], tolerance["value"], "p90")
        self.near(percentile(values, 95), self.t1["p95"], tolerance["value"], "p95")
        self.near(max(values), self.t1["max"], tolerance["value"], "max")
        self.each([median(v) for v in per_drive], self.t1["perDriveMedian"], tolerance["perDriveMedian"],
                  "per-drive median")

    def test_tier2_windows_and_values(self):
        per_drive = [[v for v in map(qv.lane_sway_sd, blocks) if v is not None] for blocks in self.sent]
        values = [v for drive in per_drive for v in drive]
        tolerance, frozen = self.t2["tolerance"], self.t2["all"]
        self.assertEqual([len(v) for v in per_drive], self.t2["qualifyingWindows"])
        self.assertEqual(len(values), frozen["n"])
        for name, got in (("median", median(values)), ("p90", percentile(values, 90)),
                          ("p95", percentile(values, 95)), ("max", max(values))):
            self.near(got, frozen[name], tolerance["value"], name)
        self.each([median(v) for v in per_drive], self.t2["perDriveMedian"], tolerance["value"], "per-drive median")

    def test_tier2_pooled_values_and_scored_time(self):
        pooled = [qv.pool("lane", blocks) for blocks in self.sent]
        self.each([qv.lane_sway_sd(p, 0.0) for p in pooled], self.t2["pooled"], self.t2["tolerance"]["value"], "pooled")
        self.each([p["scoredS"] / 60 for p in pooled], self.t2["scoredMin"], self.t2["tolerance"]["scoredMin"],
                  "scored minutes")

    def test_tier2_time_at_each_stage_only_shrinks(self):
        for blocks in self.sent:
            for b in blocks:
                self.assertLessEqual(b["scoredS"], b["validS"] + 0.11)
                self.assertLessEqual(b["validS"], b["eligibleS"] + 0.11)
                self.assertEqual(b["n"] == 0, b["vMean"] is None)


class WheelSwingsAcceptance(QualityAcceptance, unittest.TestCase):
    SECTION, BLOCK = "wheelSwings", "swings"

    def test_tier1_swings_per_straight_minute(self):
        # Pooled over the one-minute log chunks that hold at least a quarter of a qualified minute.
        pooled = [qv.pool("swings", [b for b in run["swings"].values() if b["straightS"] >= 15.0]) for run in self.port]
        tolerance = self.t1["tolerance"]
        self.each([p["n"] for p in pooled], self.t1["swings"], tolerance["swings"], "swings")
        self.each([p["straightS"] / 60 for p in pooled], self.t1["straightMin"], tolerance["straightMin"], "minutes")
        self.each([qv.swing_rate(p) for p in pooled], self.t1["rate"], tolerance["rate"], "rate")

    def test_tier2_swings_and_straight_time(self):
        pooled = [qv.pool("swings", blocks) for blocks in self.sent]
        tolerance = self.t2["tolerance"]
        self.each([p["n"] for p in pooled], self.t2["swings"], tolerance["swings"], "swings")
        self.each([p["straightS"] / 60 for p in pooled], self.t2["straightEligibleMin"],
                  tolerance["straightEligibleMin"], "straight-eligible minutes")
        self.each([qv.swing_rate(p) for p in pooled], self.t2["rate"], tolerance["rate"], "rate")
        everything = qv.pool("swings", pooled)
        self.near(qv.swing_rate(everything), self.t2["pooledRate"], tolerance["rate"], "pooled rate")


class LateralRmsAcceptance(QualityAcceptance, unittest.TestCase):
    SECTION, BLOCK = "lateralRms", "ay"

    def test_tier1_scored_minutes_and_values(self):
        per_drive = [[v for v in map(qv.lat_accel_rms, run["ay"].values()) if v is not None] for run in self.port]
        actual = [[v[0] for v in drive] for drive in per_drive]
        values = sorted(v for drive in actual for v in drive)
        tolerance, stated = self.t1["tolerance"]["value"], self.t1["all"]
        self.assertEqual([len(v) for v in actual], self.t1["scoredMinutes"])
        for name, got in (("median", median(values)), ("p90", percentile(values, 90)), ("p95", percentile(values, 95)),
                          ("p99", percentile(values, 99)), ("max", values[-1]), ("secondHighest", values[-2])):
            self.near(got, stated[name], tolerance, name)
        self.each([median(v) for v in actual], self.t1["perDrive"]["median"], tolerance, "per-drive median")
        self.each([percentile(v, 90) for v in actual], self.t1["perDrive"]["p90"], tolerance, "per-drive p90")
        self.each([max(v) for v in actual], self.t1["perDrive"]["max"], tolerance, "per-drive max")

    def test_tier1_actual_over_plan(self):
        ratios = [a / p for run in self.port for a, p in (v for v in map(qv.lat_accel_rms, run["ay"].values()) if v)]
        tolerance, stated = self.t1["tolerance"]["ratio"], self.t1["ratio"]
        for name, got in (("p5", percentile(ratios, 5)), ("median", median(ratios)), ("p95", percentile(ratios, 95)),
                          ("max", max(ratios))):
            self.near(got, stated[name], tolerance, name)

    def window_values(self, run):
        """Per window as the service will: at 30 accepted seconds, and only with the loop near ten a second."""
        values = [qv.lat_accel_rms(s.get("ay"), s["windowS"] / s["n"]) for s in run["summaries"]]
        return [v for v in values if v is not None]

    def test_tier2_scored_windows_and_values(self):
        actual = [[v[0] for v in self.window_values(run)] for run in self.runs]
        values = [v for drive in actual for v in drive]
        tolerance, frozen = self.t2["tolerance"]["value"], self.t2["all"]
        self.assertEqual([len(v) for v in actual], self.t2["scoredWindows"])
        self.assertEqual(len(values), frozen["n"])
        for name, got in (("median", median(values)), ("p90", percentile(values, 90)),
                          ("p95", percentile(values, 95)), ("max", max(values))):
            self.near(got, frozen[name], tolerance, name)
        self.each([median(v) for v in actual], self.t2["perDriveMedian"], tolerance, "per-drive median")

    def test_tier2_pooled_values(self):
        pooled = [qv.lat_accel_rms(qv.pool("ay", blocks), None, 0.0) for blocks in self.sent]
        tolerance = self.t2["tolerance"]["value"]
        self.each([p[0] for p in pooled], self.t2["pooledActual"], tolerance, "pooled actual")
        self.each([p[1] for p in pooled], self.t2["pooledPlan"], tolerance, "pooled plan")


class CurveSumsAcceptance(QualityAcceptance, unittest.TestCase):
    SECTION, BLOCK = "curveSums", "curve"

    def test_tier1_curve_sums(self):
        pooled = [qv.pool("curve", run["curve"].values()) for run in self.port]
        values = [qv.curve_tracking(p) for p in pooled]
        tolerance = self.t1["tolerance"]
        self.each([p["s"] for p in pooled], self.t1["curveS"], tolerance["seconds"], "curve seconds")
        self.assertEqual([p["events"] for p in pooled], self.t1["events"])
        self.each([v["below"] for v in values], self.t1["below"], tolerance["value"], "below")
        self.each([v["above"] for v in values], self.t1["above"], tolerance["value"], "above")
        self.each([p["s04"] for p in pooled], self.t1["s04"], tolerance["seconds"], "delayed curve seconds")
        self.each([v["below04"] for v in values], self.t1["below04"], tolerance["value"], "below, delayed pair")

    def test_tier1_low_speed_band(self):
        everything = qv.pool("curve", [qv.pool("curve", run["curve"].values()) for run in self.port])
        tolerance, low = self.t1["tolerance"], self.t1["low"]
        self.near(everything["lowS"], low["s"], tolerance["lowS"], "seconds")
        self.near(qv.curve_tracking(everything)["lowBelow"], low["below"], tolerance["lowBelow"], "below")
        self.near(everything["lowLimitS"], low["limitS"], tolerance["lowS"], "seconds at the limit")

    def test_tier1_lag(self):
        pooled = [qv.pool("lag", run["lag"].values()) for run in self.port]
        self.each([qv.lag_fit(p)[0] for p in pooled], self.t1["lag"], self.t1["tolerance"]["lag"], "lag")
        self.near(qv.lag_fit(qv.pool("lag", pooled))[0], self.t1["lagPooled"], self.t1["tolerance"]["lag"], "pooled lag")

    def test_tier2_curve_sums(self):
        pooled = [qv.pool("curve", blocks) for blocks in self.sent]
        values = [qv.curve_tracking(p) for p in pooled]
        tolerance = self.t2["tolerance"]
        self.each([p["s"] for p in pooled], self.t2["curveS"], tolerance["seconds"], "curve seconds")
        self.assertEqual([p["events"] for p in pooled], self.t2["events"])
        self.each([p["s04"] for p in pooled], self.t2["s04"], tolerance["seconds"], "delayed curve seconds")
        for name in ("below", "above", "below04", "above04"):
            self.each([v[name] for v in values], self.t2[name], tolerance["value"], name)

    def test_tier2_low_speed_line(self):
        everything = qv.pool("curve", [qv.pool("curve", blocks) for blocks in self.sent])
        values, tolerance, low = qv.curve_tracking(everything), self.t2["tolerance"], self.t2["low"]
        self.near(everything["lowS"], low["s"], tolerance["seconds"], "seconds")
        self.near(everything["lowLimitS"], low["limitS"], tolerance["seconds"], "seconds at the limit")
        self.near(values["lowBelow"], low["below"], tolerance["value"], "below")
        self.near(values["lowAbove"], low["above"], tolerance["value"], "above")

    def test_tier2_lag(self):
        pooled = [qv.pool("lag", [s["lag"] for s in run["summaries"] if "lag" in s]) for run in self.runs]
        tolerance = self.t2["tolerance"]
        self.each([qv.lag_fit(p)[0] for p in pooled], self.t2["lag"], tolerance["lag"], "lag")
        everything = qv.pool("lag", pooled)
        lag, gain = qv.lag_fit(everything)
        self.near(lag, self.t2["lagPooled"], tolerance["lag"], "pooled lag")
        self.near(gain, self.t2["gainPooled"], tolerance["lag"], "pooled gain")
        self.near(everything["s"], self.t2["lagS"], tolerance["seconds"], "seconds of lag data")


if __name__ == "__main__":
    unittest.main()
