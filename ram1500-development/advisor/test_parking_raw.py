"""Experimental offline parking telemetry: raw fields only, no meaning invented."""
import unittest

from parking_raw import ADDR_E9, ADDR_F6, ParkingLog, decode, e9_checksum_ok


def e9(byte0=0xFF, byte1=0x00, byte3=0, counter=0, good=True):
    d = bytearray([byte0, byte1, 0x14, byte3, 0x14, 0x00, (counter & 15) << 4, 0])
    crc = 0xFF
    for value in d[:-1]:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1D if crc & 0x80 else 0)) & 0xFF
    d[7] = (crc ^ 0xFF) if good else (crc ^ 0xFF ^ 0x01)
    return bytes(d)


def f6(a=0xFF, b=0xFF, a_codes=None, b_codes=None):
    d = bytearray([0x04, a, a, b, b, a, b, 0x00])
    for i, c in (a_codes or {}).items():
        d[i] = c
    for i, c in (b_codes or {}).items():
        d[i] = c
    return bytes(d)


class DecodeTests(unittest.TestCase):
    def test_e9_checksum_and_single_bit_mutations(self):
        good = e9(byte0=0x40, byte1=0x0C, byte3=1, counter=7)
        self.assertTrue(e9_checksum_ok(good))
        for i in range(8):
            for bit in range(8):
                bad = bytearray(good)
                bad[i] ^= 1 << bit
                self.assertFalse(e9_checksum_ok(bytes(bad)), (i, bit))

    def test_e9_fields_stay_raw(self):
        r = decode(ADDR_E9, 0, e9(byte0=0x40, byte1=0x0C, byte3=1, counter=3))
        self.assertEqual((r["byte0"], r["byte1"], r["byte3"], r["counter"]), (0x40, 0x0C, 1, 3))
        self.assertTrue(r["checksum_ok"] and r["format_ok"])
        self.assertNotIn("distance", r)

    def test_f6_groups_and_unknown_codes(self):
        r = decode(ADDR_F6, 0, f6(a=0xF0))
        self.assertIs(r["group_a_active"], True)
        self.assertIs(r["group_b_active"], False)
        self.assertTrue(r["format_ok"])
        r = decode(ADDR_F6, 0, f6(a=0xF0, a_codes={2: 0xFF}))     # group A disagrees with itself
        self.assertIsNone(r["group_a_active"])
        self.assertFalse(r["format_ok"])
        r = decode(ADDR_F6, 0, f6(b=0x77))                        # a code never seen: kept, flagged
        self.assertEqual(r["codes"][3], 0x77)
        self.assertFalse(r["format_ok"])

    def test_only_bus0_eight_byte_frames_of_the_two_ids(self):
        self.assertIsNone(decode(ADDR_F6, 2, f6()))
        self.assertIsNone(decode(ADDR_E9, 0, e9()[:7]))
        self.assertIsNone(decode(0x123, 0, bytes(8)))


class ParkingLogTests(unittest.TestCase):
    def test_counts_with_gear_context_and_no_meaning(self):
        log = ParkingLog()
        log.car_state(0.00, "drive", 1.5)
        log.frame(0.00, 0, ADDR_F6, f6(a=0xF0))
        log.car_state(0.04, "reverse", 0.8)
        log.frame(0.05, 0, ADDR_F6, f6(a=0xF0, b=0x31))
        log.frame(0.06, 0, ADDR_E9, e9(byte0=0x50, byte1=0x0C, byte3=1, counter=1))
        log.frame(0.10, 0, ADDR_E9, e9(counter=2))
        log.frame(0.20, 0, ADDR_E9, e9(counter=4))                # skipped a count
        log.frame(0.30, 0, ADDR_E9, e9(counter=5, good=False))    # bad checksum
        log.frame(0.30, 2, ADDR_E9, e9(counter=5))                # bus-2 copy: counted apart
        rec = log.record()
        self.assertEqual((rec["experimental"], rec["meaningConfirmed"], rec["units"]), (True, False, None))
        self.assertEqual(rec["f6"]["frames"], 2)
        self.assertEqual(rec["f6"]["group_a_active"], 2)
        self.assertEqual(rec["f6"]["groupA_active_by_gear"], {"drive": 1, "reverse": 1})
        self.assertEqual(rec["f6"]["groupB_active_by_gear"], {"reverse": 1})
        self.assertEqual(rec["f6"]["groupA_active_vmax_ms"], 1.5)
        self.assertEqual(rec["f6"]["codes_by_byte"]["3"], {"FF": 1, "31": 1})
        self.assertEqual((rec["e9"]["frames"], rec["e9"]["valid_frames"]), (4, 3))
        self.assertEqual(rec["e9"]["checksum_fail"], 1)
        self.assertEqual((rec["e9"]["counter_pairs"], rec["e9"]["counter_skips"]), (2, 1))   # valid frames only
        self.assertEqual((rec["e9"]["byte0_set"], rec["e9"]["byte0_min"], rec["e9"]["byte0_max"]), (1, 0x50, 0x50))
        self.assertEqual(rec["e9"]["byte0_set_in_reverse"], 1)
        self.assertEqual(rec["e9"]["byte3_one_by_gear"], {"reverse": 1})
        self.assertEqual(rec["bus2_copies_ignored"], {hex(ADDR_E9): 1})

    def test_review_unknown_codes_stay_distinct(self):
        log = ParkingLog()
        log.frame(0.0, 0, ADDR_F6, f6(b=0x77))
        log.frame(0.1, 0, ADDR_F6, f6(b=0x78))
        rec = log.record()["f6"]
        self.assertEqual(rec["codes_by_byte"]["3"], {"77": 1, "78": 1})
        self.assertEqual(rec["unknown_code_values"], 6)           # bytes 3, 4, 6 of each frame
        self.assertEqual(rec["groupB_active_by_gear"], {})

    def test_f6_group_counts_exclude_bad_header_trailer_code_group_and_length(self):
        log = ParkingLog()
        log.car_state(0., "reverse", 1.)
        good = f6(a=0x31,b=0x32)
        for payload in (good, b"short", b"\x05" + good[1:], good[:-1] + b"\x01",
                        f6(a=0x77,b=0x32), f6(a=0x31,b=0x32,a_codes={2:0xff})):
            log.frame(.1, 0, ADDR_F6, payload)
        f = log.record()["f6"]
        self.assertEqual((f["frames"], f["format_mismatch"]), (6, 5))
        self.assertEqual(f["codes_by_byte"]["3"], {"32":5})
        self.assertEqual(f["codes_by_byte"]["2"], {"31":3, "77":1, "FF":1})
        self.assertEqual(f["groupA_active_by_gear"], {"reverse":1})
        self.assertEqual(f["groupB_active_by_gear"], {"reverse":1})

    def test_review_a_bad_checksum_frame_never_enters_values_or_continuity(self):
        log = ParkingLog()
        log.frame(0.00, 0, ADDR_E9, e9(byte0=80, byte1=0x0C, byte3=1, counter=1))
        log.frame(0.05, 0, ADDR_E9, e9(byte0=1, byte1=0x0C, byte3=1, counter=2, good=False))
        log.frame(0.10, 0, ADDR_E9, e9(byte0=81, byte1=0x0C, byte3=1, counter=3))
        e = log.record()["e9"]
        self.assertEqual((e["byte0_set"], e["byte0_min"], e["byte0_max"]), (2, 80, 81))
        self.assertEqual((e.get("counter_pairs", 0), e.get("counter_skips", 0)), (0, 0))  # continuity reset

    def test_review_old_or_invalid_context_gives_unknown_gear(self):
        log = ParkingLog()
        log.car_state(0.0, "reverse", 1.0)
        log.frame(600.0, 0, ADDR_E9, e9(byte0=0x40, byte1=0x0C, byte3=1))
        log.car_state(601.0, "reverse", 1.0, valid=False)
        log.car_state(602.0, "reverse", float("nan"))
        log.frame(602.1, 0, ADDR_E9, e9(byte0=0x41, byte1=0x0C, byte3=1, counter=1))
        rec = log.record()
        self.assertEqual(rec["e9"].get("byte0_set_in_reverse", 0), 0)
        self.assertEqual(rec["e9"]["byte0_set_in_unknown"], 2)
        self.assertEqual(rec["invalid_context"], 2)

    def test_invalid_state_expires_previous_gear_at_its_own_timestamp(self):
        log = ParkingLog()
        log.car_state(10., "reverse", 1.)
        log.car_state(10.1, "reverse", 1., valid=False)
        log.frame(10.05, 0, ADDR_E9, e9(byte0=80))
        log.frame(10.15, 0, ADDR_E9, e9(byte0=81))
        log.car_state(None, None, None, valid=False)
        log.car_state(10., "reverse", 1.)  # old input cannot restore the cleared history
        log.frame(10.2, 0, ADDR_E9, e9(byte0=82))
        record = log.record()["e9"]
        self.assertEqual(record["byte0_set_in_reverse"], 1)
        self.assertEqual(record["byte0_set_in_unknown"], 2)

    def test_short_e9_resets_counter_history(self):
        log = ParkingLog()
        log.frame(0., 0, ADDR_E9, e9(counter=1))
        log.frame(0.1, 0, ADDR_E9, b"")
        log.frame(0.2, 0, ADDR_E9, e9(counter=3))
        record = log.record()["e9"]
        self.assertEqual((record["frames"], record["valid_frames"]), (3, 2))
        self.assertEqual(record["format_mismatch"], 1)
        self.assertEqual(record.get("counter_pairs", 0), 0)

    def test_e9_out_of_order_or_duplicate_frame_breaks_pairing_until_reseeded(self):
        for anomalous_time in (1., .95):
            with self.subTest(anomalous_time=anomalous_time):
                log = ParkingLog()
                for stamp, counter in ((1.,1), (anomalous_time,9), (.99,8), (1.1,2), (1.2,3)):
                    log.frame(stamp, 0, ADDR_E9, e9(byte0=10,counter=counter))
                r = log.record()["e9"]
                self.assertEqual((r["valid_frames"], r["byte0_set"]), (5, 5))
                self.assertEqual((r["counter_pairs"], r["counter_skips"]), (1, 0))

    def test_ff_is_never_reported_as_clear_or_failed(self):
        log = ParkingLog()
        log.car_state(0.0, "drive", 25.0)
        log.frame(0.0, 0, ADDR_F6, f6())
        text = str(log.record()).lower()
        for word in ("clear", "fail", "fault", "distance", "obstacle", "alert"):
            self.assertNotIn(word, text)


class NotOnTheComma(unittest.TestCase):
    def test_the_report_is_offline_only_and_the_live_path_is_off(self):
        import parking_live
        from install_agent import AGENT_PAYLOAD
        self.assertNotIn("parking_report.py", AGENT_PAYLOAD)
        self.assertIn("parking_raw.py", AGENT_PAYLOAD)          # the decode library, used by the switched-off live path
        self.assertIs(parking_live.PARKING_LIVE_ENABLED, False)


if __name__ == "__main__":
    unittest.main()
