"""Synthetic-only route integrity tests: no real log data and no device."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("route_integrity", Path(__file__).with_name("route_integrity.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestRouteIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "dummy-route"

    def file(self, n, *, name="qlog.zst", contents=b"some-data", root=None):
        p = (root or self.root) / str(n) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contents)
        return p

    def test_known_51_segment_route_passes(self):
        files = [self.file(i) for i in range(51)]
        report = module.inspect(list(reversed(files)), expected_segments=51, kind="qlog")
        self.assertTrue(report["expectedRangeFullyPresent"])
        self.assertEqual(report["observedSegments"], list(range(51)))
        self.assertEqual(report["bytes"], 51 * len(b"some-data"))
        self.assertNotIn(str(self.root), repr(report))

    def test_gap_and_absent_final_segment_are_different_failures(self):
        files = [self.file(n) for n in [0, 1, 3]]
        r = module.inspect(files, expected_segments=5)
        self.assertEqual(r["missingExpectedSegments"], [2, 4])
        self.assertEqual(r["missingSegmentsThroughObservedMaximum"], [2])
        self.assertFalse(r["expectedRangeFullyPresent"])
        self.assertIn("segmentGap", r["errors"])

    def test_tail_unknown_without_expected_count(self):
        r = module.inspect([self.file(i) for i in range(3)])
        self.assertTrue(r["contiguousFromZero"])
        self.assertFalse(r["expectedRangeFullyPresent"])
        self.assertIsNone(r["missingExpectedSegments"])

    def test_mixed_types_and_duplicate_index_rejected(self):
        r = module.inspect([self.file(0), self.file(0, name="rlog.zst")], expected_segments=1)
        self.assertIn("mixedQlogRlog", r["errors"])
        self.assertIn("duplicateSegment", r["errors"])

    def test_different_route_paths_rejected(self):
        r = module.inspect([self.file(0), self.file(1, root=Path(self.tmp.name) / "other")], expected_segments=2)
        self.assertIn("multipleRouteDirectories", r["errors"])

    def test_missing_empty_wrong_kind_and_bad_filename(self):
        empty = self.file(0, contents=b"")
        notthere = self.root / "1" / "qlog.zst"
        wrong = self.file(2, name="rlog.zst")
        bad = self.file(3, name="camera.bin")
        r = module.inspect([empty, notthere, wrong, bad], expected_segments=2, kind="qlog")
        for err in ("emptyLogFile", "missingOrUnreadableFile", "wrongLogKind", "unrecognizedLogFilename"):
            self.assertIn(err, r["errors"])
        self.assertFalse(r["expectedRangeFullyPresent"])

    def test_generator_counts_all_inputs(self):
        files = (self.file(i) for i in range(2))
        r = module.inspect(files, expected_segments=2)
        self.assertTrue(r["expectedRangeFullyPresent"])
        self.assertEqual(r["inputFiles"], 2)

    def test_invalid_parameter(self):
        with self.assertRaises(ValueError):
            module.inspect([], expected_segments=0)
        with self.assertRaises(ValueError):
            module.inspect([], expected_segments=True)
        with self.assertRaises(ValueError):
            module.inspect([], kind="both")


if __name__ == "__main__":
    unittest.main()
