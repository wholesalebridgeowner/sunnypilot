"""Synthetic-only SHA-256 commitments; no route identifiers or raw vehicle data."""
import unittest

from private_replay_fingerprints import SOURCE, hash_route_inputs, hash_eligible_window


class FingerprintTests(unittest.TestCase):
    def raw(self, *, a=b"synthetic-A", b=b"synthetic-B"):
        return hash_route_inputs([(27, [a]), (28, [b])])

    def test_chunk_boundaries_have_no_effect(self):
        one = self.raw()
        many = hash_route_inputs([(27, [b"syn", b"thetic-", b"A"]),
                                  (28, [b"synthe", b"tic-B"])])
        self.assertEqual(one, many)
        self.assertEqual(len(one), 64)

    def test_input_change_or_segment_framing_changes_digest(self):
        base = self.raw()
        self.assertNotEqual(base, self.raw(a=b"synthetic-A!"))
        self.assertNotEqual(base, hash_route_inputs([(27, [b"synthetic-Asynthetic-B"])]))
        self.assertNotEqual(base, hash_route_inputs([(27, [b"synthetic-"]),
                                                      (28, [b"Asynthetic-B"])]))

    def test_wrong_source_unordered_duplicate_empty_and_nonbinary_fail(self):
        bad = [
            ([(27, [b"a"])], "incorrect-source"),
            ([(28, [b"b"]), (27, [b"a"])], SOURCE),
            ([(27, [b"a"]), (27, [b"a"])], SOURCE),
            ([], SOURCE),
            ([(27, [b""])], SOURCE),
            ([(27, ["text"])], SOURCE),
            ([(True, [b"a"])], SOURCE),
        ]
        for segments, source in bad:
            with self.subTest(segments=segments):
                with self.assertRaises(ValueError):
                    hash_route_inputs(segments, source_commit=source)

    def test_window_identity_depends_on_exact_anchors_and_route(self):
        raw = self.raw()
        markers = [(27, 3, 100_000_000), (27, 4, 110_000_000),
                   (28, 1, 150_000_000)]
        base = hash_eligible_window(raw, markers)
        self.assertEqual(base, hash_eligible_window(raw, iter(markers)))
        self.assertNotEqual(base, hash_eligible_window(raw, [(27, 3, 100_000_000),
                                                               (27, 5, 110_000_000),
                                                               (28, 1, 150_000_000)]))
        self.assertNotEqual(base, hash_eligible_window(self.raw(a=b"changed"), markers))
        self.assertNotEqual(base, raw)

    def test_window_rejects_duplicates_and_noncanonical_digest(self):
        good = [(27, 3, 100), (27, 4, 110), (28, 1, 150)]
        bad_markers = [[], [(27, 1, 10), (27, 1, 10)],
                       [(27, 2, 10), (27, 3, 9)],
                       [(28, 1, 10), (27, 2, 11)],
                       [(27, 2, True)], [(27, -1, 10)]]
        for bad in bad_markers:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                hash_eligible_window(self.raw(), bad)
        for bad in ("A" * 64, "z" * 64, self.raw()[:-1], True):
            with self.subTest(digest=repr(bad)), self.assertRaises(ValueError):
                hash_eligible_window(bad, good)

    def test_identical_input_and_frozen_selection_pair(self):
        inputs = hash_route_inputs([(0, [b"only-synthetic-evidence"])])
        selected = [(0, 2, 1000), (0, 3, 2000), (0, 8, 6000)]
        baseline = hash_eligible_window(inputs, selected)
        candidate = hash_eligible_window(inputs, list(selected))
        self.assertEqual(baseline, candidate)


if __name__ == "__main__":
    unittest.main()
