"""RamPilot backport of sunnypilot #1906: the fixed steering-delay setting reaches controlsd.

Runs the real get_lat_delay from helpers.py with a stand-in Params, so it needs no device."""
import ast
import pathlib
import unittest

HELPERS = pathlib.Path(__file__).resolve().parent / "helpers.py"
_fn = next(n for n in ast.parse(HELPERS.read_text(encoding="utf-8")).body
           if isinstance(n, ast.FunctionDef) and n.name == "get_lat_delay")
_ns = {"Params": object}
exec(compile(ast.Module(body=[_fn], type_ignores=[]), str(HELPERS), "exec"), _ns)
get_lat_delay = _ns["get_lat_delay"]


class FakeParams:
  def __init__(self, live_learning: bool, cache: float):
    self.values = {"LagdToggle": live_learning, "LagdValueCache": cache}

  def get_bool(self, key):
    return bool(self.values[key])

  def get(self, key, return_default=False):
    return self.values[key]


LEARNED, FIXED = 0.33, 0.55


class LatDelayTests(unittest.TestCase):
  def test_live_learning_on_uses_the_published_delay(self):
    self.assertEqual(get_lat_delay(FakeParams(True, LEARNED), LEARNED), LEARNED)

  def test_live_learning_off_uses_the_fixed_setting(self):
    # LagdToggle off: lagd caches steerActuatorDelay + software delay; controlsd must use it
    self.assertEqual(get_lat_delay(FakeParams(False, FIXED), LEARNED), FIXED)


if __name__ == "__main__":
  unittest.main()
