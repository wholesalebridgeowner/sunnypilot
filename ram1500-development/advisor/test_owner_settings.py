"""Pinned owner-menu domains, stored types, and one-direction protective settings."""
import json
import unittest

from owner_settings import (
    BLOCKED_REASONS, FLOAT_BOUNDS, INT_VALUES, OWNER_BOOL_KEYS, OWNER_KEYS,
    OWNER_NATIVE_LOCKS, PROTECTIVE_VALUES, TEXT_VALUES, parse_owner_value, validate_owner_change,
)
from settings_guard import GuardError, validate_change


class OwnerSettingsTests(unittest.TestCase):
    def test_catalogs_are_disjoint_and_cover_reviewed_inventory(self):
        groups = [OWNER_BOOL_KEYS, INT_VALUES, FLOAT_BOUNDS, TEXT_VALUES, BLOCKED_REASONS]
        self.assertEqual(sum(map(len, groups)), 94)
        self.assertEqual(len(set().union(*groups)), 94)
        self.assertLessEqual(PROTECTIVE_VALUES.keys(), OWNER_KEYS)
        self.assertEqual(OWNER_NATIVE_LOCKS, {"RecordFront": "RecordFrontLock"})

    def test_all_permitted_values_round_trip_through_cli_and_json(self):
        domains = {key: (True, False) for key in OWNER_BOOL_KEYS}
        domains.update(INT_VALUES)
        domains.update(TEXT_VALUES)
        for key, (lo, hi) in FLOAT_BOUNDS.items():
            domains[key] = (n / 100 for n in range(round(lo * 100), round(hi * 100) + 1))
        for key, values in domains.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    if key in PROTECTIVE_VALUES and value != PROTECTIVE_VALUES[key][0]:
                        with self.assertRaisesRegex(GuardError, "protected_setting"):
                            validate_owner_change({key: value})
                        with self.assertRaisesRegex(GuardError, "protected_setting"):
                            parse_owner_value(key, str(value))
                        continue
                    parsed = parse_owner_value(key, str(value))
                    self.assertIs(type(parsed), type(value))
                    self.assertEqual(parsed, value)
                    validate_owner_change(json.loads(json.dumps({key: parsed})))

    def test_native_widget_values_and_automatic_sentinels(self):
        change = {"AutoLaneChangeTimer": -1, "Brightness": 1, "MaxTimeOffroad": 0,
                  "CustomAccLongPressIncrement": 10, "BlinkerPauseLateralControl": 1,
                  "BlinkerMinLateralControlSpeed": 255, "LaneTurnValue": 18.38,
                  "LanguageSetting": "main_zh-CHT", "LagdToggleDelay": 0.05}
        validate_owner_change(change)
        self.assertEqual(parse_owner_value("Brightness", "0"), 0)
        self.assertEqual(parse_owner_value("LanguageSetting", "main_pt-BR"), "main_pt-BR")
        self.assertIs(parse_owner_value("BlindSpot", "0"), False)
        self.assertEqual(parse_owner_value("LagdToggleDelay", ".500000"), 0.5)

    def test_reject_bad_types_and_gaps_between_choices(self):
        for key, values in {
            "BlindSpot": [0, 1, "true", [], None],
            "AutoLaneChangeTimer": [True, 0.0, 6, -2, "1"],
            "BlinkerPauseLateralControl": [True, 2],
            "BlinkerMinLateralControlSpeed": [256, 13],
            "Brightness": [2, 99], "CustomAccLongPressIncrement": [2, 3],
            "InteractivityTimeout": [1, 130], "MaxTimeOffroad": [1, 1500],
            "LaneTurnValue": [5, 4.99, 20.01, 19.011],
            "LagdToggleDelay": [True, 0.201, 0.501, 0.049, float("nan"),
                                float("inf"), -float("inf"), 10 ** 1000],
            "LanguageSetting": [None, {}, "en", "main_pt-br"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(GuardError):
                    validate_owner_change({key: value})

    def test_cli_rejects_invalid_input_without_rounding_into_range(self):
        for key, samples in {
            "AutoLaneChangeTimer": ["2.5", "true", " 2", "１"],
            "LagdToggleDelay": ["0.5000000000000000001", "NaN", "0_1", "1e100000",
                                "0.19999999999999999999999999", "", "9" * 256],
            "BlindSpot": ["maybe", "00", 1, None], "LanguageSetting": ["main_ja "],
        }.items():
            for raw in samples:
                with self.subTest(key=key, raw=raw), self.assertRaises(GuardError):
                    parse_owner_value(key, raw)

    def test_all_exclusions_return_their_specific_reason(self):
        for key, reason in BLOCKED_REASONS.items():
            with self.subTest(key=key), self.assertRaisesRegex(GuardError, reason):
                validate_owner_change({"Brightness": 10, key: True})
        for key in ("IsMetric", "AlwaysOnDM", "SteerMax", "RecordFrontLock", "../IsOffroad"):
            with self.subTest(key=key), self.assertRaisesRegex(GuardError, "unknown_key"):
                parse_owner_value(key, "1")
        for change in (None, [], {}, {2: 0}):
            with self.subTest(change=change), self.assertRaises(GuardError):
                validate_owner_change(change)

    def test_no_weakening_mads_brake_disengagement(self):
        validate_owner_change({"MadsSteeringMode": 2, "DisengageOnAccelerator": True,
                               "QuietMode": False})
        for value in (0, 1, True, 2.0, "2"):
            with self.subTest(value=value), self.assertRaises(GuardError):
                validate_owner_change({"MadsSteeringMode": value})
        for key, value in (("DisengageOnAccelerator", False), ("QuietMode", True)):
            with self.subTest(key=key), self.assertRaises(GuardError):
                validate_owner_change({key: value})

    def test_owner_changes_remain_outside_suggestion_guard(self):
        with self.assertRaises(GuardError):
            validate_change({"AutoLaneChangeTimer": -1}, mode="nightly",
                            learned_delay_s=0.25, base_actuator_delay_s=0.1)


if __name__ == "__main__":
    unittest.main()
