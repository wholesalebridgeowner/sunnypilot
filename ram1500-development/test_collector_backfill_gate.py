"""Synthetic, non-identifying fixtures. No real routes or vehicle content."""
import unittest
from datetime import datetime, timedelta, timezone
from collector_backfill_gate import audit_historical_segment

NOW = datetime(2026, 2, 8, 12, 0, tzinfo=timezone.utc)


def check(age_hours=80, rank=None, **flags):
    return audit_historical_segment(
        now=NOW, route_end=NOW - timedelta(hours=age_hours),
        recent_route_rank=rank, **flags,
    )


class CollectorBackfillGateTests(unittest.TestCase):
    def test_old_route_outside_scanner_even_with_upload_accepted(self):
        r = check(upload_requested=True)
        self.assertEqual(r['discoveryGate'], 'outside_collector_lookback')
        self.assertEqual(r['rlogEvidenceGate'], 'upload_request_only_not_file')
        self.assertFalse(r['mayAnalyzeHighResolution'])

    def test_exact_72_hour_boundary_requires_rank(self):
        r = check(age_hours=72)
        self.assertEqual(r['discoveryGate'], 'inside_lookback_rank_unverified')

    def test_top_four_selection_zero_based(self):
        self.assertEqual(check(10, rank=3)['discoveryGate'], 'eligible_for_collector_route_selection_only')
        self.assertEqual(check(10, rank=4)['discoveryGate'], 'inside_lookback_but_outside_top_routes')

    def test_unknown_rank_is_not_assumed_good(self):
        self.assertEqual(check(1)['discoveryGate'], 'inside_lookback_rank_unverified')

    def test_cloud_url_not_local_private_object(self):
        r = check(cloud_rlog_url=True)
        self.assertEqual(r['rlogEvidenceGate'], 'cloud_link_seen_private_object_unverified')
        self.assertFalse(r['mayAnalyzeHighResolution'])

    def test_bucket_presence_not_decompression(self):
        r = check(private_rlog_object=True)
        self.assertEqual(r['rlogEvidenceGate'], 'private_object_present_integrity_unverified')
        self.assertFalse(r['mayAnalyzeHighResolution'])

    def test_fail_closed_without_source_pin_and_segment_identity(self):
        r = check(private_rlog_object=True, decompress_verified=True)
        self.assertFalse(r['mayAnalyzeHighResolution'])
        r = check(private_rlog_object=True, decompress_verified=True, pinned_schema_verified=True)
        self.assertFalse(r['mayAnalyzeHighResolution'])

    def test_only_all_provenance_checks_make_segment_eligible(self):
        r = check(private_rlog_object=True, decompress_verified=True,
                  pinned_schema_verified=True, segment_identity_verified=True)
        self.assertTrue(r['mayAnalyzeHighResolution'])
        self.assertEqual(r['rlogEvidenceGate'], 'high_resolution_segment_evidence_verified')
        self.assertEqual(r['discoveryGate'], 'outside_collector_lookback')

    def test_claiming_decoded_data_without_object_is_contradiction(self):
        r = check(decompress_verified=True, pinned_schema_verified=True,
                  segment_identity_verified=True)
        self.assertEqual(r['rlogEvidenceGate'], 'inconsistent_provenance_require_review')
        self.assertFalse(r['mayAnalyzeHighResolution'])

    def test_validation_rejects_naive_datetimes(self):
        with self.assertRaises(ValueError):
            audit_historical_segment(now=NOW.replace(tzinfo=None), route_end=NOW)
        with self.assertRaises(ValueError):
            audit_historical_segment(now=NOW, route_end=NOW.replace(tzinfo=None))

    def test_validation_rejects_invalid_rank_and_flags(self):
        for rank in (-1, True, 2.2):
            with self.subTest(rank=rank), self.assertRaises(ValueError):
                check(rank=rank)
        with self.assertRaises(ValueError):
            check(private_rlog_object=1)

    def test_validation_rejects_future_end(self):
        with self.assertRaises(ValueError):
            check(age_hours=-1)

    def test_privacy_aggregate_only(self):
        r = check(upload_requested=True)
        text = str(r)
        self.assertNotIn('2026', text)
        self.assertNotIn('credential', text)


if __name__ == '__main__':
    unittest.main()
