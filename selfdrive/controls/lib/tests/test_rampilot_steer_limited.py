"""RamPilot backport of sunnypilot #1716: steer_limited_by_safety keeps updating in steering-only mode.

The installed build updated the flag only while selfdriveState.active (cruise engaged). In steering-only
mode it froze at its last value, which disables the saturation timer ("Turn Exceeds Steering Limit")
whenever it froze True. These tests run the real `if` block from controlsd.publish() with stand-in
objects, so they need neither cereal nor a device."""
import ast
import pathlib
import textwrap
import types
import unittest

CONTROLSD = pathlib.Path(__file__).resolve().parents[2] / "controlsd.py"
SRC = CONTROLSD.read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _method(name):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Controls")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _flag_block():
    """The `if` statement in publish() that assigns self.steer_limited_by_safety, compiled as a function."""
    for node in ast.walk(_method("publish")):
        if isinstance(node, ast.If) and "steer_limited_by_safety" in ast.unparse(node.body):
            body = textwrap.indent(ast.unparse(node), "  ")
            ns = {}
            exec(f"def gate(self, CC, car, STEER_ANGLE_SATURATION_THRESHOLD):\n{body}\n", ns)
            return node, ns["gate"]
    raise AssertionError("publish() no longer assigns steer_limited_by_safety")


TORQUE = "torque"
CAR = types.SimpleNamespace(CarParams=types.SimpleNamespace(SteerControlType=types.SimpleNamespace(angle="angle", torque=TORQUE)))


def _controls(lat_active, cruise_active, requested, sent, flag):
    c = types.SimpleNamespace()
    c.lat_active_for_limits = lat_active
    c.steer_limited_by_safety = flag
    c.CP = types.SimpleNamespace(steerControlType=TORQUE)
    c.sm = {"selfdriveState": types.SimpleNamespace(active=cruise_active),
            "carOutput": types.SimpleNamespace(actuatorsOutput=types.SimpleNamespace(torque=sent, steeringAngleDeg=0.0))}
    CC = types.SimpleNamespace(actuators=types.SimpleNamespace(torque=requested, steeringAngleDeg=0.0))
    return c, CC


class SteerLimitedBackportTests(unittest.TestCase):
    def setUp(self):
        self.node, self.gate = _flag_block()

    def run_gate(self, **kw):
        c, CC = _controls(**kw)
        self.gate(c, CC, CAR, 0.0)
        return c.steer_limited_by_safety

    def test_steering_only_mode_clears_a_stale_true_flag(self):
        # Steering on, cruise off, request fully sent: the flag that froze True must clear.
        self.assertFalse(self.run_gate(lat_active=True, cruise_active=False, requested=0.40, sent=0.40, flag=True))

    def test_steering_only_mode_sets_the_flag_when_the_car_limits_torque(self):
        self.assertTrue(self.run_gate(lat_active=True, cruise_active=False, requested=0.60, sent=0.45, flag=False))

    def test_fully_engaged_behaviour_is_unchanged(self):
        self.assertTrue(self.run_gate(lat_active=True, cruise_active=True, requested=0.60, sent=0.45, flag=False))
        self.assertFalse(self.run_gate(lat_active=True, cruise_active=True, requested=0.40, sent=0.40, flag=True))

    def test_steering_off_holds_the_last_value(self):
        self.assertTrue(self.run_gate(lat_active=False, cruise_active=False, requested=0.0, sent=0.0, flag=True))
        self.assertFalse(self.run_gate(lat_active=False, cruise_active=True, requested=0.6, sent=0.0, flag=False))

    def test_gate_reads_the_value_computed_in_state_control(self):
        self.assertEqual(ast.unparse(self.node.test), "self.lat_active_for_limits")
        assigns = [ast.unparse(n) for n in ast.walk(_method("state_control")) if isinstance(n, ast.Assign)]
        self.assertIn("_lat_active = self.get_lat_active(self.sm)", assigns)
        self.assertIn("self.lat_active_for_limits = _lat_active", assigns)
        i_get = assigns.index("_lat_active = self.get_lat_active(self.sm)")
        self.assertEqual(assigns[i_get + 1], "self.lat_active_for_limits = _lat_active")

    def test_get_lat_active_is_called_once_per_cycle(self):
        # Newer sunnypilot's blinker-pause timer counts down on every call; one call per cycle keeps it correct.
        calls = [n for n in ast.walk(TREE) if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.get_lat_active"]
        self.assertEqual(len(calls), 1)

    def test_initialised_before_the_first_cycle(self):
        assigns = [ast.unparse(n) for n in ast.walk(_method("__init__")) if isinstance(n, ast.Assign)]
        self.assertIn("self.lat_active_for_limits = False", assigns)


if __name__ == "__main__":
    unittest.main()
