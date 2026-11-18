"""Synthetic, no-network, no-vehicle tests of exact-source OEM status provenance."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("oem_eps_dbc_provenance", HERE / "oem_eps_dbc_provenance.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

COMMON = b"""BO_ 258 STEERING: 8 XXX
 SG_ AUTO_PARK_HAS_CONTROL_2 : 30|1@0+ (1,0) [0|1] "" XXX
BO_ 544 EPS_2: 8 XXX
 SG_ LKAS_STATE : 23|4@0+ (1,0) [0|15] "" XXX
 SG_ TORQUE_OVERLAY_STATUS : 6|4@0+ (1,0) [0|15] "" XXX
 SG_ AUTO_PARK_HAS_CONTROL_2 : 51|1@0+ (1,0) [0|1] "" XXX
BO_ 571 CRUISE_BUTTONS: 3 XXX
 SG_ LKAS_STATE : 10|4@0+ (1,0) [0|15] "" XXX
"""
MAPPING = b"""chrysler_to_ram = {
  "_stellantis_common_ram_dt_generated.dbc": {258: 35, 544: 49, 571: 177},
  "_stellantis_common_ram_hd_generated.dbc": {571: 570},
}
"""


def checked(common=COMMON, generator=MAPPING):
  return m.audit(common, generator, expected_common=m.git_blob_sha(common),
                 expected_generator=m.git_blob_sha(generator))


class ProvenanceTests(unittest.TestCase):
  def test_success_requires_correct_source_frame_and_mapping(self):
    result = checked()
    self.assertEqual(result["sourceAddressDecimal"], 544)
    self.assertEqual(result["ramDtAddressHex"], "0x31")
    self.assertEqual(result["statusSignals"], m.EXPECTED)
    self.assertEqual(result["recordedBuild"], m.DRIVE_COMMIT)
    self.assertIn("CAN-C visibility at the Comma harness", result["notEstablished"])
    self.assertNotIn("sender", result["evidenceScope"].lower())

  def test_pinned_sha_constants_match_independently_retrieved_github_blobs(self):
    self.assertEqual(m.COMMON_BLOB, "df913561de2fad009ba27288cd8d5ec00a36ca02")
    self.assertEqual(m.MAPPING_BLOB, "fa408f1315f34feffad23c4637c7df905b5f8d7c")
    self.assertEqual(m.git_blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

  def test_wrong_source_hash_fails_even_if_layout_matches(self):
    with self.assertRaisesRegex(m.SourceAuditError, "SHA differs"):
      m.audit(COMMON, MAPPING)
    with self.assertRaisesRegex(m.SourceAuditError, "SHA differs"):
      m.audit(COMMON, MAPPING, expected_common=m.git_blob_sha(COMMON))

  def test_wrong_signal_location_fails_even_with_matching_fixture_sha(self):
    changed = COMMON.replace(b"51|1@0+", b"50|1@0+")
    with self.assertRaisesRegex(m.SourceAuditError, "AUTO_PARK_HAS_CONTROL_2"):
      checked(changed)

  def test_missing_or_duplicate_source_frames_are_rejected(self):
    with self.assertRaisesRegex(m.SourceAuditError, "expected one"):
      checked(COMMON.replace(b"BO_ 544 EPS_2", b"BO_ 545 EPS_2"))
    with self.assertRaisesRegex(m.SourceAuditError, "expected one"):
      checked(COMMON + COMMON)

  def test_duplicate_signal_in_eps_frame_is_rejected(self):
    extra = b" SG_ AUTO_PARK_HAS_CONTROL_2 : 51|1@0+ (1,0) [0|1] \"\" XXX\n"
    changed = COMMON.replace(b"BO_ 571", extra + b"BO_ 571")
    with self.assertRaisesRegex(m.SourceAuditError, "duplicate"):
      checked(changed)

  def test_wrong_mapping_and_address_collision_fail(self):
    changed = MAPPING.replace(b"544: 49", b"544: 50")
    with self.assertRaisesRegex(m.SourceAuditError, "not mapped"):
      checked(COMMON, changed)
    collide = MAPPING.replace(b"258: 35", b"258: 49")
    with self.assertRaisesRegex(m.SourceAuditError, "multiple source"):
      checked(COMMON, collide)

  def test_no_executable_mapping_code_is_evaluated(self):
    bad = b'chrysler_to_ram = __import__("os").system("echo THIS_MUST_NEVER_EXECUTE")\n'
    with self.assertRaisesRegex(m.SourceAuditError, "statically resolve"):
      checked(COMMON, bad)

  def test_require_correct_ram_dt_generated_target(self):
    with self.assertRaisesRegex(m.SourceAuditError, "statically resolve"):
      checked(COMMON, MAPPING.replace(b"_stellantis_common_ram_dt_generated.dbc",
                                      b"_stellantis_common_unknown_generated.dbc"))

  def test_missing_status_field_in_correct_source_frame_fails(self):
    moved = COMMON.replace(b" SG_ LKAS_STATE : 23|4@0+ (1,0) [0|15] \"\" XXX\n", b"")
    with self.assertRaisesRegex(m.SourceAuditError, "LKAS_STATE"):
      checked(moved)


if __name__ == "__main__":
  unittest.main()
