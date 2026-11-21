"""RamPilot backport of openpilot #39095: after a stall the Ratekeeper resyncs instead of running catch-up frames.

Runs the real Ratekeeper.monitor_time from realtime.py on a stand-in object with a fake clock."""
import ast
import pathlib
import types
import unittest

REALTIME = pathlib.Path(__file__).resolve().parents[1] / "realtime.py"
_cls = next(n for n in ast.parse(REALTIME.read_text(encoding="utf-8")).body if isinstance(n, ast.ClassDef) and n.name == "Ratekeeper")
_fn = next(n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name == "monitor_time")
_fn.decorator_list = []


def make(interval=0.01):
  clock = types.SimpleNamespace(t=100.0)
  ns = {"time": types.SimpleNamespace(monotonic=lambda: clock.t), "print": lambda *a, **k: None}
  exec(compile(ast.Module(body=[_fn], type_ignores=[]), str(REALTIME), "exec"), ns)
  rk = types.SimpleNamespace(_interval=interval, _next_frame_time=0.0, _last_monitor_time=-1.0, _frame=0,
                             _remaining=0.0, _print_delay_threshold=0.0, _process_name="test",
                             avg_dt=types.SimpleNamespace(add_value=lambda v: None))
  return rk, clock, (lambda: ns["monitor_time"](rk))


class RatekeeperResyncTests(unittest.TestCase):
  def test_on_time_frames_advance_by_one_interval(self):
    rk, clock, monitor = make()
    monitor()                                  # first call sets the schedule
    start = rk._next_frame_time
    for i in range(5):
      clock.t += 0.01
      self.assertFalse(monitor())
    self.assertAlmostEqual(rk._next_frame_time, start + 5 * 0.01)

  def test_a_stall_resyncs_instead_of_catching_up(self):
    rk, clock, monitor = make()
    monitor()
    clock.t += 0.5                             # 50 intervals behind
    self.assertTrue(monitor())                 # still reported as lagging
    self.assertAlmostEqual(rk._next_frame_time, clock.t + 0.01)
    self.assertGreater(rk._remaining, -1.0)    # the lag of this frame is still recorded
    self.assertLess(rk._remaining, -0.4)

  def test_a_small_slip_is_still_caught_up(self):
    rk, clock, monitor = make()
    monitor()
    sched = rk._next_frame_time
    clock.t = sched + 0.005                    # half an interval late: keep the original schedule
    monitor()
    self.assertAlmostEqual(rk._next_frame_time, sched + 0.01)


if __name__ == "__main__":
  unittest.main()
