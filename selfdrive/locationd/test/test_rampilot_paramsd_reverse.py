"""RamPilot backport of openpilot #38573: paramsd does not learn steer ratio, stiffness or offset in reverse.

Evaluates the real `self.active = ...` statement from ParamsLearner.handle_log's carState branch with
stand-in objects, so it needs neither the compiled Kalman filter nor a device."""
import ast
import pathlib
import types
import unittest

PARAMSD = pathlib.Path(__file__).resolve().parents[1] / "paramsd.py"
TREE = ast.parse(PARAMSD.read_text(encoding="utf-8"))
MIN_ACTIVE_SPEED = next(n.value.value for n in TREE.body if isinstance(n, ast.Assign)
                        and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["MIN_ACTIVE_SPEED"])


def _carstate_branch():
  for node in ast.walk(TREE):
    if isinstance(node, ast.If) and ast.unparse(node.test) == "which == 'carState'":
      return node
  raise AssertionError("handle_log has no carState branch")


def _active_statement():
  stmt = next(n for n in _carstate_branch().body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "self.active")
  return compile(ast.Module(body=[stmt], type_ignores=[]), str(PARAMSD), "exec")


GEAR = types.SimpleNamespace(drive="drive", reverse="reverse", park="park", neutral="neutral")
CAR = types.SimpleNamespace(CarState=types.SimpleNamespace(GearShifter=GEAR))


def active(v, angle, gear):
  me = types.SimpleNamespace(observed_speed=v, active=None)
  ns = {"self": me, "msg": types.SimpleNamespace(gearShifter=gear), "car": CAR,
        "MIN_ACTIVE_SPEED": MIN_ACTIVE_SPEED, "in_linear_region": abs(angle) < 45}
  exec(_active_statement(), ns)
  return me.active


class ParamsdReverseTests(unittest.TestCase):
  def test_no_learning_in_reverse(self):
    self.assertFalse(active(2.0, 5.0, GEAR.reverse))

  def test_forward_learning_is_unchanged(self):
    self.assertTrue(active(2.0, 5.0, GEAR.drive))
    self.assertFalse(active(MIN_ACTIVE_SPEED, 5.0, GEAR.drive))
    self.assertFalse(active(20.0, 50.0, GEAR.drive))

  def test_statement_reads_the_gear_from_carstate(self):
    src = ast.unparse(_carstate_branch())
    self.assertIn("msg.gearShifter != car.CarState.GearShifter.reverse", src)


if __name__ == "__main__":
  unittest.main()
