"""RamPilot late blind-spot check (chrysler/rampilot_bsm.py).

The unit tests load the module by path and need nothing else. The get_car tests run the real startup
path with simulated CAN and skip when the opendbc dependencies are not installed."""
import ast
import importlib.util
import os
import pathlib
import time
import unittest
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("rampilot_bsm", HERE.parent / "rampilot_bsm.py")
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

Can = namedtuple("Can", "address dat src")
BSM = Can(720, bytes(6), 0)
OTHER = Can(0x2A0, bytes(8), 0)


class FakeBus:
  """can_recv stand-in: each call is 10 ms; `schedule(t)` returns the frames sent at time t."""
  def __init__(self, schedule, period=0.01):
    self.t, self.period, self.schedule, self.calls = 0.0, period, schedule, 0

  def clock(self):
    return self.t

  def can_recv(self, wait_for_one=False):
    self.calls += 1
    self.t += self.period
    frames = self.schedule(self.t)
    return [frames] if frames else []


def bsm_from(start_s, period_s=0.5):
  return lambda t: [OTHER] + ([BSM] if t >= start_s and abs(((t - start_s) % period_s)) < 0.0099 else [])


class WaitForLateBsmTests(unittest.TestCase):
  def run_case(self, fingerprint, schedule, wait_s=rb.LATE_BSM_WAIT_S):
    bus = FakeBus(schedule)
    out = rb.wait_for_late_bsm(bus.can_recv, fingerprint, wait_s=wait_s, monotonic=bus.clock)
    return out, bus

  def test_timely_bsm_already_in_the_fingerprint_reads_nothing(self):
    fp = {0: {720: 6}}
    out, bus = self.run_case(fp, bsm_from(0.0))
    self.assertEqual(out, 0.0)
    self.assertEqual(bus.calls, 0)

  def test_late_bsm_is_added_when_it_arrives(self):
    fp = {0: {0x2A0: 8}}
    out, _ = self.run_case(fp, bsm_from(0.31))
    self.assertIn(720, fp[0])
    self.assertAlmostEqual(out, 0.31, delta=0.011)

  def test_missing_bsm_leaves_the_fingerprint_unchanged_after_the_wait(self):
    fp = {0: {0x2A0: 8}}
    out, bus = self.run_case(fp, lambda t: [OTHER])
    self.assertIsNone(out)
    self.assertNotIn(720, fp[0])
    self.assertGreaterEqual(bus.t, rb.LATE_BSM_WAIT_S)
    self.assertLess(bus.t, rb.LATE_BSM_WAIT_S + 0.02)

  def test_bsm_after_the_wait_is_not_added(self):
    fp = {0: {}}
    out, _ = self.run_case(fp, bsm_from(rb.LATE_BSM_WAIT_S + 0.1))
    self.assertIsNone(out)
    self.assertNotIn(720, fp[0])

  def test_silent_bus_ends_at_the_deadline(self):
    fp = {0: {}}
    out, bus = self.run_case(fp, lambda t: [])
    self.assertIsNone(out)
    self.assertLess(bus.t, rb.LATE_BSM_WAIT_S + 0.02)

  def test_address_720_on_another_bus_does_not_count(self):
    fp = {0: {}}
    out, _ = self.run_case(fp, lambda t: [Can(720, bytes(6), 2)])
    self.assertIsNone(out)
    self.assertEqual(fp, {0: {}})

  def test_only_bsm_is_added(self):
    fp = {0: {}}
    self.run_case(fp, bsm_from(0.5))
    self.assertEqual(fp, {0: {720: 6}})


class GetCarHookTests(unittest.TestCase):
  SRC = (HERE.parents[1] / "car_helpers.py").read_text(encoding="utf-8")

  def test_hook_runs_before_carparams_are_built_and_only_for_ram_1500(self):
    get_car = next(n for n in ast.parse(self.SRC).body if isinstance(n, ast.FunctionDef) and n.name == "get_car")
    lines = ast.unparse(get_car).splitlines()
    hook = next(i for i, ln in enumerate(lines) if "wait_for_late_bsm(" in ln)
    params = next(i for i, ln in enumerate(lines) if "CarInterface.get_params(" in ln)
    self.assertLess(hook, params)
    self.assertIn("if candidate == CHRYSLER_CAR.RAM_1500_5TH_GEN:", lines[hook - 1])


def _opendbc_available():
  try:
    import opendbc.car.car_helpers  # noqa: F401
    return True
  except Exception:
    return False


@unittest.skipUnless(_opendbc_available(), "opendbc dependencies not installed")
class GetCarIntegrationTests(unittest.TestCase):
  """Real get_car() with simulated CAN at 100 Hz (real time). FW query skipped; platform fixed."""

  def get_car(self, bsm_start_s):
    from opendbc.car.car_helpers import get_car
    start = time.monotonic()

    def can_recv(wait_for_one=False):
      time.sleep(0.01)
      t = time.monotonic() - start
      return [bsm_from(bsm_start_s)(t)] if bsm_start_s is not None else [[OTHER]]

    os.environ["SKIP_FW_QUERY"] = "1"
    try:
      CI = get_car(can_recv, lambda msgs: None, lambda multiplexing: None, False, False,
                   fixed_fingerprint="RAM_1500_5TH_GEN", init_params_list_sp=[])
    finally:
      del os.environ["SKIP_FW_QUERY"]
    return CI.CP, time.monotonic() - start

  def test_bsm_in_the_fingerprint_window(self):
    CP, _ = self.get_car(0.0)
    self.assertTrue(CP.enableBsm)

  def test_bsm_starting_after_the_window_is_enabled(self):
    # can_fingerprint ends by frame 200 (about 2 s here); BSM_1 starts after that
    CP, elapsed = self.get_car(2.5)
    self.assertTrue(CP.enableBsm)
    self.assertLess(elapsed, 2.5 + 0.6)

  def test_no_bsm_stays_off(self):
    CP, elapsed = self.get_car(None)
    self.assertFalse(CP.enableBsm)
    self.assertGreaterEqual(elapsed, rb.LATE_BSM_WAIT_S)


if __name__ == "__main__":
  unittest.main()
