"""Test the driver-visible blind-spot notice with the real event tables and alert manager."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from cereal import car, custom, log
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived import events as sp_events
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import AlertSize, AlertStatus, AudibleAlert, ET, Priority, VisualAlert


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "selfdrive/selfdrived/selfdrived.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
LCS = log.LaneChangeState
TITLE = "Comma Blind-Spot Data Unavailable"
NOTICE = custom.OnroadEventSP.EventName.rampilotBlindSpotUnavailable
EVENT = log.OnroadEvent.EventName
RAM = "RAM_1500_5TH_GEN"


def notice_conditions():
  # Run the production init and two event-generation clauses without starting the process or opening sockets.
  cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
  methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
  init = next(n for n in methods["__init__"].body if isinstance(n, ast.Assign) and
              ast.unparse(n.targets[0]) == "self.rampilot_bsm_off")
  relevant = [n for n in methods["update_events"].body if isinstance(n, ast.If) and
              ("laneChangeBlocked" in ast.unparse(n) or "rampilotBlindSpotUnavailable" in ast.unparse(n))]
  duration = next(n for n in TREE.body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "RAMPILOT_BSM_NOTICE_S")
  namespace = {"ET": ET, "LONGITUDINAL_PERSONALITY_MAP": {"standard": log.LongitudinalPersonality.standard}}
  exec(compile(ast.Module(body=[methods["update_alerts"]], type_ignores=[]), str(SOURCE), "exec"), namespace)
  return compile(ast.Module(body=[duration, init, *relevant], type_ignores=[]), str(SOURCE), "exec"), namespace["update_alerts"]


CONDITIONS, UPDATE_ALERTS = notice_conditions()


def alerts_for(*, platform=RAM, feed=False, frame=5000, phase=LCS.off, direction="left", occupied=False, extra_event=None, manager=None):
  class ModelData(dict):
    pass

  cp = car.CarParams.new_message(carFingerprint=platform, enableBsm=feed)
  cs = car.CarState.new_message(leftBlindspot=occupied and direction == "left", rightBlindspot=occupied and direction == "right")
  model = log.ModelDataV2.new_message()
  model.meta.laneChangeState, model.meta.laneChangeDirection = phase, direction
  sm = ModelData(modelV2=model)
  sm.frame = frame
  events, sp = Events(), sp_events.EventsSP()
  me = SimpleNamespace(CP=cp, sm=sm, events=events, events_sp=sp, AM=manager or AlertManager(), enabled=True,
                       personality="standard", is_metric=False,
                       state_machine=SimpleNamespace(current_alert_types=[ET.PERMANENT, ET.WARNING], soft_disable_timer=0))
  exec(CONDITIONS, {**vars(sp_events), "self": me, "CS": cs, "EventName": EVENT,
                    "LaneChangeState": LCS, "LaneChangeDirection": log.LaneChangeDirection, "DT_CTRL": DT_CTRL})
  if extra_event is not None:
    events.add(extra_event)
  UPDATE_ALERTS(me, cs)
  return me.AM.current_alert, events.events, sp.events


class BsmAlertTests(unittest.TestCase):
  def assert_silent(self, alert):
    self.assertEqual(alert.audible_alert, AudibleAlert.none)
    self.assertEqual(alert.visual_alert, VisualAlert.none)
    self.assertEqual(alert.alert_status, AlertStatus.normal)

  def test_startup_scope_and_expiration(self):
    before_end = round(15 / DT_CTRL) - 1
    for frame in (0, before_end):
      alert, _, sp = alerts_for(frame=frame)
      self.assertIn(NOTICE, sp)
      self.assertEqual((alert.alert_text_1, alert.alert_text_2), (TITLE, "Check mirrors before changing lanes"))
      self.assertEqual((alert.priority, alert.event_type), (Priority.LOWER, ET.PERMANENT))
      self.assert_silent(alert)
    _, _, after = alerts_for(frame=before_end + 1)
    self.assertNotIn(NOTICE, after)
    for platform, feed in ((RAM, True), ("RAM_HD_5TH_GEN", False), ("CHRYSLER_PACIFICA_2018", False)):
      with self.subTest(platform=platform, feed=feed):
        _, _, sp = alerts_for(platform=platform, feed=feed, frame=0)
        self.assertNotIn(NOTICE, sp)

  def test_combined_display_keeps_both_directional_prompts(self):
    for direction in ("left", "right"):
      with self.subTest(direction=direction):
        alert, _, sp = alerts_for(phase=LCS.preLaneChange, direction=direction)
        self.assertIn(NOTICE, sp)
        prompt = f"Steer {direction.title()} to Start Lane Change Once Safe"
        self.assertEqual((alert.alert_text_1, alert.alert_text_2), (TITLE, prompt))
        self.assertEqual((alert.priority, alert.alert_size, alert.event_type), (Priority.LOW, AlertSize.mid, ET.WARNING))
        self.assert_silent(alert)
        for platform, feed in ((RAM, True), ("RAM_HD_5TH_GEN", False)):
          stock, _, sp = alerts_for(platform=platform, feed=feed, phase=LCS.preLaneChange, direction=direction)
          self.assertNotIn(NOTICE, sp)
          self.assertEqual((stock.alert_text_1, stock.alert_text_2, stock.alert_size), (prompt, "", AlertSize.small))
          self.assert_silent(stock)

  def test_existing_stock_startup_alert_can_display_first(self):
    manager = AlertManager()
    first, _, _ = alerts_for(frame=0, extra_event=EVENT.startup, manager=manager)
    self.assertEqual(first.alert_type, "startup/permanent")
    for frame in range(1, first.duration + 1):
      winner, _, _ = alerts_for(frame=frame, manager=manager)
      self.assertEqual(winner.alert_type, "startup/permanent")
    next_alert, _, _ = alerts_for(frame=first.duration + 1, manager=manager)
    self.assertEqual(next_alert.alert_text_1, TITLE)

  def test_blocked_lane_and_urgent_warnings_keep_precedence(self):
    for direction in ("left", "right"):
      for feed in (True, False):
        with self.subTest(direction=direction, feed=feed):
          blocked, stock_events, _ = alerts_for(feed=feed, phase=LCS.preLaneChange, direction=direction, occupied=True)
          self.assertEqual(stock_events, [EVENT.laneChangeBlocked])
          self.assertEqual((blocked.alert_text_1, blocked.audible_alert), ("Car Detected in Blindspot", AudibleAlert.prompt))
      urgent, _, _ = alerts_for(phase=LCS.preLaneChange, direction=direction, extra_event=EVENT.fcw)
      self.assertEqual((urgent.alert_text_1, urgent.priority, urgent.audible_alert), ("BRAKE!", Priority.HIGHEST, AudibleAlert.warningSoft))

  def test_starting_and_finishing_keep_the_stock_progress_message(self):
    for phase, notice_expected in ((LCS.laneChangeStarting, True), (LCS.laneChangeFinishing, False)):
      alert, stock, sp = alerts_for(phase=phase)
      self.assertEqual(stock, [EVENT.laneChange])
      self.assertEqual(NOTICE in sp, notice_expected)
      self.assertEqual(alert.alert_text_1, "Changing Lanes")

  def test_notice_has_only_a_display_event_type(self):
    self.assertEqual(set(sp_events.EVENTS_SP[NOTICE]), {ET.PERMANENT})
    names = custom.OnroadEventSP.EventName.schema.enumerants
    self.assertEqual((names["laneTurnRight"], names["rampilotBlindSpotUnavailable"]), (18, 19))

  def test_combined_and_standalone_text_fit(self):
    from PIL import Image, ImageDraw, ImageFont

    font_path = ROOT / "selfdrive/assets/fonts"
    drawer = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    fonts = [ImageFont.truetype(str(font_path / name), size) for name, size in (("Inter-Bold.ttf", 88), ("Inter-SemiBold.ttf", 66))]
    for alert in (alerts_for(frame=0)[0], alerts_for(phase=LCS.preLaneChange, direction="left")[0],
                  alerts_for(phase=LCS.preLaneChange, direction="right")[0]):
      self.assertEqual(alert.alert_size, AlertSize.mid)
      for line, font in zip((alert.alert_text_1, alert.alert_text_2), fonts, strict=True):
        left, _, right, _ = drawer.textbbox((0, 0), line, font)
        self.assertLessEqual(right - left, 2160 - 300, line)


if __name__ == "__main__":
  unittest.main()
