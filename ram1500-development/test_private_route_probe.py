"""No credentials, live S3, Comma device, or network required."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "ram_private_probe", Path(__file__).with_name("private_route_probe.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixtures(route="garage|000003d8--sample", segments=(0, 29, 30, 31), kind="rlog"):
    return [{"Key": f"logs/{route}/{segment}/{kind}.zst", "Size": 1_000_000}
            for segment in segments]


class PrivateProbeTests(unittest.TestCase):
    def test_selects_exact_read_only_rlog_segments(self):
        other = fixtures(route="other|000003d7--previous")
        objs = fixtures() + other + fixtures(kind="qlog")
        keys = mod.select_keys(objs, route_prefix="000003d8")
        self.assertEqual(len(keys), 4)
        self.assertEqual([int(k.split("/")[-2]) for k in keys], [0, 29, 30, 31])
        self.assertTrue(all(k.endswith("/rlog.zst") for k in keys))

    def test_rejects_partial_window(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            mod.select_keys(fixtures(segments=(0, 29, 30)), route_prefix="000003d8")

    def test_rejects_ambiguous_route_without_leaking_paths(self):
        objs = fixtures() + fixtures(route="other|000003d8--different")
        with self.assertRaises(ValueError) as error:
            mod.select_keys(objs, route_prefix="000003d8")
        self.assertNotIn("garage", str(error.exception))
        self.assertNotIn("different", str(error.exception))

    def test_rejects_absent_invalid_prefix_or_log_kind(self):
        for prefix in ("nothex", "000003d", "../path", "000003d800"):
            with self.assertRaises(ValueError):
                mod.select_keys(fixtures(), route_prefix=prefix)
        with self.assertRaises(ValueError):
            mod.select_keys(fixtures(), route_prefix="000003d9")
        with self.assertRaises(ValueError):
            mod.select_keys(fixtures(), route_prefix="000003d8", kind="other")

    def test_rejects_zero_and_oversized_files(self):
        for size in (0, mod.MAX_COMPRESSED_BYTES + 1):
            obj = fixtures()
            obj[0]["Size"] = size
            with self.assertRaisesRegex(ValueError, "oversized"):
                mod.select_keys(obj, route_prefix="000003d8")

    def test_rejects_invalid_requested_segments(self):
        for selected in ((0, 0), (-1, 0), (True, 29), (0, "29")):
            with self.assertRaises(ValueError):
                mod.select_keys(fixtures(), route_prefix="000003d8", segments=selected)

    def test_summary_only_includes_nonidentifying_aggregates(self):
        result = {"summary": {"focusSeconds": 1803.0, "windowSeconds": 40.0,
                              "messageCount": 100}, "rows": [1, 2, 3]}
        steering = {
            "groups": {
                "controllerSaturated": {"samples": 2},
                "allControllerStateSamples": {"nearReportedCommandLimitSamples": 3},
            },
            "eventSampleCount": 1, "eventEpisodeCount": 1,
            "controllerSamples": 5, "sourceMatchedWithinAgeSamples": {"carState": 4},
        }
        report = mod.summarize(result, steering,
                               {"inputRows": 3, "steerSaturatedEpisodeCount": 1})
        self.assertEqual(report["nearCommandLimitControllerSamplesInWindow"], 3)
        self.assertEqual(report["quality"]["eventEpisodes"], 1)
        self.assertNotIn("VIN", str(report))
        self.assertNotIn("routeKey", str(report))
        self.assertNotIn("monoTimeNs", str(report))


if __name__ == "__main__":
    unittest.main()
