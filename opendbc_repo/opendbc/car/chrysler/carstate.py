from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.chrysler.ram_dt_diesel import DieselReadouts, supports_diesel
from opendbc.car.chrysler.values import DBC, STEER_THRESHOLD, RAM_CARS, RAM_DT
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase

from opendbc.sunnypilot.car.chrysler.mads import MadsCarState

ButtonType = structs.CarState.ButtonEvent.Type

# Observed on the US-configured Ram DT. Unconfirmed codes (including 0x04/0x0D)
# and unavailable codes 0x00/0x3F leave the display blank.
SPEED_LIMIT_MPH = {0x05: 25, 0x06: 30, 0x07: 35, 0x08: 40, 0x09: 45, 0x0A: 50, 0x0B: 55, 0x0C: 60, 0x0E: 70}
SPEED_LIMIT_FRESH_NS = 1_500_000_000


class CarState(CarStateBase, MadsCarState):
  def __init__(self, CP, CP_SP):
    CarStateBase.__init__(self, CP, CP_SP)
    MadsCarState.__init__(self, CP, CP_SP)
    self.CP = CP
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])

    self.auto_high_beam = 0
    self.button_counter = 0
    self.lkas_car_model = -1

    if CP.carFingerprint in RAM_CARS:
      self.shifter_values = can_define.dv["Transmission_Status"]["Gear_State"]
    else:
      self.shifter_values = can_define.dv["GEAR"]["PRNDL"]

    self.distance_button = 0
    self.limit_sample_ns: int | None = None
    self.display_clock_ns = 0
    self.display_now_ns: int | None = None
    self.limit_mps = 0.0
    self.limit_fresh = False
    self.diesel = DieselReadouts() if supports_diesel(CP) else None
    if CP.carFingerprint in RAM_DT:
      self.observe_can = self.observe_display_data

  def observe_display_data(self, packets, now_nanos, packet_valid):
    # In replay, CAN logMonoTime supplies the publication clock. An empty replay
    # tick has no recording time; withhold until a timestamp is available again.
    now = now_nanos if now_nanos is not None else max((stamp for stamp, _ in packets), default=None)
    self.limit_fresh = False
    self.display_now_ns = None
    if now is None or now < self.display_clock_ns:
      return
    self.display_now_ns = self.display_clock_ns = now
    diesel_ids = self.diesel.addresses if self.diesel is not None else ()
    for i, (stamp, frames) in enumerate(packets):
      if stamp < 0 or stamp > now:
        continue
      valid = None
      for address, data, bus in frames:
        if bus != 0 or (address != 0x265 and address not in diesel_ids):
          continue
        if valid is None and packet_valid is not None:
          valid = packet_valid(i)
        if address == 0x265:
          if self.limit_sample_ns is not None and stamp < self.limit_sample_ns:
            continue
          self.limit_sample_ns = stamp
          mph = SPEED_LIMIT_MPH.get(data[5], 0) if (packet_valid is None or valid) and len(data) == 8 else 0
          self.limit_mps = mph * CV.MPH_TO_MS
        else:
          self.diesel.observe(address, data, stamp, bool(valid))
    self.limit_fresh = self.limit_sample_ns is not None and now - self.limit_sample_ns <= SPEED_LIMIT_FRESH_NS

  def update(self, can_parsers) -> tuple[structs.CarState, structs.CarStateSP]:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    ret = structs.CarState()
    ret_sp = structs.CarStateSP()
    if self.limit_fresh and self.limit_mps > 0:
      ret_sp.speedLimit = self.limit_mps
      ret_sp.speedLimitValid = True
    if self.diesel is not None:
      self.diesel.publish(ret_sp, self.display_now_ns)

    prev_distance_button = self.distance_button
    self.distance_button = cp.vl["CRUISE_BUTTONS"]["ACC_Distance_Dec"]

    # lock info
    ret.doorOpen = any([cp.vl["BCM_1"]["DOOR_OPEN_FL"],
                        cp.vl["BCM_1"]["DOOR_OPEN_FR"],
                        cp.vl["BCM_1"]["DOOR_OPEN_RL"],
                        cp.vl["BCM_1"]["DOOR_OPEN_RR"]])
    ret.seatbeltUnlatched = cp.vl["ORC_1"]["SEATBELT_DRIVER_UNLATCHED"] == 1

    # brake pedal
    ret.brake = 0
    ret.brakePressed = cp.vl["ESP_1"]['Brake_Pedal_State'] == 1  # Physical brake pedal switch

    # gas pedal
    ret.gasPressed = cp.vl["ECM_5"]["Accelerator_Position"] > 1e-5

    # car speed
    if self.CP.carFingerprint in RAM_CARS:
      ret.vEgoRaw = cp.vl["ESP_8"]["Vehicle_Speed"] * CV.KPH_TO_MS
      ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(cp.vl["Transmission_Status"]["Gear_State"], None))
    else:
      ret.vEgoRaw = (cp.vl["SPEED_1"]["SPEED_LEFT"] + cp.vl["SPEED_1"]["SPEED_RIGHT"]) / 2.
      ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(cp.vl["GEAR"]["PRNDL"], None))
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.standstill = not ret.vEgoRaw > 0.001

    # button presses
    ret.leftBlinker, ret.rightBlinker = self.update_blinker_from_stalk(200, cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 1,
                                                                       cp.vl["STEERING_LEVERS"]["TURN_SIGNALS"] == 2)
    ret.genericToggle = cp.vl["STEERING_LEVERS"]["HIGH_BEAM_PRESSED"] == 1

    # steering wheel
    ret.steeringAngleDeg = cp.vl["STEERING"]["STEERING_ANGLE"] + cp.vl["STEERING"]["STEERING_ANGLE_HP"]
    ret.steeringRateDeg = cp.vl["STEERING"]["STEERING_RATE"]
    ret.steeringTorque = cp.vl["EPS_2"]["COLUMN_TORQUE"]
    ret.steeringTorqueEps = cp.vl["EPS_2"]["EPS_TORQUE_MOTOR"]
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD

    # cruise state
    cp_cruise = cp_cam if self.CP.carFingerprint in RAM_CARS else cp

    ret.cruiseState.available = cp_cruise.vl["DAS_3"]["ACC_AVAILABLE"] == 1
    ret.cruiseState.enabled = cp_cruise.vl["DAS_3"]["ACC_ACTIVE"] == 1
    ret.cruiseState.speed = cp_cruise.vl["DAS_4"]["ACC_SET_SPEED_KPH"] * CV.KPH_TO_MS
    ret.cruiseState.nonAdaptive = cp_cruise.vl["DAS_4"]["ACC_STATE"] in (1, 2)  # 1 NormalCCOn and 2 NormalCCSet
    ret.cruiseState.standstill = cp_cruise.vl["DAS_3"]["ACC_STANDSTILL"] == 1
    ret.accFaulted = cp_cruise.vl["DAS_3"]["ACC_FAULTED"] != 0

    if self.CP.carFingerprint in RAM_CARS:
      # Auto High Beam isn't Located in this message on chrysler or jeep currently located in 729 message
      self.auto_high_beam = cp_cam.vl["DAS_6"]['AUTO_HIGH_BEAM_ON']
      ret.steerFaultTemporary = cp.vl["EPS_3"]["DASM_FAULT"] == 1
    else:
      ret.steerFaultTemporary = cp.vl["EPS_2"]["LKAS_TEMPORARY_FAULT"] == 1
      ret.steerFaultPermanent = cp.vl["EPS_2"]["LKAS_STATE"] == 4

    # blindspot sensors
    if self.CP.enableBsm:
      ret.leftBlindspot = cp.vl["BSM_1"]["LEFT_STATUS"] == 1
      ret.rightBlindspot = cp.vl["BSM_1"]["RIGHT_STATUS"] == 1

    self.lkas_car_model = cp_cam.vl["DAS_6"]["CAR_MODEL"]
    self.button_counter = cp.vl["CRUISE_BUTTONS"]["COUNTER"]

    MadsCarState.update_mads(self, ret, can_parsers)

    ret.buttonEvents = [
      *create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise}),
      *create_button_events(self.lkas_button, self.prev_lkas_button, {1: ButtonType.lkas}),
    ]

    return ret, ret_sp

  @staticmethod
  def get_can_parsers(CP, CP_SP):
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }
