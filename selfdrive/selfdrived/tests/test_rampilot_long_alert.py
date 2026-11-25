"""RamPilot long: the "Comma Braking Unavailable" warning, with the real event tables, alert manager and selfdrived code.

The warning follows what reached the truck: CarState compares the Panda's bus-0 DAS_3 echoes with the comma's copies
and publishes carStateSP.rampilotBrakingNotDelivered and rampilotDas3EchoAgeMs. Also: what the driver gets when the
Panda drops controls_allowed while engaged."""
import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cereal import car, custom, log
from openpilot.common.realtime import DT_CTRL
from opendbc.car.chrysler.values import ChryslerSafetyFlags
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.sunnypilot.mads.state import StateMachine as MadsStateMachine
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
  def __init__(self, frame, **services):
    super().__init__(**services)
    self.frame = frame


class Drive:
  """selfdrived at 100 Hz, fed carControl and the RamPilot long carStateSP fields."""

  def __init__(self, CP=None):
    self.me = SimpleNamespace(CP=CP if CP is not None else car_params(), AM=AlertManager(), personality="standard",
                              is_metric=False)
    exec(INIT, {"self": self.me, "RamPilotLongMonitor": rla.RamPilotLongMonitor,
                "rampilot_long_enabled": rla.rampilot_long_enabled})
    self.frame = 0

  def step(self, enabled=True, long_active=True, cruise=True, brake=False, gas=False, not_delivered=0, echo_age=20.,
           since_echo=1, emergency=False):
    self.frame += 1
    cc = car.CarControl.new_message(enabled=enabled, longActive=long_active)
    cs_sp = custom.CarStateSP.new_message(rampilotFactoryEmergency=emergency, rampilotBrakingNotDelivered=not_delivered,
                                          rampilotDas3EchoAgeMs=echo_age, rampilotFactorySinceEcho=since_echo)
    me = self.me
    me.sm = SubMaster(self.frame, carControl=cc, carStateSP=cs_sp)
    me.enabled, me.events, me.events_sp = enabled, Events(), sp_events.EventsSP()
    types = [ET.PERMANENT, ET.WARNING] if enabled else [ET.PERMANENT]
    me.state_machine = SimpleNamespace(current_alert_types=types, soft_disable_timer=0)
    CS = car.CarState.new_message(brakePressed=brake, gasPressed=gas)
    CS.cruiseState.enabled = cruise
    exec(CLAUSE, {"self": me, "CS": CS, "custom": custom})
    UPDATE_ALERTS(me, CS)
    return EVENT in me.events_sp.events

  def run(self, frames, **kw):
    return [self.step(**kw) for _ in range(frames)]


class TestRaise:
  def engaged(self, seconds=1.0):
    d = Drive()
    assert not any(d.run(int(seconds * 100)))
    return d

  def test_three_frames_of_added_braking_not_delivered(self):
    d = self.engaged()
    assert d.step(not_delivered=3)
    assert d.me.rampilot_long_monitor.reason == "not delivered"

  def test_two_do_not(self):
    d = self.engaged()
    assert not any(d.run(300, not_delivered=2))

  def test_no_echo_for_100_ms(self):
    d = self.engaged()
    assert not any(d.run(100, echo_age=99.9, since_echo=5))
    assert d.step(echo_age=100., since_echo=5)
    assert d.me.rampilot_long_monitor.reason == "no echo"

  def test_no_echo_during_a_host_receive_pause_does_not(self):
    d = self.engaged()
    # no factory frames either since the last echo, or fewer than 3: the host is not receiving, not the truck
    assert not any(d.run(300, echo_age=500., since_echo=0) + d.run(300, echo_age=500., since_echo=2))

  def test_not_delivered_is_ignored_in_the_factory_emergency_window_but_a_silent_bus_is_not(self):
    d = self.engaged()
    d.step(emergency=True)
    assert not any(d.run(250, not_delivered=10))  # 2 s window plus latency: the Panda passes factory frames
    assert d.step(not_delivered=10)  # after it, they count again
    d = self.engaged()
    d.step(emergency=True)
    assert d.step(echo_age=150., since_echo=7)  # nothing reaching the truck matters in the window too

  @pytest.mark.parametrize("kw", [{"long_active": False}, {"enabled": False, "long_active": False}, {"cruise": False},
                                  {"brake": True}, {"gas": True, "long_active": False}])
  def test_not_without_active_long_control(self, kw):
    d = Drive()
    assert not any(d.run(300, not_delivered=10, echo_age=500., since_echo=25, **kw))

  def test_not_in_the_first_0_2_s_of_an_engagement(self):
    d = Drive()
    d.run(50, long_active=False, not_delivered=10, echo_age=500., since_echo=25)
    assert not any(d.run(20, not_delivered=10, echo_age=500., since_echo=25))
    assert d.step(not_delivered=10, echo_age=500., since_echo=25)

  def test_brake_disengagement_does_not_raise(self):
    d = self.engaged()
    raised = d.run(3, brake=True, not_delivered=5) + d.run(100, enabled=False, long_active=False, brake=True, not_delivered=5)
    assert not any(raised)

  def test_panda_transmit_counter_is_no_longer_read(self):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
    assert "safetyTxBlocked" not in ast.unparse(cls)


class TestPersist:
  def raised(self):
    d = Drive()
    d.run(100)
    assert d.step(not_delivered=3)
    return d

  def test_stays_until_disengaged(self):
    d = self.raised()
    assert all(d.run(300))  # stays: the comma's braking is not trusted again until disengaged
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


def test_rampilot_long_fields_reach_the_logged_message():
  from opendbc.car import structs
  from openpilot.selfdrive.car.helpers import convert_to_capnp
  msg = convert_to_capnp(structs.CarStateSP(rampilotFactoryEmergency=True, rampilotDas3EchoAgeMs=120.,
                                            rampilotBrakingNotDelivered=4, rampilotFactorySinceEcho=6))
  assert (msg.rampilotFactoryEmergency, msg.rampilotDas3EchoAgeMs, msg.rampilotBrakingNotDelivered,
          msg.rampilotFactorySinceEcho) == (True, 120., 4, 6)


class TruckAndHost:
  """End to end: the real Ram DT CarState (alpha long on) at 100 Hz, factory DAS_3 on bus 2 at 50 Hz, the Panda's bus-0
  echoes, and selfdrived's warning clause reading the resulting carStateSP."""

  def __init__(self):
    from opendbc.can import CANPacker
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.chrysler.interface import CarInterface
    fp = gen_empty_fingerprint()
    CP = CarInterface.get_params("RAM_1500_5TH_GEN", fp, [], alpha_long=True, is_release=False, docs=False)
    self.CI = CarInterface(CP, CarInterface.get_params_sp(CP, "RAM_1500_5TH_GEN", fp, [], alpha_long=True, docs=False))
    self.packer = CANPacker("chrysler_ram_dt_generated")
    self.drive = Drive()
    self.t, self.counter, self.queued = 0, 0, []

  def factory_frame(self, **extra):
    values = {"ACC_AVAILABLE": 1, "ACC_ACTIVE": 1, "ENGINE_TORQUE_REQUEST": 100., "ENGINE_TORQUE_REQUEST_MAX": 1,
              "ACC_DECEL": 4.0, "COUNTER": self.counter, **extra}
    self.counter = (self.counter + 1) % 16
    return self.packer.make_can_msg("DAS_3", 2, values)[1]

  def tick(self, echo=True, host="receiving", **factory):
    """10 ms. host: "receiving" (frames delivered now), "paused" (frames queue up and card is blocked), "idle" (card
    runs with no frames: nothing arrives at all). `factory`: extra factory DAS_3 values. Returns whether the warning is
    raised."""
    from opendbc.car import CanData
    self.t += 10_000_000
    frames = []
    if self.t % 20_000_000 == 0 and host != "idle":
      dat = self.factory_frame(**factory)
      frames.append(CanData(0x99, dat, 2))
      if echo:
        frames.append(CanData(0x99, dat, 128))  # forwarded to the truck and returned to the host
    if host == "paused":
      self.queued += frames
    else:
      self.CI.update([(self.t, self.queued + frames)], now_nanos=self.t)  # a catch-up batch after a pause
      self.queued = []
    sp, cs = self.CI.CS.out_sp, self.CI.CS.out
    return self.drive.step(cruise=cs.cruiseState.enabled, not_delivered=sp.rampilotBrakingNotDelivered,
                           echo_age=sp.rampilotDas3EchoAgeMs, since_echo=sp.rampilotFactorySinceEcho,
                           emergency=sp.rampilotFactoryEmergency)

  def run(self, ms, **kw):
    return [self.tick(**kw) for _ in range(ms // 10)]


class TestEndToEnd:
  def test_normal_driving_does_not_raise(self):
    th = TruckAndHost()
    assert not any(th.run(3000))

  @pytest.mark.parametrize("host", ["paused", "idle"])
  def test_154_ms_host_receive_pause_does_not_raise(self, host):
    th = TruckAndHost()
    assert not any(th.run(1000))
    assert not any(th.run(154, host=host))  # recorded maximum: nothing reaches the host, factory or echo
    assert not any(th.run(1000))  # catch-up batch with the factory frames and their echoes, then normal

  @pytest.mark.parametrize("factory, emergency", [
    ({"ACC_BRK_PREP": 1}, True), ({"COLLISION_BRK_PREP": 1}, True),
    ({"ACC_DECEL_REQ": 1, "ACC_DECEL": 2763 * 0.004885 - 16.}, True),  # raw 2763: stronger than -2.5 m/s^2
    ({"ACC_DECEL_REQ": 1, "ACC_DECEL": 2764 * 0.004885 - 16.}, False),
    ({"ACC_DECEL_REQ": 1, "ACC_DECEL": -2.16}, False)])
  def test_factory_emergency_frames_start_the_suppression_window(self, factory, emergency):
    th = TruckAndHost()
    assert not any(th.run(1000))
    th.run(20, **factory)  # one factory frame with these values
    monitor = th.drive.me.rampilot_long_monitor
    assert (monitor.emergency_frame is not None) == emergency
    if emergency:
      assert th.drive.frame - monitor.emergency_frame <= 1  # the window starts with that frame

  def test_factory_frames_without_echoes_for_100_ms_raise(self):
    th = TruckAndHost()
    assert not any(th.run(1000))
    raised = th.run(200, echo=False)  # the host receives factory frames; nothing is transmitted to the truck
    assert raised.index(True) == 9  # at 100 ms without an echo (5 factory frames since the last one)
    assert th.drive.me.rampilot_long_monitor.reason == "no echo"


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
    assert not any(d.run(100)) and not any(d.run(100, not_delivered=10, echo_age=500., since_echo=25))

  def test_selfdrived_reads_carstatesp_only_for_rampilot_long_and_never_checks_it(self):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
    init = ast.unparse(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"))
    assert "rampilot_long_services = ['carStateSP'] if self.rampilot_long_monitor is not None else []" in init
    assert "ignore += rampilot_long_services" in init
    assert "self.gps_packets + rampilot_long_services" in init


def selfdrived_mismatch_code():
  cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "SelfdriveD")
  methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
  counter = [n for n in methods["data_sample"].body if isinstance(n, ast.If) and "mismatch_counter" in ast.unparse(n)]
  check = next(n for n in methods["update_events"].body if isinstance(n, ast.For) and "pandaStates" in ast.unparse(n.iter))
  return (compile(ast.Module(body=counter, type_ignores=[]), str(SOURCE), "exec"),
          compile(ast.Module(body=[check], type_ignores=[]), str(SOURCE), "exec"))


def test_panda_controls_drop_while_engaged():
  """The Panda drops controls_allowed (e.g. on a fallback latch) while openpilot is engaged, with MADS lateral on."""
  counter_code, check_code = selfdrived_mismatch_code()
  panda = log.PandaState.new_message(safetyModel=car.CarParams.SafetyModel.chrysler, safetyParam=int(LONG_PARAM),
                                     controlsAllowed=False)
  me = SimpleNamespace(CP=car_params(), sm=SubMaster(2000, pandaStates=[panda]), events=Events(),
                       events_sp=sp_events.EventsSP(), state_machine=StateMachine(), mismatch_counter=0, AM=AlertManager(),
                       personality="standard", is_metric=False)
  mads = MadsStateMachine(SimpleNamespace(selfdrive=me))
  me.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
  mads.state = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState.enabled
  namespace = {"self": me, "IGNORED_SAFETY_MODES": (car.CarParams.SafetyModel.silent, car.CarParams.SafetyModel.noOutput),
               "DT_CTRL": DT_CTRL, "EventName": log.OnroadEvent.EventName, "log": log}
  me.enabled = True
  frames = 0
  while me.enabled:
    frames += 1
    me.events.clear()
    me.events_sp.clear()
    exec(counter_code, namespace)
    exec(check_code, namespace)
    me.enabled, _ = me.state_machine.update(me.events)
    mads_enabled, mads_active = mads.update()
    UPDATE_ALERTS(me, None)
    assert frames <= 300
  assert frames == 200  # 2.0 s of the Panda disagreeing
  assert me.events.names == [log.OnroadEvent.EventName.controlsMismatch]
  assert not mads_enabled and not mads_active  # MADS lateral disengages too
  alert = me.AM.current_alert
  assert (alert.alert_text_1, alert.alert_text_2) == ("TAKE CONTROL IMMEDIATELY", "Controls Mismatch")
  assert (alert.alert_status, alert.alert_size, alert.priority) == (AlertStatus.critical, AlertSize.full, Priority.HIGHEST)
  assert (alert.visual_alert, alert.audible_alert, alert.event_type) == (VisualAlert.steerRequired,
                                                                         AudibleAlert.warningImmediate, ET.IMMEDIATE_DISABLE)
  assert alert.duration == 400  # shown for at least 4 s
  # then openpilot cancels factory cruise: controlsd asks to cancel while the PCM is on and openpilot is disengaged
  controlsd = (ROOT / "selfdrive/controls/controlsd.py").read_text(encoding="utf-8")
  assert "CC.cruiseControl.cancel = CS.cruiseState.enabled and (not CC.enabled or not self.CP.pcmCruise)" in controlsd
