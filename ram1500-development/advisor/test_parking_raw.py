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
        log.car_state("drive", 1.5)
        log.frame(0.00, 0, ADDR_F6, f6(a=0xF0))
        log.car_state("reverse", 0.8)
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
        self.assertEqual(rec["e9"]["frames"], 4)
        self.assertEqual(rec["e9"]["checksum_fail"], 1)
        self.assertEqual((rec["e9"]["counter_pairs"], rec["e9"]["counter_skips"]), (3, 1))
        self.assertEqual((rec["e9"]["byte0_set"], rec["e9"]["byte0_min"], rec["e9"]["byte0_max"]), (1, 0x50, 0x50))
        self.assertEqual(rec["e9"]["byte0_set_in_reverse"], 1)
        self.assertEqual(rec["e9"]["byte3_one_by_gear"], {"reverse": 1})
        self.assertEqual(rec["bus2_copies_ignored"], {hex(ADDR_E9): 1})

    def test_ff_is_never_reported_as_clear_or_failed(self):
        log = ParkingLog()
        log.car_state("drive", 25.0)
        log.frame(0.0, 0, ADDR_F6, f6())
        text = str(log.record()).lower()
        for word in ("clear", "fail", "fault", "distance", "obstacle", "alert"):
            self.assertNotIn(word, text)


class NotOnTheComma(unittest.TestCase):
    def test_parking_modules_are_offline_only(self):
        from install_agent import AGENT_PAYLOAD
        self.assertNotIn("parking_raw.py", AGENT_PAYLOAD)
        self.assertNotIn("parking_report.py", AGENT_PAYLOAD)


if __name__ == "__main__":
    unittest.main()
