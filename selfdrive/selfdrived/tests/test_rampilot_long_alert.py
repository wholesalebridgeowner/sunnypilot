"""RamPilot long: the "Comma Braking Unavailable" warning, with the real event tables, alert manager and selfdrived code.

The Panda latches stock fallback after 3 refused comma DAS_3 in a row while engaged; the host sees only the Panda's
blocked-transmit counter (pandaStates.safetyTxBlocked, 10 Hz)."""
import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cereal import car, custom, log
from opendbc.car.chrysler.values import ChryslerSafetyFlags
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived import events as sp_events
from openpilot.sunnypilot.selfdrive.selfdrived import rampilot_long_alert as rla
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import AlertSize, AlertStatus, AudibleAlert, ET, Priority, VisualAlert

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "selfdrive/selfdrived/selfdrived.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
EVENT = custom.OnroadEventSP.EventName.rampilotLongBrakingUnavailable
RAM = "RAM_1500_5TH_GEN"
LONG_PARAM = ChryslerSafetyFlags.RAM_DT | ChryslerSafetyFlags.RAMPILOT_LONG
TITLE, BODY = "Comma Braking Unavailable", "Factory Cruise Only"


def selfdrived_code():
  # The production init line and update_events clause, run without starting the process or opening sockets
  cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
  methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
  init = next(n for n in methods["__init__"].body if isinstance(n, ast.Assign) and
              ast.unparse(n.targets[0]) == "self.rampilot_long_monitor")
  clause = next(n for n in methods["update_events"].body if isinstance(n, ast.If) and
                "rampilot_long_monitor" in ast.unparse(n.test))
  namespace = {"ET": ET, "LONGITUDINAL_PERSONALITY_MAP": {"standard": log.LongitudinalPersonality.standard}}
  exec(compile(ast.Module(body=[methods["update_alerts"]], type_ignores=[]), str(SOURCE), "exec"), namespace)
  return (compile(ast.Module(body=[init], type_ignores=[]), str(SOURCE), "exec"),
          compile(ast.Module(body=[clause], type_ignores=[]), str(SOURCE), "exec"), namespace["update_alerts"])


INIT, CLAUSE, UPDATE_ALERTS = selfdrived_code()


def car_params(platform=RAM, long=True, param=LONG_PARAM):
  cp = car.CarParams.new_message(carFingerprint=platform, openpilotLongitudinalControl=long)
  cp.init("safetyConfigs", 1)
  cp.safetyConfigs[0].safetyModel = car.CarParams.SafetyModel.chrysler
  cp.safetyConfigs[0].safetyParam = int(param)
  return cp


class SubMaster(dict):
  def __init__(self, frame, updated, **services):
    super().__init__(**services)
    self.frame, self.updated = frame, updated


class Drive:
  """selfdrived at 100 Hz with pandaStates at 10 Hz; `refusals` adds to the Panda's blocked-transmit counter."""

  def __init__(self, CP=None):
    self.me = SimpleNamespace(CP=CP if CP is not None else car_params(), AM=AlertManager(), personality="standard",
                              is_metric=False)
    exec(INIT, {"self": self.me, "RamPilotLongMonitor": rla.RamPilotLongMonitor,
                "rampilot_long_enabled": rla.rampilot_long_enabled})
    self.frame, self.tx = 0, 0

  def step(self, enabled=True, long_active=True, cruise=True, brake=False, gas=False, refusals=0, emergency=False):
    self.frame += 1
    self.tx += refusals
    panda = log.PandaState.new_message(safetyTxBlocked=self.tx)
    cc = car.CarControl.new_message(enabled=enabled, longActive=long_active)
    cs_sp = custom.CarStateSP.new_message(rampilotFactoryEmergency=emergency)
    me = self.me
    me.sm = SubMaster(self.frame, {"pandaStates": self.frame % 10 == 0}, pandaStates=[panda], carControl=cc, carStateSP=cs_sp)
    me.enabled, me.events, me.events_sp = enabled, Events(), sp_events.EventsSP()
    types = [ET.PERMANENT, ET.WARNING] if enabled else [ET.PERMANENT]
    me.state_machine = SimpleNamespace(current_alert_types=types, soft_disable_timer=0)
    CS = car.CarState.new_message(brakePressed=brake, gasPressed=gas)
    CS.cruiseState.enabled = cruise
    exec(CLAUSE, {"self": me, "CS": CS, "custom": custom})
    UPDATE_ALERTS(me, CS)
    return EVENT in me.events_sp.events

  def run(self, frames, refusal_every=0, **kw):
    """`refusal_every` frames one Panda refusal (2 = 50 Hz, every comma DAS_3); True if the event was ever raised."""
    raised = []
    for _ in range(frames):
      refusals = 1 if refusal_every and self.frame % refusal_every == 0 else 0
      raised.append(self.step(refusals=refusals, **kw))
    return raised


class TestRaise:
  def engaged(self, seconds=1.0):
    d = Drive()
    assert not any(d.run(int(seconds * 100)))
    return d

  def test_latched_fallback_raises_within_0_3_s(self):
    d = self.engaged()
    raised = d.run(30, refusal_every=2)  # every comma DAS_3 refused at 50 Hz
    assert raised[-1]

  def test_a_three_step_rise_raises(self):
    d = self.engaged()
    d.step(refusals=3)
    assert any(d.run(10))  # next pandaStates sample

  @pytest.mark.parametrize("count", [1, 2])
  def test_one_or_two_refusals_do_not(self, count):
    d = self.engaged()
    d.step(refusals=count)
    assert not any(d.run(100))

  def test_spread_out_refusals_do_not(self):
    d = self.engaged()
    assert not any(d.run(500, refusal_every=30))  # one every 0.3 s: never 3 within 0.2 s

  @pytest.mark.parametrize("kw", [{"long_active": False}, {"enabled": False, "long_active": False}, {"cruise": False},
                                  {"brake": True}, {"gas": True, "long_active": False}])
  def test_not_without_active_long_control(self, kw):
    d = Drive()
    assert not any(d.run(300, refusal_every=2, **kw))

  def test_not_in_the_first_0_2_s_of_an_engagement(self):
    d = Drive()
    d.run(100, long_active=False, refusal_every=2)
    assert not any(d.run(15, refusal_every=2))  # refusals before engagement show up late in pandaStates
    assert not any(d.run(100))

  def test_brake_disengagement_does_not_raise(self):
    # the Panda refuses at once on the brake; openpilot drops long control a few frames later
    d = self.engaged()
    raised = d.run(3, brake=True, refusal_every=2) + d.run(100, enabled=False, long_active=False, brake=True, refusal_every=2)
    assert not any(raised)

  def test_not_during_the_factory_emergency_window(self):
    d = self.engaged()
    d.step(emergency=True)
    assert not any(d.run(240, refusal_every=2))  # 2 s window plus latency: refused by design, no latch
    assert any(d.run(60, refusal_every=2))  # after it, refusals count again

  def test_counter_restart(self):
    d = self.engaged()
    d.tx = 0  # Panda reset: the counter starts again
    assert not any(d.run(100))


class TestPersist:
  def raised(self):
    d = Drive()
    d.run(100)
    assert any(d.run(30, refusal_every=2))
    return d

  def test_stays_until_disengaged(self):
    d = self.raised()
    assert all(d.run(300))  # refusals stop mattering: the Panda keeps factory cruise until disengaged
    assert all(d.run(100, long_active=False, gas=True))  # gas override is not a disengagement
    assert not d.step(enabled=False, long_active=False)
    assert not any(d.run(200))  # engaged again: clear until it happens again

  def test_warning_alert_and_one_prompt(self):
    d = self.raised()
    alert = d.me.AM.current_alert
    assert (alert.alert_text_1, alert.alert_text_2) == (TITLE, BODY)
    assert (alert.alert_status, alert.alert_size, alert.priority) == (AlertStatus.userPrompt, AlertSize.mid, Priority.LOW)
    assert (alert.visual_alert, alert.audible_alert, alert.event_type) == (VisualAlert.none, AudibleAlert.prompt, ET.WARNING)
    entry = d.me.AM.alerts[alert.alert_type]
    start = entry.start_frame
    d.run(500)
    assert d.me.AM.current_alert.alert_type == alert.alert_type
    assert entry.start_frame == start  # one continuous alert: the sound starts once
    # soundd plays a prompt once per alert (no repeat)
    soundd = (ROOT / "selfdrive/ui/soundd.py").read_text(encoding="utf-8")
    assert re.search(r"AudibleAlert\.prompt: \(\"prompt\.wav\", 1,", soundd)

  def test_only_a_warning_event_type(self):
    assert set(sp_events.EVENTS_SP[EVENT]) == {ET.WARNING}
    names = custom.OnroadEventSP.EventName.schema.enumerants
    assert (names["rampilotBlindSpotUnavailable"], names["rampilotLongBrakingUnavailable"]) == (19, 20)

  def test_text_fits(self):
    from PIL import Image, ImageDraw, ImageFont
    font_path = ROOT / "selfdrive/assets/fonts"
    drawer = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for line, (name, size) in ((TITLE, ("Inter-Bold.ttf", 88)), (BODY, ("Inter-SemiBold.ttf", 66))):
      left, _, right, _ = drawer.textbbox((0, 0), line, ImageFont.truetype(str(font_path / name), size))
      assert right - left <= 2160 - 300, line


def test_factory_emergency_reaches_the_logged_message():
  from opendbc.car import structs
  from openpilot.selfdrive.car.helpers import convert_to_capnp
  for value in (True, False):
    assert convert_to_capnp(structs.CarStateSP(rampilotFactoryEmergency=value)).rampilotFactoryEmergency is value


class TestScope:
  def test_ram_dt_with_rampilot_long(self):
    assert rla.rampilot_long_enabled(car_params())
    assert Drive().me.rampilot_long_monitor is not None

  @pytest.mark.parametrize("cp", [car_params(long=False), car_params(param=ChryslerSafetyFlags.RAM_DT),
                                  car_params(platform="RAM_HD_5TH_GEN"), car_params(platform="CHRYSLER_PACIFICA_2020", param=0),
                                  car_params(platform="TOYOTA_RAV4", param=0)],
                           ids=["ram-dt-long-off", "ram-dt-no-safety-bit", "ram-hd", "pacifica", "other-brand"])
  def test_nothing_for_other_cars_or_alpha_long_off(self, cp):
    assert not rla.rampilot_long_enabled(cp)
    d = Drive(cp)
    assert d.me.rampilot_long_monitor is None
    assert not any(d.run(100)) and not any(d.run(100, refusal_every=2))

  def test_selfdrived_reads_carstatesp_only_for_rampilot_long_and_never_checks_it(self):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
    init = ast.unparse(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"))
    assert "rampilot_long_services = ['carStateSP'] if self.rampilot_long_monitor is not None else []" in init
    assert "ignore += rampilot_long_services" in init
    assert "self.gps_packets + rampilot_long_services" in init
