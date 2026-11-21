"""The settings snapshot of a drive: what is read, how a failed read shows, and what counts as a change."""
import json
import unittest

from device_params import DeviceParams
from settings_snapshot import (KEYS, WATCHED, changed_keys, model_bundle_id, nnlc_model_token, read_settings,
                               reread_watched)
from test_device_params import FakeParams

BUNDLE = {"index": 3, "internalName": "example-model", "displayName": "Example Model", "generation": 4,
          "environment": "release", "runner": "snpe", "is20hz": False, "ref": "0123abcd", "status": "downloaded",
          "minimumSelectorVersion": 2, "models": [{"type": "vision", "artifact": {"fileName": "a.bin"}}],
          "overrides": []}
VALUES = {"LagdToggle": True, "LagdToggleDelay": 0.2, "NeuralNetworkLateralControl": False,
          "AutoLaneChangeTimer": 2, "ModelManager_ActiveBundle": BUNDLE,
          "GitCommit": "0123456789abcdef0123456789abcdef01234567"}


def params(values=VALUES, broken=(), **changes):
    return DeviceParams(FakeParams({**values, **changes}, broken))


class SnapshotTests(unittest.TestCase):
    def test_the_six_fields(self):
        snapshot = read_settings(params())
        self.assertEqual(tuple(snapshot), KEYS)
        self.assertEqual(KEYS, ("lagdToggle", "lagdToggleDelayS", "nnlc", "laneChangeTimer", "modelBundleId", "build"))
        self.assertEqual({k: v for k, v in snapshot.items() if k != "modelBundleId"},
                         {"lagdToggle": True, "lagdToggleDelayS": 0.2, "nnlc": False, "laneChangeTimer": 2,
                          "build": "0123456789ab"})
        self.assertRegex(snapshot["modelBundleId"], "^[0-9a-f]{12}$")

    def test_every_lane_change_timer_value_is_recorded_as_it_is(self):
        for value in (-1, 0, 1, 2, 3, 4, 5):
            with self.subTest(value=value):
                self.assertEqual(read_settings(params(AutoLaneChangeTimer=value))["laneChangeTimer"], value)

    def test_a_read_that_raises_gives_null_never_a_default(self):
        for key, field in (("LagdToggle", "lagdToggle"), ("LagdToggleDelay", "lagdToggleDelayS"),
                           ("NeuralNetworkLateralControl", "nnlc"), ("AutoLaneChangeTimer", "laneChangeTimer"),
                           ("GitCommit", "build")):
            with self.subTest(key=key):
                snapshot = read_settings(params(broken={key}))
                self.assertIsNone(snapshot[field])
                self.assertEqual(sum(value is None for value in snapshot.values()), 1)     # the others are read

    def test_a_value_of_the_wrong_type_gives_null(self):
        snapshot = read_settings(params(LagdToggle=1, LagdToggleDelay="0.2", NeuralNetworkLateralControl=None,
                                        AutoLaneChangeTimer=True, GitCommit=b"0123456789abcdef"))
        self.assertEqual({k: v for k, v in snapshot.items() if k != "modelBundleId"},
                         {"lagdToggle": None, "lagdToggleDelayS": None, "nnlc": None, "laneChangeTimer": None,
                          "build": None})

    def test_the_build_is_twelve_hex_characters_or_null(self):
        for value, sent in (("1f7233cb983f3c13fdbba47a86f8fbc796ad9211", "1f7233cb983f"), ("1F7233CB983F3C", "1f7233cb983f"),
                            ("1f7233cb983f", "1f7233cb983f"), ("1f7233cb983", None), ("", None),
                            ("release-branch-name", None), ("1f7233cb983g3c13", None)):
            with self.subTest(value=value):
                self.assertEqual(read_settings(params(GitCommit=value))["build"], sent)

    def test_the_snapshot_is_made_of_plain_values(self):
        json.dumps(read_settings(params()), allow_nan=False)


class ModelBundleTests(unittest.TestCase):
    def test_twelve_hex_characters_of_the_identity_fields(self):
        self.assertRegex(model_bundle_id(BUNDLE), "^[0-9a-f]{12}$")
        self.assertEqual(model_bundle_id(BUNDLE), model_bundle_id(dict(BUNDLE)))

    def test_the_same_bundle_with_reordered_keys_has_the_same_id(self):
        reordered = dict(reversed(list(BUNDLE.items())))
        self.assertEqual(list(reordered)[0], "overrides")
        self.assertEqual(model_bundle_id(reordered), model_bundle_id(BUNDLE))

    def test_fields_that_are_not_identity_do_not_change_the_id(self):
        for key, value in (("status", "downloading"), ("index", 9), ("displayName", "Other Name"),
                           ("environment", "staging"), ("minimumSelectorVersion", 3), ("models", []),
                           ("overrides", [{"key": "lat", "value": "0.1"}])):
            with self.subTest(key=key):
                self.assertEqual(model_bundle_id({**BUNDLE, key: value}), model_bundle_id(BUNDLE))

    def test_each_identity_field_changes_the_id(self):
        for key, value in (("internalName", "another-model"), ("generation", 5), ("ref", "ffff0000"),
                           ("runner", "tinygrad"), ("is20hz", True)):
            with self.subTest(key=key):
                self.assertNotEqual(model_bundle_id({**BUNDLE, key: value}), model_bundle_id(BUNDLE))

    def test_header_bytes_and_the_parsed_object_give_the_same_id(self):
        raw = json.dumps(BUNDLE).encode("utf-8")
        self.assertEqual(model_bundle_id(raw), model_bundle_id(BUNDLE))
        self.assertEqual(model_bundle_id(raw.decode("utf-8")), model_bundle_id(BUNDLE))
        self.assertEqual(model_bundle_id(bytearray(raw)), model_bundle_id(BUNDLE))

    def test_an_empty_setting_is_the_default_bundle(self):
        for value in (None, b"", "", {}, "   ", b"\n"):
            with self.subTest(value=value):
                self.assertEqual(model_bundle_id(value), "default")

    def test_what_cannot_be_parsed_is_unreadable(self):
        for value in (b"{not json", "[1, 2]", "12", 12, True, [BUNDLE], b"\xff\xfe", {"name": "x"},
                      {"internalName": {1, 2}, "generation": 4}):
            with self.subTest(value=value):
                self.assertEqual(model_bundle_id(value), "unreadable")

    def test_a_bundle_with_some_identity_fields_missing_still_has_an_id(self):
        partial = {"internalName": "example-model", "generation": 4}
        self.assertRegex(model_bundle_id(partial), "^[0-9a-f]{12}$")
        self.assertNotEqual(model_bundle_id(partial), model_bundle_id(BUNDLE))

    def test_in_the_snapshot(self):
        self.assertEqual(read_settings(params())["modelBundleId"], model_bundle_id(BUNDLE))
        self.assertEqual(read_settings(params(ModelManager_ActiveBundle=None))["modelBundleId"], "default")
        self.assertEqual(read_settings(params(ModelManager_ActiveBundle=[1]))["modelBundleId"], "unreadable")
        self.assertEqual(read_settings(params(broken={"ModelManager_ActiveBundle"}))["modelBundleId"], "unreadable")


class WatchTests(unittest.TestCase):
    def test_five_settings_are_read_again_and_the_model_bundle_is_not(self):
        self.assertEqual(WATCHED, ("lagdToggle", "lagdToggleDelayS", "nnlc", "laneChangeTimer", "build"))
        first = read_settings(params())
        fake = FakeParams({**VALUES, "AutoLaneChangeTimer": 0, "ModelManager_ActiveBundle": None})
        again = reread_watched(DeviceParams(fake), first)
        self.assertEqual(again, {**first, "laneChangeTimer": 0})       # the bundle id is carried, not read
        self.assertNotIn("ModelManager_ActiveBundle", [key for key, _ in fake.asked])
        self.assertEqual(tuple(again), KEYS)

    def test_changed_keys_in_schema_order(self):
        first = read_settings(params())
        self.assertEqual(changed_keys(first, dict(first)), [])
        second = read_settings(params(AutoLaneChangeTimer=0, LagdToggle=False))
        self.assertEqual(changed_keys(first, second), ["lagdToggle", "laneChangeTimer"])

    def test_a_changed_download_status_is_no_change(self):
        first = read_settings(params())
        second = read_settings(params(ModelManager_ActiveBundle={**BUNDLE, "status": "downloading"}))
        self.assertEqual(changed_keys(first, second), [])
        third = read_settings(params(ModelManager_ActiveBundle={**BUNDLE, "ref": "ffff0000"}))
        self.assertEqual(changed_keys(first, third), ["modelBundleId"])

    def test_a_read_that_starts_to_fail_is_a_change_and_true_is_not_one(self):
        first = read_settings(params())
        self.assertEqual(changed_keys(first, read_settings(params(broken={"LagdToggle"}))), ["lagdToggle"])
        self.assertEqual(changed_keys({"laneChangeTimer": 1}, {"laneChangeTimer": True}), ["laneChangeTimer"])
        self.assertEqual(changed_keys({"lagdToggleDelayS": 0.2}, {"lagdToggleDelayS": 0.2}), [])


class NnlcModelTests(unittest.TestCase):
    def test_tokens(self):
        for name, token in (("MOCK", "mock"), ("mock", "mock"), (" Mock ", "mock"),
                            ("RAM_1500_5TH_GEN", "ram"), ("RAM 1500 5TH GEN", "ram"), ("ram1500", "ram"),
                            ("Ram_1500 5th_gen b", "ram"), ("RAM_HD_5TH_GEN", "other"), ("RAM_2500", "other"),
                            ("HYUNDAI_IONIQ_5", "other"), ("1500_RAM", "other"),
                            (None, None), ("", None), ("  ", None), (12, None), (b"MOCK", None)):
            with self.subTest(name=name):
                self.assertEqual(nnlc_model_token(name), token)


if __name__ == "__main__":
    unittest.main()
