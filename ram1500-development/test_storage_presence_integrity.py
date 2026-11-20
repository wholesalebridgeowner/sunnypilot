"""Synthetic fixtures only; never commit real route IDs or object keys."""
import unittest
from storage_presence_integrity import audit_inventory


def key(route, segment, kind, ext="zst"):
  return f"logs/namespace|{route}/{segment}/{kind}.{ext}"


def study(keys, **kwargs):
  return audit_inventory(keys, target_short_id="TESTAAA", expected_segments=3,
                         focus_segment=2, listing_complete=True, **kwargs)


class PresenceTests(unittest.TestCase):
  def test_absence_is_scoped_to_complete_bucket(self):
    result = study([key("OTHERID--fake", 2, "rlog")])
    self.assertEqual(result["status"], "not_found_in_scoped_listing")
    self.assertEqual(result["matchedRoutes"], 0)
    self.assertNotIn("qlog", result)

  def test_complete_and_extra_segments(self):
    result = study([key("TESTAAA--fake", i, k) for i in range(4)
                    for k in ("qlog", "rlog")])
    self.assertEqual(result["status"], "one_target_found")
    self.assertEqual(result["rlog"]["expectedSegmentsPresent"], 3)
    self.assertEqual(result["rlog"]["segmentsBeyondExpected"], 1)
    self.assertTrue(result["rlog"]["focusSegmentPresent"])

  def test_incomplete_listing_never_certifies_absence(self):
    result = audit_inventory([], target_short_id="TESTAAA", expected_segments=3,
                             focus_segment=2, listing_complete=False)
    self.assertEqual(result["status"], "incomplete_listing")

  def test_ambiguity_does_not_choose_arbitrary_route(self):
    result = study([key("TESTAAA--one", 2, "rlog"), key("TESTAAA--two", 2, "rlog")])
    self.assertEqual(result["status"], "ambiguous_target")
    self.assertNotIn("rlog", result)

  def test_unclassified_and_malformed_keys_not_matched(self):
    result = study([key("TESTAAA--fake", 2, "rlog"),
                    "logs/namespace|TESTAAA--fake/../../rlog.zst", "other/file", None])
    self.assertEqual(result["unclassifiedObjects"], 3)
    self.assertEqual(result["rlog"]["observedObjectsBySegment"], 1)

  def test_de_duplicate_extensions(self):
    result = study([key("TESTAAA--fake", 2, "rlog", ext) for ext in ("zst", "bz2")])
    self.assertEqual(result["rlog"]["observedObjectsBySegment"], 1)

  def test_private_route_data_never_returned(self):
    secret = "TESTAAA--PRIVATE-DO-NOT-PUBLISH"
    result = study([key(secret, 2, "rlog")])
    self.assertNotIn(secret, str(result))
    self.assertNotIn("namespace", str(result))

  def test_selector_validation(self):
    for selector in ("", "TESTAAA--secret/evil", "../", "  "):
      with self.subTest(selector=selector):
        with self.assertRaises(ValueError):
          audit_inventory([], target_short_id=selector, expected_segments=3,
                          focus_segment=2, listing_complete=True)

  def test_bad_types_cannot_mark_complete(self):
    for complete in (1, "yes", None):
      with self.subTest(complete=complete):
        with self.assertRaises(ValueError):
          audit_inventory([], target_short_id="TESTAAA", expected_segments=3,
                          focus_segment=2, listing_complete=complete)

  def test_excessive_segment_number_is_unclassified(self):
    result = study([key("TESTAAA--fake", 1000001, "rlog")])
    self.assertEqual(result["status"], "not_found_in_scoped_listing")
    self.assertEqual(result["unclassifiedObjects"], 1)


if __name__ == "__main__":
  unittest.main()
