from opendbc.can import CANPacker
from opendbc.car import Bus, DT_CTRL, structs
from opendbc.car.lateral import apply_meas_steer_torque_limits
from opendbc.car.chrysler import chryslercan, rampilot_long
from opendbc.car.chrysler.values import RAM_CARS, CarControllerParams, ChryslerFlags
from opendbc.car.interfaces import CarControllerBase

from opendbc.sunnypilot.car.chrysler.carcontroller_ext import CarControllerExt
from opendbc.sunnypilot.car.chrysler.mads import MadsCarController

ButtonType = structs.CarState.ButtonEvent.Type


class CarController(CarControllerBase, MadsCarController, CarControllerExt):
  def __init__(self, dbc_names, CP, CP_SP):
    CarControllerBase.__init__(self, dbc_names, CP, CP_SP)
    MadsCarController.__init__(self)
    CarControllerExt.__init__(self, CP, CP_SP)
    self.apply_torque_last = 0

    self.hud_count = 0
    self.last_lkas_falling_edge = 0
    self.lkas_control_bit_prev = False
    self.last_button_frame = 0

    # RamPilot long
    self.das_3_count_sent = 0  # factory DAS_3 frames already answered
    self.das_3_counter_last: int | None = None  # factory COUNTER of the last copy sent
    self.das_3_frame_last = 0  # controller frame of the last copy sent
    self.long_state = rampilot_long.LongState()
    self.long_confirm = False  # driver's resume/accel button press or new gas press since the last decision
    self.gas_pressed_prev = False
    self.accel = 0.  # braking the comma applied in the last copy

    self.packer = CANPacker(dbc_names[Bus.pt])
    self.params = CarControllerParams(CP)

  def update(self, CC, CC_SP, CS, now_nanos):
    MadsCarController.update(self, CC, CC_SP, CS)
    can_sends = []

    lkas_active = CC.latActive and self.lkas_control_bit_prev

    # cruise buttons
    if (self.frame - self.last_button_frame) * DT_CTRL > 0.05:
      das_bus = 2 if self.CP.carFingerprint in RAM_CARS else 0

      # ACC cancellation
      if CC.cruiseControl.cancel:
        self.last_button_frame = self.frame
        can_sends.append(chryslercan.create_cruise_buttons(self.packer, CS.button_counter + 1, das_bus, cancel=True))

      # ACC resume from standstill
      elif CC.cruiseControl.resume:
        self.last_button_frame = self.frame
        can_sends.append(chryslercan.create_cruise_buttons(self.packer, CS.button_counter + 1, das_bus, resume=True))

    # HUD alerts
    if self.frame % 25 == 0:
      if CS.lkas_car_model != -1:
        can_sends.append(chryslercan.create_lkas_hud(self.packer, self.CP, lkas_active, CC.hudControl.visualAlert,
                                                     self.hud_count, CS.lkas_car_model, CS.auto_high_beam, self.mads))
        self.hud_count += 1

    # steering
    if self.frame % self.params.STEER_STEP == 0:

      # TODO: can we make this more sane? why is it different for all the cars?
      lkas_control_bit = self.lkas_control_bit_prev
      if CS.out.vEgo > self.CP.minSteerSpeed:
        lkas_control_bit = True
      elif self.CP.flags & ChryslerFlags.HIGHER_MIN_STEERING_SPEED:
        if CS.out.vEgo < (self.CP.minSteerSpeed - 3.0):
          lkas_control_bit = False
      elif self.CP.carFingerprint in RAM_CARS:
        if CS.out.vEgo < (self.CP.minSteerSpeed - 0.5):
          lkas_control_bit = False

      lkas_control_bit = CarControllerExt.get_lkas_control_bit(self, CS, CC, lkas_control_bit, self.lkas_control_bit_prev)

      # EPS faults if LKAS re-enables too quickly
      lkas_control_bit = lkas_control_bit and (self.frame - self.last_lkas_falling_edge > 200)

      if not lkas_control_bit and self.lkas_control_bit_prev:
        self.last_lkas_falling_edge = self.frame
      self.lkas_control_bit_prev = lkas_control_bit

      # steer torque
      new_torque = int(round(CC.actuators.torque * self.params.STEER_MAX))
      apply_torque = apply_meas_steer_torque_limits(new_torque, self.apply_torque_last, CS.out.steeringTorqueEps, self.params)
      if not lkas_active or not lkas_control_bit:
        apply_torque = 0
      self.apply_torque_last = apply_torque

      can_sends.append(chryslercan.create_lkas_command(self.packer, self.CP, int(apply_torque), lkas_control_bit))

    if self.frame % 10 == 0 and self.CP.carFingerprint not in RAM_CARS:
      can_sends.append(MadsCarController.create_lkas_heartbit(self.packer, CS.lkas_heartbit, self.mads))

    # RamPilot long (Ram DT): one DAS_3 on bus 0 for each factory DAS_3 (50 Hz), each built from its own factory frame;
    # the comma only adds braking (rampilot_long.py). A copy of a factory frame that requests braking or brake prep is
    # still sent (an unchanged copy, which the Panda refuses while it forwards the factory frame): every attempt keeps
    # the Panda's comma-sending timer fresh, so the next frame without factory braking is blocked and answered at once.
    if self.CP.openpilotLongitudinalControl:
      # A driver confirm is a fresh action: a resume/accel button press (not the release of a button already held) or the
      # start of a gas press. It can come in a frame without a new factory DAS_3, so it is kept for the next decision,
      # which uses it up; a confirm used up by the decision that starts a hold is ignored (rampilot_long.py).
      gas_press = CS.out.gasPressed and not self.gas_pressed_prev
      self.gas_pressed_prev = CS.out.gasPressed
      self.long_confirm |= gas_press or any(be.pressed and be.type in (ButtonType.resumeCruise, ButtonType.accelCruise)
                                            for be in CS.out.buttonEvents)

      if CS.das_3_count != self.das_3_count_sent:
        self.das_3_count_sent = CS.das_3_count
        stopped = CS.out.standstill or CS.out.vEgo < rampilot_long.STOPPED_SPEED
        # the last two factory frames of this update, oldest first; older ones are skipped
        for das_3, das_3_raw in CS.das_3_frames[-rampilot_long.COPIES_PER_UPDATE:]:
          counter = int(das_3["COUNTER"])
          # never the same factory COUNTER twice, never an older one after a newer one
          if not rampilot_long.counter_moves_forward(counter, self.das_3_counter_last, self.frame - self.das_3_frame_last):
            continue
          self.das_3_counter_last, self.das_3_frame_last = counter, self.frame
          inputs = rampilot_long.StandstillInputs(stopped=stopped, confirm=self.long_confirm, gas_pressed=CS.out.gasPressed)
          self.long_confirm = False
          overrides, self.long_state = rampilot_long.das_3_overrides(das_3, CC.longActive, CC.actuators.accel,
                                                                     self.long_state, inputs)
          copy = chryslercan.create_das_3(self.packer, 0, das_3, das_3_raw, overrides)
          can_sends.append(copy)
          self.accel = float(overrides.get("ACC_DECEL", 0.))
          if CS.das_3_delivery is not None:
            CS.das_3_delivery.copy_sent(bytes(copy[1]), added_braking=bool(overrides))  # checked against the bus-0 echo

    self.frame += 1

    new_actuators = CC.actuators.as_builder()
    new_actuators.torque = self.apply_torque_last / self.params.STEER_MAX
    new_actuators.torqueOutputCan = self.apply_torque_last
    if self.CP.openpilotLongitudinalControl:
      new_actuators.accel = self.accel

    return new_actuators, can_sends
