"""RamPilot: the "Blind Spot Monitoring Off" notice when blind-spot support did not start this drive.

Checks the schema addition, the alert definition and the selfdrived condition. The condition runs as the
real `if` statement from selfdrived.update_events with stand-in objects, so no device is needed."""
import ast
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SELFDRIVED = ROOT / "selfdrive" / "selfdrived" / "selfdrived.py"
TREE = ast.parse(SELFDRIVED.read_text(encoding="utf-8"))


def _method(name):
  cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
  return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _notice_if():
  for node in ast.walk(_method("update_events")):
    if isinstance(node, ast.If) and "rampilotBlindSpotUnavailable" in ast.unparse(node):
      return node
  raise AssertionError("update_events does not raise rampilotBlindSpotUnavailable")


LCS = types.SimpleNamespace(off=0, preLaneChange=1, laneChangeStarting=2, laneChangeFinishing=3)
NOTICE = "rampilotBlindSpotUnavailable"


def raised(bsm_off, frame, lane_change_state):
  added = []
  me = types.SimpleNamespace(rampilot_bsm_off=bsm_off,
                             sm=types.SimpleNamespace(frame=frame, __getitem__=None),
                             events_sp=types.SimpleNamespace(add=added.append))
  sm = {"modelV2": types.SimpleNamespace(meta=types.SimpleNamespace(laneChangeState=lane_change_state))}
  me.sm = type("SM", (), {"frame": frame, "__getitem__": lambda self, k: sm[k]})()
  custom = types.SimpleNamespace(OnroadEventSP=types.SimpleNamespace(EventName=types.SimpleNamespace(rampilotBlindSpotUnavailable=NOTICE)))
  ns = {"self": me, "custom": custom, "LaneChangeState": LCS, "DT_CTRL": 0.01, "RAMPILOT_BSM_NOTICE_S": 15.}
  exec(compile(ast.Module(body=[_notice_if()], type_ignores=[]), str(SELFDRIVED), "exec"), ns)
  return NOTICE in added


class BsmAlertTests(unittest.TestCase):
  def test_shown_at_startup_when_blind_spot_is_off(self):
    self.assertTrue(raised(True, 100, LCS.off))

  def test_shown_when_a_lane_change_is_pending(self):
    self.assertTrue(raised(True, 100000, LCS.preLaneChange))
    self.assertTrue(raised(True, 100000, LCS.laneChangeStarting))

  def test_quiet_otherwise(self):
    self.assertFalse(raised(True, 100000, LCS.off))
    self.assertFalse(raised(False, 100, LCS.preLaneChange))

  def test_only_for_the_ram_1500_without_blind_spot(self):
    src = ast.unparse(_method("__init__"))
    self.assertIn("self.rampilot_bsm_off = self.CP.carFingerprint == 'RAM_1500_5TH_GEN' and (not self.CP.enableBsm)", src)

  def test_alert_is_display_only(self):
    events = (ROOT / "sunnypilot" / "selfdrive" / "selfdrived" / "events.py").read_text(encoding="utf-8")
    block = events[events.index("EventNameSP.rampilotBlindSpotUnavailable: {"):]
    block = block[:block.index("},") + 2]
    self.assertIn("ET.PERMANENT: NormalPermanentAlert(\"Blind Spot Monitoring Off\"", block)
    for blocking in ("NO_ENTRY", "SOFT_DISABLE", "IMMEDIATE_DISABLE", "USER_DISABLE"):
      self.assertNotIn(blocking, block)

  def test_schema_adds_the_event_at_the_end(self):
    schema = (ROOT / "cereal" / "custom.capnp").read_text(encoding="utf-8")
    self.assertIn("    laneTurnRight @18;\n    rampilotBlindSpotUnavailable @19;", schema)
    try:
      import capnp
    except ImportError:
      self.skipTest("pycapnp not installed")
    capnp.remove_import_hook()
    custom = capnp.load(str(ROOT / "cereal" / "custom.capnp"), imports=[str(ROOT / "cereal"), str(ROOT / "cereal" / "include")])
    names = custom.OnroadEventSP.EventName.schema.enumerants
    self.assertEqual(names["rampilotBlindSpotUnavailable"], 19)
    self.assertEqual(names["laneTurnRight"], 18)


if __name__ == "__main__":
  unittest.main()
