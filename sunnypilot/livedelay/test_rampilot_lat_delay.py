"""Exercise the delay selector and its cache writer without the device parameter store."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class FakeParams:
  def __init__(self):
    self.values = {"LagdToggle": True, "LagdValueCache": 0.31, "LagdToggleDelay": 0.28}
    self.pending = {}

  def get_bool(self, key):
    return self.values[key]

  def get(self, key, return_default=False):
    return self.values[key]

  def put_nonblocking(self, key, value):
    self.pending[key] = value

  def flush(self):
    self.values.update(self.pending)
    self.pending.clear()


class LatDelayTests(unittest.TestCase):
  def setUp(self):
    self.params = FakeParams()
    # Load the actual definitions with only Params replaced. This test also runs outside a device build.
    root = Path(__file__).resolve().parents[1]
    ns = {"Params": lambda: self.params}
    for file, name in (("livedelay/helpers.py", "get_lat_delay"), ("modeld/modeld_base.py", "ModelStateBase"),
                       ("livedelay/lagd_toggle.py", "LagdToggle")):
      source = root / file
      node = next(n for n in ast.parse(source.read_text(encoding="utf-8")).body if getattr(n, "name", None) == name)
      exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), ns)
    self.get_delay = ns["get_lat_delay"]
    self.model = ns["ModelStateBase"]()
    self.writer = ns["LagdToggle"](SimpleNamespace(steerActuatorDelay=0.20))

  def test_live_mode_before_first_sample(self):
    self.assertEqual(self.model.lat_delay, 0.31)
    self.assertEqual(self.get_delay(self.params, None), 0.31)

  def test_received_value_controls_live_mode_even_when_zero(self):
    self.assertEqual(self.get_delay(self.params, 0.46), 0.46)
    self.assertEqual(self.get_delay(self.params, 0.0), 0.0)

  def test_polling_waits_for_first_sample_and_keeps_it_between_updates(self):
    class SubMaster(dict):
      pass

    sm = SubMaster(liveDelay=SimpleNamespace(lateralDelay=0.0))
    sm.seen = {"liveDelay": False}
    sm.updated = {"liveDelay": False}
    self.model.update_lat_delay(self.params, sm)
    self.assertEqual(self.model.lat_delay, 0.31)

    sm.seen["liveDelay"] = sm.updated["liveDelay"] = True
    sm["liveDelay"].lateralDelay = 0.46
    self.model.update_lat_delay(self.params, sm)
    self.assertEqual(self.model.lat_delay, 0.46)

    sm.updated["liveDelay"] = False
    self.model.update_lat_delay(self.params, sm)
    self.assertEqual(self.model.lat_delay, 0.46)

  def test_switch_to_fixed_mode_uses_cache_until_its_write_completes(self):
    published = SimpleNamespace(liveDelay=SimpleNamespace(lateralDelay=0.46))
    self.writer.update(published)
    self.assertEqual(self.get_delay(self.params, None), 0.31)
    self.assertEqual(self.get_delay(self.params, 0.46), 0.46)
    self.writer.params.flush()
    self.assertEqual(self.get_delay(self.params, None), 0.46)

    self.params.values["LagdToggle"] = False
    published.liveDelay.lateralDelay = 0.71
    self.writer.update(published)
    self.assertEqual(self.get_delay(self.params, 0.71), 0.46)
    self.writer.params.flush()
    self.assertAlmostEqual(self.get_delay(self.params, 0.71), 0.48)
    self.assertAlmostEqual(self.get_delay(self.params, None), 0.48)
    self.params.values["LagdToggle"] = True
    self.assertEqual(self.get_delay(self.params, 0.71), 0.71)


if __name__ == "__main__":
  unittest.main()
