"""Comma settings adapter and delay readers, tested with a fake Params."""
import unittest
from types import SimpleNamespace as ns

from device_params import DeviceParams, base_actuator_delay_s, learned_delay_s


class FakeParams:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, return_default=False):
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value

    def get_bool(self, key):
        return self.values.get(key) is True


def decode(raw):
    return ns(steerActuatorDelay=float(raw.decode()))


class DeviceParamsTests(unittest.TestCase):
    def test_get_put_round_trip(self):
        params = DeviceParams(FakeParams({}))
        params.put("LagdToggleDelay", 0.15)
        self.assertEqual(params.get("LagdToggleDelay"), 0.15)

    def test_onroad(self):
        self.assertTrue(DeviceParams(FakeParams({"IsOnroad": True})).onroad())
        self.assertFalse(DeviceParams(FakeParams({})).onroad())


class DelayTests(unittest.TestCase):
    def test_learned_delay_only_while_learning_is_on(self):
        self.assertEqual(learned_delay_s(DeviceParams(FakeParams({"LagdToggle": True, "LagdValueCache": 0.27}))), 0.27)
        self.assertIsNone(learned_delay_s(DeviceParams(FakeParams({"LagdToggle": False, "LagdValueCache": 0.27}))))

    def test_implausible_learned_delay_ignored(self):
        for value in (None, 0.0, 1.5, float("nan"), True):
            with self.subTest(value=value):
                self.assertIsNone(learned_delay_s(DeviceParams(FakeParams({"LagdToggle": True, "LagdValueCache": value}))))

    def test_base_delay_from_car_params_with_persistent_fallback(self):
        self.assertEqual(base_actuator_delay_s(DeviceParams(FakeParams({"CarParams": b"0.2"})), decode), 0.2)
        self.assertEqual(base_actuator_delay_s(DeviceParams(FakeParams({"CarParamsPersistent": b"0.1"})), decode), 0.1)
        self.assertIsNone(base_actuator_delay_s(DeviceParams(FakeParams({})), decode))

    def test_undecodable_car_params_gives_none(self):
        def broken(raw):
            raise ValueError("bad")
        self.assertIsNone(base_actuator_delay_s(DeviceParams(FakeParams({"CarParams": b"x"})), broken))


if __name__ == "__main__":
    unittest.main()
