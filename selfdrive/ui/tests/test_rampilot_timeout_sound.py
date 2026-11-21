"""RamPilot backport of sunnypilot #1717: the "stopped responding" warning also covers steering-only mode.

Runs the real check_selfdrive_timeout_alert() from soundd.py with stand-in objects and a fake clock,
so it needs neither cereal nor a device. The upstream messaging test is in test_soundd.py."""
import ast
import pathlib
import types
import unittest

SOUNDD = pathlib.Path(__file__).resolve().parents[1] / "soundd.py"
TREE = ast.parse(SOUNDD.read_text(encoding="utf-8"))
TIMEOUT = next(n.value.value for n in TREE.body if isinstance(n, ast.Assign)
               and [t.id for t in n.targets if isinstance(t, ast.Name)] == ["SELFDRIVE_STATE_TIMEOUT"])


def _load_check(now):
    fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "check_selfdrive_timeout_alert")
    ns = {"time": types.SimpleNamespace(monotonic=lambda: now[0]), "SELFDRIVE_STATE_TIMEOUT": TIMEOUT}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOUNDD), "exec"), ns)
    return ns["check_selfdrive_timeout_alert"]


class _SM(dict):
    def __init__(self, enabled, mads_enabled, last_recv):
        super().__init__(selfdriveState=types.SimpleNamespace(enabled=enabled),
                         selfdriveStateSP=types.SimpleNamespace(mads=types.SimpleNamespace(enabled=mads_enabled)))
        self.recv_time = {"selfdriveState": last_recv}


class TimeoutSoundTests(unittest.TestCase):
    def check(self, enabled, mads_enabled, missing_s):
        now = [1000.0]
        return _load_check(now)(_SM(enabled, mads_enabled, now[0] - missing_s))

    def test_steering_only_mode_warns_after_the_timeout(self):
        self.assertTrue(self.check(enabled=False, mads_enabled=True, missing_s=TIMEOUT + 0.1))

    def test_fully_engaged_still_warns(self):
        self.assertTrue(self.check(enabled=True, mads_enabled=False, missing_s=TIMEOUT + 0.1))

    def test_no_warning_before_the_timeout(self):
        self.assertFalse(self.check(enabled=False, mads_enabled=True, missing_s=TIMEOUT - 0.1))

    def test_no_warning_when_nothing_was_engaged(self):
        self.assertFalse(self.check(enabled=False, mads_enabled=False, missing_s=TIMEOUT + 0.1))

    def test_warning_stops_ten_seconds_after_the_timeout(self):
        self.assertFalse(self.check(enabled=False, mads_enabled=True, missing_s=TIMEOUT + 10.1))

    def test_soundd_subscribes_to_the_mads_state(self):
        subs = [ast.unparse(n) for n in ast.walk(TREE) if isinstance(n, ast.Call) and ast.unparse(n.func) == "messaging.SubMaster"]
        self.assertEqual(len(subs), 1)
        self.assertIn("'selfdriveStateSP'", subs[0])


if __name__ == "__main__":
    unittest.main()
