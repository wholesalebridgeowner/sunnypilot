"""Helpers for measures that send a block of sums."""
import json
import unittest

import synth
from measure_api import Measure, closing_block, significant
from pipeline import Pipeline, PipelineConfig


class ClosingBlockTests(unittest.TestCase):
    def test_nothing_booked(self):
        self.assertIsNone(closing_block({}))

    def test_the_one_block_of_the_closing_window(self):
        block = {"n": 3}
        self.assertIs(closing_block({7: block}), block)

    def test_sums_in_two_windows_at_once_are_a_fault(self):
        with self.assertRaises(RuntimeError):
            closing_block({7: {"n": 3}, 8: {"n": 1}})

    def test_that_fault_switches_the_measure_off_and_names_it(self):
        class TwoWindows(Measure):
            name = "ay"

            def window_fields(self):
                return {"ay": closing_block({0: {"n": 1}, 1: {"n": 1}})}

        p = Pipeline(PipelineConfig(measures=(TwoWindows,)))
        out = synth.summaries(synth.run(p, synth.drive(synth.ticks(700))))
        self.assertTrue(all("ay" not in s for s in out))
        self.assertEqual([s["health"]["failed"] for s in out], [["ay"], ["ay"]])


class SignificantTests(unittest.TestCase):
    def test_five_significant_digits(self):
        self.assertEqual(significant(123456.789), 123460.0)
        self.assertEqual(significant(0.000123456789), 0.00012346)
        self.assertEqual(significant(3.14159265), 3.1416)
        self.assertEqual(significant(-0.0271828), -0.027183)
        self.assertEqual(significant(1.0), 1.0)

    def test_other_numbers_of_digits(self):
        self.assertEqual(significant(3.14159265, 3), 3.14)
        self.assertEqual(significant(123456.789, 2), 120000.0)

    def test_zero_stays_plain_zero(self):
        for value in (0.0, -0.0, 0):
            self.assertEqual(json.dumps(significant(value)), "0.0")

    def test_the_result_is_a_float_that_json_can_carry(self):
        value = significant(2.0 / 3.0)
        self.assertIsInstance(value, float)
        self.assertEqual(json.dumps(value), "0.66667")
        self.assertEqual(json.dumps(significant(6.6666666e-7)), "6.6667e-07")


if __name__ == "__main__":
    unittest.main()
