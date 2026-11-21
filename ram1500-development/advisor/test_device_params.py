"""Comma settings adapter and the read-only readers, tested with a fake Params."""
import unittest
from types import SimpleNamespace as ns

import device_params
from device_params import (DeviceParams, base_actuator_delay_s, cache_delay_s, nnlc_match, persisted_live_delay,
                           read_setting)


class FakeParams:
    """Reads like the Comma's typed Params: a value, or None when nothing is stored. A key in `broken` raises."""

    def __init__(self, values, broken=()):
        self.values = dict(values)
        self.broken = set(broken)
        self.asked = []

    def get(self, key, return_default=False):
        self.asked.append((key, return_default))
        if key in self.broken:
            raise KeyError(key)
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value

    def get_bool(self, key):
        return self.values.get(key) is True


def params(values=None, broken=()):
    return DeviceParams(FakeParams(values or {}, broken))


def decode(raw):
    return ns(steerActuatorDelay=float(raw.decode()))


class DeviceParamsTests(unittest.TestCase):
    def test_get_put_round_trip(self):
        p = params()
        p.put("LagdToggleDelay", 0.15)
        self.assertEqual(p.get("LagdToggleDelay"), 0.15)

    def test_onroad(self):
        self.assertTrue(params({"IsOnroad": True}).onroad())
        self.assertFalse(params().onroad())

    def test_a_setting_that_was_never_set_reads_as_its_declared_default(self):
        fake = FakeParams({"LagdToggle": True})
        DeviceParams(fake).get("LagdToggle")
        self.assertEqual(fake.asked, [("LagdToggle", True)])           # asked for with return_default


class ReadSettingTests(unittest.TestCase):
    """A value of the wrong type, or a read that raises, gives None and never a default."""

    def test_values_of_the_declared_type(self):
        p = params({"LagdToggle": False, "LagdToggleDelay": 0.2, "AutoLaneChangeTimer": 2, "GitCommit": "0123abcd"})
        self.assertIs(read_setting(p, "LagdToggle", bool), False)
        self.assertEqual(read_setting(p, "LagdToggleDelay", float), 0.2)
        self.assertEqual(read_setting(p, "AutoLaneChangeTimer", int), 2)
        self.assertEqual(read_setting(p, "GitCommit", str), "0123abcd")

    def test_a_whole_number_is_a_float_too_and_comes_back_as_one(self):
        value = read_setting(params({"LagdToggleDelay": 1}), "LagdToggleDelay", float)
        self.assertEqual((value, type(value)), (1.0, float))

    def test_wrong_types_give_none(self):
        for kind, wrong in ((bool, (1, 0, "1", None, 1.0, b"1")),
                            (float, (True, "0.2", None, b"0.2", float("nan"), float("inf"))),
                            (int, (True, 2.0, "2", None, b"2")),
                            (str, (b"abc", 12, None, True))):
            for value in wrong:
                with self.subTest(kind=kind.__name__, value=value):
                    self.assertIsNone(read_setting(params({"X": value}), "X", kind))

    def test_a_read_that_raises_gives_none(self):
        p = params({"LagdToggle": True}, broken={"LagdToggle"})
        self.assertIsNone(read_setting(p, "LagdToggle", bool))

    def test_an_unknown_kind_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            read_setting(params({"X": [1]}), "X", list)


class DelayReaderTests(unittest.TestCase):
    def test_there_is_no_reader_of_the_learned_delay_from_the_cache(self):
        self.assertFalse(hasattr(device_params, "learned_delay_s"))

    def test_the_cached_total_is_read_for_diagnosis_only(self):
        self.assertEqual(cache_delay_s(params({"LagdValueCache": 0.4})), 0.4)
        self.assertIsNone(cache_delay_s(params({"LagdValueCache": "0.4"})))
        self.assertIsNone(cache_delay_s(params({}, broken={"LagdValueCache"})))

    def test_base_delay_from_car_params_with_persistent_fallback(self):
        self.assertEqual(base_actuator_delay_s(params({"CarParams": b"0.2"}), decode), 0.2)
        self.assertEqual(base_actuator_delay_s(params({"CarParamsPersistent": b"0.1"}), decode), 0.1)
        self.assertIsNone(base_actuator_delay_s(params(), decode))

    def test_undecodable_car_params_gives_none(self):
        def broken(raw):
            raise ValueError("bad")
        self.assertIsNone(base_actuator_delay_s(params({"CarParams": b"x"}), broken))

    def test_a_car_params_read_that_raises_gives_none(self):
        self.assertIsNone(base_actuator_delay_s(params({"CarParams": b"0.2"}, broken={"CarParams"}), decode))

    def test_the_learners_persisted_message(self):
        message = ns(lateralDelay=0.3457, status="estimated")
        got = persisted_live_delay(params({"LiveDelay": b"stored"}), lambda raw: message if raw == b"stored" else None)
        self.assertIs(got, message)

    def test_no_persisted_message(self):
        calls = []
        for values in ({}, {"LiveDelay": b""}, {"LiveDelay": None}, {"LiveDelay": "text"}, {"LiveDelay": 12}):
            with self.subTest(values=values):
                self.assertIsNone(persisted_live_delay(params(values), calls.append))
        self.assertEqual(calls, [])                                    # nothing was handed to the decoder

    def test_a_persisted_message_that_cannot_be_read(self):
        def broken(raw):
            raise ValueError("bad")
        self.assertIsNone(persisted_live_delay(params({"LiveDelay": b"x"}), broken))
        self.assertIsNone(persisted_live_delay(params({"LiveDelay": b"x"}, broken={"LiveDelay"}), lambda raw: 1))


def car_params_sp(name="RAM 1500 5TH GEN", fuzzy=False):
    return ns(neuralNetworkLateralControl=ns(model=ns(name=name, path="x"), fuzzyFingerprint=fuzzy))


class NnlcMatchTests(unittest.TestCase):
    """Which neural-network model the build matched to the truck: its name and the fuzzy-match flag."""

    def test_from_car_params_sp(self):
        got = nnlc_match(params({"CarParamsSP": b"a"}), lambda raw: car_params_sp("RAM_1500_5TH_GEN", True))
        self.assertEqual(got, ("RAM_1500_5TH_GEN", True))

    def test_falls_back_to_the_persistent_copy(self):
        seen = []

        def decode_sp(raw):
            seen.append(raw)
            return car_params_sp("MOCK", False)
        self.assertEqual(nnlc_match(params({"CarParamsSPPersistent": b"p"}), decode_sp), ("MOCK", False))
        self.assertEqual(nnlc_match(params({"CarParamsSP": b"", "CarParamsSPPersistent": b"p"}), decode_sp),
                         ("MOCK", False))
        self.assertEqual(seen, [b"p", b"p"])

    def test_the_copy_of_this_drive_comes_before_the_persistent_one(self):
        def decode_sp(raw):
            return car_params_sp("RAM_1500_5TH_GEN" if raw == b"now" else "MOCK", raw != b"now")
        got = nnlc_match(params({"CarParamsSP": b"now", "CarParamsSPPersistent": b"earlier"}), decode_sp)
        self.assertEqual(got, ("RAM_1500_5TH_GEN", False))

    def test_nothing_stored(self):
        self.assertEqual(nnlc_match(params(), lambda raw: car_params_sp()), (None, None))

    def test_unreadable(self):
        def broken(raw):
            raise ValueError("bad")
        self.assertEqual(nnlc_match(params({"CarParamsSP": b"a"}), broken), (None, None))
        self.assertEqual(nnlc_match(params({"CarParamsSP": b"a"}, broken={"CarParamsSP"}), lambda raw: car_params_sp()),
                         (None, None))
        self.assertEqual(nnlc_match(params({"CarParamsSP": b"a"}), lambda raw: ns(other=1)), (None, None))

    def test_values_of_the_wrong_type_are_unknown_one_by_one(self):
        self.assertEqual(nnlc_match(params({"CarParamsSP": b"a"}), lambda raw: car_params_sp(12, "yes")), (None, None))
        self.assertEqual(nnlc_match(params({"CarParamsSP": b"a"}), lambda raw: car_params_sp("MOCK", None)),
                         ("MOCK", None))


if __name__ == "__main__":
    unittest.main()
