#!/usr/bin/env python3
import unittest

from opendbc.can.packer import set_value
from opendbc.car.chrysler.values import ChryslerSafetyFlags
from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import CANPackerPanda

# DAS_3 raw values
ACC_DECEL_MIN = 2558       # -3.5 m/s^2
ACC_DECEL_EMERGENCY = 2764  # -2.5 m/s^2: a stronger factory request opens the emergency window
ACC_DECEL_FACTORY_HARDEST = 2834  # -2.16 m/s^2, the factory's hardest request in 9.9 h of recorded drives
ACC_DECEL_INACTIVE = 4094  # +4.0 m/s^2
ACC_DECEL_ZERO = 3275      # -0.002 m/s^2: the weakest deceleration the comma may request on its own
TORQUE_INACTIVE = 2000     # 0 Nm
STOCK_MAX_AGE = 40000       # us, newest factory frame
STOCK_PREV_MAX_AGE = 60000  # us, the factory frame before it
STOCK_PERIOD = 20000        # us, factory DAS_3 at 50 Hz
EMERGENCY_TIME = 2000000    # us, factory frames pass directly after a factory emergency frame
STOCK_EXPIRY = 100000      # us, a stored factory frame older than this is forgotten
SETTLE = EMERGENCY_TIME + 1  # us, a gap that closes any emergency window and leaves only the newest factory frame fresh

# DAS_3 signals the comma may change; every other bit in bytes 0-6 is copied from the factory frame
DAS_3_CHANGEABLE = ("ENGINE_TORQUE_REQUEST", "ENGINE_TORQUE_REQUEST_MAX", "ACC_DECEL", "ACC_DECEL_REQ")
DAS_3_COPIED = ("ACC_AVAILABLE", "ACC_ACTIVE", "ACC_FAULTED", "COLLISION_BRK_PREP", "ACC_BRK_PREP", "GR_MAX_REQ",
                "DISABLE_FUEL_SHUTOFF", "ACC_STANDSTILL", "ACC_GO")


class TestChryslerSafety(common.PandaCarSafetyTest, common.MotorTorqueSteeringSafetyTest):
  TX_MSGS = [[0x23B, 0], [0x292, 0], [0x2A6, 0], [0x2D9, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x292, 0x2A6, 0x2D9)}
  FWD_BLACKLISTED_ADDRS = {2: [0x292, 0x2A6, 0x2D9]}

  MAX_RATE_UP = 3
  MAX_RATE_DOWN = 3
  MAX_TORQUE_LOOKUP = [0], [261]
  MAX_RT_DELTA = 112
  MAX_TORQUE_ERROR = 80

  LKAS_ACTIVE_VALUE = 1

  DAS_BUS = 0

  def setUp(self):
    self.packer = CANPackerPanda("chrysler_pacifica_2017_hybrid_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.chrysler, 0)
    self.safety.init_tests()

  def _button_msg(self, cancel=False, resume=False):
    values = {"ACC_Cancel": cancel, "ACC_Resume": resume}
    return self.packer.make_can_msg_panda("CRUISE_BUTTONS", self.DAS_BUS, values)

  def _pcm_status_msg(self, enable):
    values = {"ACC_ACTIVE": enable}
    return self.packer.make_can_msg_panda("DAS_3", self.DAS_BUS, values)

  def _speed_msg(self, speed):
    values = {"SPEED_LEFT": speed, "SPEED_RIGHT": speed}
    return self.packer.make_can_msg_panda("SPEED_1", 0, values)

  def _user_gas_msg(self, gas):
    values = {"Accelerator_Position": gas}
    return self.packer.make_can_msg_panda("ECM_5", 0, values)

  def _user_brake_msg(self, brake):
    values = {"Brake_Pedal_State": 1 if brake else 0}
    return self.packer.make_can_msg_panda("ESP_1", 0, values)

  def _torque_meas_msg(self, torque):
    values = {"EPS_TORQUE_MOTOR": torque}
    return self.packer.make_can_msg_panda("EPS_2", 0, values)

  def _torque_cmd_msg(self, torque, steer_req=1):
    values = {"STEERING_TORQUE": torque, "LKAS_CONTROL_BIT": self.LKAS_ACTIVE_VALUE if steer_req else 0}
    return self.packer.make_can_msg_panda("LKAS_COMMAND", 0, values)

  def test_buttons(self):
    for controls_allowed in (True, False):
      self.safety.set_controls_allowed(controls_allowed)

      # resume only while controls allowed
      self.assertEqual(controls_allowed, self._tx(self._button_msg(resume=True)))

      # can always cancel
      self.assertTrue(self._tx(self._button_msg(cancel=True)))

      # only one button at a time
      self.assertFalse(self._tx(self._button_msg(cancel=True, resume=True)))
      self.assertFalse(self._tx(self._button_msg(cancel=False, resume=False)))

  def _lkas_button_msg(self, enabled):
    values = {"TOGGLE_LKAS": enabled}
    return self.packer.make_can_msg_panda("TRACTION_BUTTON", 0, values)


class TestChryslerRamDTSafety(TestChryslerSafety):
  TX_MSGS = [[0xB1, 2], [0xA6, 0], [0xFA, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0xA6, 0xFA)}
  FWD_BLACKLISTED_ADDRS = {2: [0xA6, 0xFA]}

  MAX_RATE_UP = 6
  MAX_RATE_DOWN = 6
  MAX_TORQUE_LOOKUP = [0], [350]

  DAS_BUS = 2

  LKAS_ACTIVE_VALUE = 2

  def setUp(self):
    self.packer = CANPackerPanda("chrysler_ram_dt_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.chrysler, ChryslerSafetyFlags.RAM_DT)
    self.safety.init_tests()

  def _speed_msg(self, speed):
    values = {"Vehicle_Speed": speed}
    return self.packer.make_can_msg_panda("ESP_8", 0, values)

  def _lkas_button_msg(self, enabled):
    values = {"LKAS_Button": enabled}
    return self.packer.make_can_msg_panda("Center_Stack_2", 0, values)


class TestChryslerRamHDSafety(TestChryslerSafety):
  TX_MSGS = [[0x275, 0], [0x276, 0], [0x23A, 2]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x276, 0x275)}
  FWD_BLACKLISTED_ADDRS = {2: [0x275, 0x276]}

  MAX_TORQUE_LOOKUP = [0], [361]
  MAX_RATE_UP = 14
  MAX_RATE_DOWN = 14
  MAX_RT_DELTA = 182

  DAS_BUS = 2

  LKAS_ACTIVE_VALUE = 2

  def setUp(self):
    self.packer = CANPackerPanda("chrysler_ram_hd_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.chrysler, ChryslerSafetyFlags.RAM_HD)
    self.safety.init_tests()

  def _speed_msg(self, speed):
    values = {"Vehicle_Speed": speed}
    return self.packer.make_can_msg_panda("ESP_8", 0, values)

  def _lkas_button_msg(self, enabled):
    values = {"LKAS_Button": enabled}
    return self.packer.make_can_msg_panda("Center_Stack_2", 0, values)


class TestChryslerRamDTRamPilotLongSafety(TestChryslerRamDTSafety):
  """RamPilot longitudinal: the comma's DAS_3 replaces the factory frame while controls are allowed, and may only
  add braking to a fresh factory DAS_3 (the newest, or the one before it when the host is a frame late)."""
  TX_MSGS = [[0xB1, 2], [0xA6, 0], [0xFA, 0], [0x99, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0xA6, 0xFA, 0x99)}
  FWD_BLACKLISTED_ADDRS = {2: [0xA6, 0xFA]}  # the factory DAS_3 (0x99) is blocked only while controls are allowed

  # factory DAS_3 frames; ENGINE_TORQUE_REQUEST and ACC_DECEL are raw values
  STOCK_CRUISE = {"ACC_AVAILABLE": 1, "ACC_ACTIVE": 1, "ENGINE_TORQUE_REQUEST": 2400, "ENGINE_TORQUE_REQUEST_MAX": 1,
                  "ACC_DECEL": ACC_DECEL_INACTIVE, "ACC_DECEL_REQ": 0, "GR_MAX_REQ": 9}  # +100 Nm, no braking
  STOCK_COAST = {**STOCK_CRUISE, "ENGINE_TORQUE_REQUEST": TORQUE_INACTIVE, "ENGINE_TORQUE_REQUEST_MAX": 0}
  STOCK_BRAKING = {**STOCK_COAST, "ACC_DECEL": 3000, "ACC_DECEL_REQ": 1, "GR_MAX_REQ": 6}  # -1.34 m/s^2
  STOCK_AEB = {**STOCK_BRAKING, "ACC_DECEL": 2000, "COLLISION_BRK_PREP": 1, "ACC_BRK_PREP": 1}  # -6.23 m/s^2
  STOCK_STANDSTILL = {**STOCK_BRAKING, "ACC_STANDSTILL": 1}
  STOCK_FRAMES = (STOCK_CRUISE, STOCK_COAST, STOCK_BRAKING, STOCK_AEB, STOCK_STANDSTILL)
  # factory frames that always reach bus 0 themselves: a deceleration request or brake preparation (brake priority)
  PRIORITY_FRAMES = (STOCK_BRAKING, STOCK_AEB, STOCK_STANDSTILL)
  # frames the comma's copy may stand in for while engaged (the content rules are also tested on the braking frames
  # without the emergency bits, with the bus 0 COUNTER history cleared; AEB opens the emergency window)
  CONTENT_FRAMES = (STOCK_CRUISE, STOCK_COAST, STOCK_BRAKING, STOCK_STANDSTILL)

  def setUp(self):
    self.packer = CANPackerPanda("chrysler_ram_dt_generated")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.chrysler, ChryslerSafetyFlags.RAM_DT | ChryslerSafetyFlags.RAMPILOT_LONG)
    self.safety.init_tests()
    self.stock_counter = 0
    self.now = 0  # us, the panda's microsecond timer

  def _set_time(self, t):
    self.now = t
    self.safety.set_timer(t)

  def _das_3_msg(self, bus, fields, flip_bits=(), bad_checksum=False):
    values = dict(fields)
    values["ENGINE_TORQUE_REQUEST"] = fields["ENGINE_TORQUE_REQUEST"] * 0.25 - 500  # raw -> Nm
    values["ACC_DECEL"] = fields["ACC_DECEL"] * 0.004885 - 16  # raw -> m/s^2
    dat = bytearray(self.packer.make_can_msg("DAS_3", bus, values)[1])
    for bit in flip_bits:
      dat[bit // 8] ^= 1 << (bit % 8)
    msg = self.packer.dbc.name_to_msg["DAS_3"]
    checksum = msg.sigs["CHECKSUM"]
    set_value(dat, checksum, checksum.calc_checksum(msg.address, checksum, dat))
    if bad_checksum:
      dat[7] ^= 0xFF
    return libsafety_py.make_CANPacket(msg.address, bus, dat)

  def _das_3_bits(self, signals):
    """DAS_3 bit positions (byte * 8 + bit) the signals occupy, from the DBC"""
    msg = self.packer.dbc.name_to_msg["DAS_3"]
    dat = bytearray(8)
    for name in signals:
      sig = msg.sigs[name]
      set_value(dat, sig, (1 << sig.size) - 1)
    return [bit for bit in range(64) if (dat[bit // 8] >> (bit % 8)) & 1]

  def _comma_attempt(self):
    """A DAS_3 attempt from the comma that is refused: it still shows the comma is sending"""
    self.assertFalse(self._tx(common.make_msg(0, 0x99, 8)))

  def _rx_stock(self, fields, flip_bits=(), dt=STOCK_PERIOD, fwd=True, comma=True):
    """Receive the next factory DAS_3 on bus 2, dt after the last one, in the panda's order: the fwd hook decides
    whether it reaches bus 0 (from the packet, as the panda's CAN receive interrupt calls it), then the rx hook runs.
    With comma, the comma's last DAS_3 attempt is now (as the car side sends one every frame), without sending one.
    Returns its fields; the forwarding decision is in self.fwd_bus (None without fwd)."""
    self._set_time(self.now + dt)
    if comma:
      self.safety.set_chrysler_das_3_tx_ts(self.now)
    self.stock_counter = (self.stock_counter + 1) % 16
    stock = {**fields, "COUNTER": self.stock_counter}
    msg = self._das_3_msg(2, stock, flip_bits)
    self.fwd_bus = self.safety.safety_fwd_packet_hook(2, msg) if fwd else None
    self.assertTrue(self._rx(msg))
    return stock

  def _fwd_das_3(self, fields=None):
    """Where a factory DAS_3 received now goes (0: bus 0, -1: blocked); by default an ordinary frame, without a
    deceleration request or brake preparation"""
    return self.safety.safety_fwd_packet_hook(2, self._das_3_msg(2, fields or self.STOCK_CRUISE))

  def _rx_stock_settled(self, fields, **kw):
    """A factory frame SETTLE after the last one, starting a new bus 0 sequence: any emergency window is over, only this
    frame is fresh, and there is no DAS_3 on bus 0 to time out against"""
    self.safety.set_chrysler_das_3_bus0_counter(-1)
    return self._rx_stock(fields, dt=SETTLE, **kw)

  def _rx_stock_until(self, fields, t_end, **kw):
    """Factory frames every 20 ms until t_end; returns the last"""
    stock = None
    while self.now + STOCK_PERIOD <= t_end:
      stock = self._rx_stock(fields, **kw)
    return stock

  def _engage(self, fields):
    stock = self._rx_stock(fields)
    self.assertTrue(self.safety.get_controls_allowed())
    return stock

  def _tx_seq(self, stock, flip_bits=(), bad_checksum=False, **overrides):
    """Comma DAS_3 built from a factory frame, in sequence with the DAS_3 frames already on bus 0"""
    return self._tx(self._das_3_msg(0, {**stock, **overrides}, flip_bits, bad_checksum))

  def _tx_das_3(self, stock, flip_bits=(), bad_checksum=False, **overrides):
    """Comma DAS_3 built from a factory frame, with the bus 0 COUNTER history and the refusal count cleared, so only
    the matching and content rules apply"""
    self.safety.set_chrysler_das_3_bus0_counter(-1)
    self.safety.reset_chrysler_das_3_fallback()
    return self._tx_seq(stock, flip_bits, bad_checksum, **overrides)

  def _added_braking(self, accel):
    return {"ACC_DECEL_REQ": 1, "ACC_DECEL": accel, "ENGINE_TORQUE_REQUEST": TORQUE_INACTIVE, "ENGINE_TORQUE_REQUEST_MAX": 0}

  def test_option_off_without_bit_4(self):
    # Ram DT without bit 4: DAS_3 is not in the TX list and the factory DAS_3 is always forwarded
    self.safety.set_safety_hooks(CarParams.SafetyModel.chrysler, ChryslerSafetyFlags.RAM_DT)
    self.safety.init_tests()
    stock = self._engage(self.STOCK_CRUISE)
    self.assertEqual(-1, self.safety.get_chrysler_das_3_bus0_counter())
    self.assertFalse(self._tx_das_3(stock))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x99))
    self.assertEqual(0, self._fwd_das_3())

    # bit 4 without the Ram DT bit does nothing: Pacifica (DAS_3 0x1F4 on bus 0) and Ram HD (DAS_3 0x1F4 on bus 2)
    for param in (ChryslerSafetyFlags.RAMPILOT_LONG, ChryslerSafetyFlags.RAM_HD | ChryslerSafetyFlags.RAMPILOT_LONG):
      self.safety.set_safety_hooks(CarParams.SafetyModel.chrysler, param)
      self.safety.init_tests()
      self.safety.set_controls_allowed(True)
      for addr in (0x99, 0x1F4):
        for bus in range(3):
          self.assertFalse(self._tx(common.make_msg(bus, addr, 8)), (param, hex(addr), bus))
      for bus, addr, fwd_bus in ((2, 0x99, 0), (2, 0x1F4, 0), (0, 0x1F4, 2)):
        self.assertEqual(fwd_bus, self.safety.safety_fwd_hook(bus, addr), (param, bus, hex(addr)))
        self.assertEqual(fwd_bus, self.safety.safety_fwd_packet_hook(bus, common.make_msg(bus, addr, 8)), (param, bus, hex(addr)))

  def test_fwd_hook_follows_controls_allowed(self):
    # From the packet (the panda's CAN receive path), an ordinary factory DAS_3 is blocked only while controls are
    # allowed and the comma is sending DAS_3. By address alone (no packet), the factory DAS_3 is never blocked.
    # Every other address is forwarded the same way on both paths.
    for controls_allowed in (False, True):
      for comma_sending in (False, True):
        self._reset_safety_hooks()
        if comma_sending:
          self._comma_attempt()
        self.safety.set_controls_allowed(controls_allowed)
        for bus in range(3):
          for addr in self.SCANNED_ADDRS:
            fwd_bus = self.FWD_BUS_LOOKUP.get(bus, -1)
            if bus == 2 and addr in (0xA6, 0xFA):
              fwd_bus = -1
            packet_fwd_bus = -1 if (bus == 2 and addr == 0x99 and controls_allowed and comma_sending) else fwd_bus
            info = (controls_allowed, comma_sending, bus, hex(addr))
            self.assertEqual(fwd_bus, self.safety.safety_fwd_hook(bus, addr), info)
            self.assertEqual(packet_fwd_bus, self.safety.safety_fwd_packet_hook(bus, common.make_msg(bus, addr, 8)), info)

  def test_factory_brake_frames_never_blocked(self):
    # engaged, comma sending: an ordinary factory DAS_3 is blocked, one with any deceleration request or brake
    # preparation bit is passed to bus 0, as is a DAS_3 that isn't 8 bytes
    self._engage(self.STOCK_COAST)
    self._rx_stock(self.STOCK_COAST)
    self.assertEqual(-1, self.fwd_bus)
    for fields in (self.STOCK_CRUISE, self.STOCK_COAST, {**self.STOCK_COAST, "ACC_DECEL": 2000},
                   {**self.STOCK_CRUISE, "ACC_STANDSTILL": 1, "ACC_GO": 1, "GR_MAX_REQ": 15}):
      self.assertEqual(-1, self._fwd_das_3(fields), fields)
    priority = [{**self.STOCK_COAST, "ACC_DECEL_REQ": decel_req} for decel_req in range(1, 8)]
    priority += [{**self.STOCK_CRUISE, name: 1} for name in ("COLLISION_BRK_PREP", "ACC_BRK_PREP")]
    priority += [{**self.STOCK_CRUISE, "COLLISION_BRK_PREP": 1, "ACC_BRK_PREP": 1}, *self.PRIORITY_FRAMES]
    for fields in priority:
      self.assertEqual(0, self._fwd_das_3(fields), fields)
      self.assertTrue(self.safety.get_controls_allowed())
    for length in (0, 1, 7, 12, 64):
      self.assertEqual(0, self.safety.safety_fwd_packet_hook(2, common.make_msg(2, 0x99, length)), length)

    # through the receive path: each one reaches bus 0 (the truck's COUNTER follows it) while the engagement goes on;
    # an ordinary frame after it (SETTLE later, past any emergency window) is blocked again
    for fields in priority:
      stock = self._rx_stock(fields)
      self.assertEqual(0, self.fwd_bus, fields)
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter(), fields)
      self.assertTrue(self.safety.get_controls_allowed(), fields)
      self._rx_stock_settled(self.STOCK_COAST)
      self.assertEqual(-1, self.fwd_bus, fields)
      self.assertTrue(self.safety.get_controls_allowed(), fields)

  def test_address_only_fwd_is_factory_cruise(self):
    # A caller with only the address (firmware without the packet-aware CAN receive change) never blocks the factory
    # DAS_3. Every comma copy then repeats a frame already on bus 0: refused, never counted, the engagement goes on.
    self._engage(self.STOCK_CRUISE)
    for fields in (self.STOCK_CRUISE, self.STOCK_COAST, self.STOCK_BRAKING) * 20:
      self._set_time(self.now + STOCK_PERIOD)
      self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x99))
      self.stock_counter = (self.stock_counter + 1) % 16
      stock = {**fields, "COUNTER": self.stock_counter}
      self.assertTrue(self._rx(self._das_3_msg(2, stock)))
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
      self.assertFalse(self._tx_seq(stock))
      self.assertFalse(self._tx_seq(stock, **self._added_braking(2800)))
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
      self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0xA6))
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0xFA))

  # brake priority: a factory frame with braking or brake preparation reaches bus 0 itself

  def test_first_factory_brake_with_silent_host(self):
    # The host's last DAS_3 was an accepted copy with added braking, then it goes silent. The first factory frame with
    # braking or brake preparation reaches bus 0 itself: 1, 10, 19 or 20 ms after the frame the copy carried, also
    # after one or two more ordinary factory frames blocked meanwhile (up to 60 ms of silence), at COUNTER 3, 15 and 0
    # (the wrap). The engagement goes on, and the late copies are refused without counting toward the latch.
    triggers = {
      "brake request": self.STOCK_BRAKING,
      "collision prep": {**self.STOCK_COAST, "COLLISION_BRK_PREP": 1},
      "ACC brake prep": {**self.STOCK_COAST, "ACC_BRK_PREP": 1},
      "strong braking": {**self.STOCK_BRAKING, "ACC_DECEL": 2000},
    }
    for trigger_counter in (3, 15, 0):
      for phase in (1000, 10000, 19000, 20000):
        for lost in range(3):
          for name, trigger in triggers.items():
            info = (trigger_counter, phase, lost, name)
            self._reset_safety_hooks()
            self.stock_counter = (trigger_counter - 3 - lost) % 16
            self._engage(self.STOCK_COAST)
            source = self._rx_stock(self.STOCK_COAST)
            self.assertEqual(-1, self.fwd_bus, info)
            self.assertTrue(self._tx_seq(source, **self._added_braking(3000)), info)

            # the host is silent from here on
            silent = [self._rx_stock(self.STOCK_COAST, comma=False) for _ in range(lost)]
            if silent:
              self.assertEqual(-1, self.fwd_bus, info)
            first = self._rx_stock(trigger, dt=phase, comma=False)
            self.assertEqual(trigger_counter, first["COUNTER"], info)
            self.assertEqual(0, self.fwd_bus, info)
            self.assertEqual(first["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter(), info)
            self.assertTrue(self.safety.get_controls_allowed(), info)

            # late copies: of the frames before it (behind) and of the factory brake frame itself (a repeat)
            for late in (source, *silent, first, {**first, "ACC_DECEL": 2700}):
              self.assertFalse(self._tx_seq(late), info)
            self.assertEqual(0, self.safety.get_chrysler_das_3_refusals(), info)
            self.assertFalse(self.safety.get_chrysler_das_3_fallback(), info)
            self.assertTrue(self.safety.get_controls_allowed(), info)

  def test_comma_copy_of_forwarded_brake_frame_refused(self):
    # A factory braking frame reaches bus 0 itself and the truck's COUNTER follows it. The comma's copy of it is a
    # repeat, and a late copy of the frame before it is behind: both refused, neither counts toward the latch, however
    # many. With the truck's COUNTER known, every copy of a forwarded frame is a repeat or behind, so the COUNTER rule
    # already refuses them all.
    self._engage(self.STOCK_COAST)
    before = self._rx_stock(self.STOCK_COAST)  # blocked; its copy comes late
    braking = self._rx_stock(self.STOCK_BRAKING)
    self.assertEqual(0, self.fwd_bus)
    self.assertEqual(braking["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    for _ in range(5):
      for copy in (braking, {**braking, "ACC_DECEL": 2800}, before, {**before, **self._added_braking(2800)}):
        self.assertFalse(self._tx_seq(copy), copy)
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())
    self.assertTrue(self.safety.get_controls_allowed())

    # the next ordinary frame is blocked; the forwarded braking frame is now the one before it, and its copy is behind
    after = self._rx_stock(self.STOCK_COAST)
    self.assertEqual(-1, self.fwd_bus)
    self.assertFalse(self._tx_seq(braking))
    self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertTrue(self._tx_seq(after, **self._added_braking(2800)))
    self.assertEqual(after["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

  def test_factory_braking_episode(self):
    # Engaged, the comma adding braking. The factory brakes for 3 s: its frames reach bus 0 themselves, so the truck
    # gets the factory's braking, not the comma's stronger request; the comma's copies are refused without counting,
    # and the forwarded frames keep the engagement going (no delivery timeout). When the factory stops braking, its
    # frames are blocked again and the comma's copies carry them.
    self._engage(self.STOCK_COAST)
    for _ in range(5):
      stock = self._rx_stock(self.STOCK_COAST)
      self.assertEqual(-1, self.fwd_bus)
      self.assertTrue(self._tx_seq(stock, **self._added_braking(2700)))
    for _ in range(150):
      stock = self._rx_stock(self.STOCK_BRAKING)
      self.assertEqual(0, self.fwd_bus)
      self.assertFalse(self._tx_seq(stock, ACC_DECEL=2700))
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
      self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.get_chrysler_das_3_emergency_seen())
    for _ in range(5):
      stock = self._rx_stock(self.STOCK_COAST)
      self.assertEqual(-1, self.fwd_bus)
      self.assertTrue(self._tx_seq(stock, **self._added_braking(2700)))

  def test_fwd_hook_engagement(self):
    # forward the factory DAS_3 until it engages, block it while engaged, forward it again on cancel
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertEqual(0, self._fwd_das_3())
    self._engage(self.STOCK_CRUISE)
    self.assertEqual(-1, self._fwd_das_3())
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertEqual(0, self._fwd_das_3())

  def test_tx_pass_through_copy(self):
    # SETTLE between frames: the AEB frame's emergency window is over before the next one. An ordinary factory frame
    # is blocked and the comma's copy carries it; a braking or brake-prep frame reaches bus 0 itself, and the comma's
    # copy repeats it: refused, without counting toward the latch.
    self._engage(self.STOCK_COAST)
    for fields in self.STOCK_FRAMES:
      stock = self._rx_stock_settled(fields)
      self.assertTrue(self.safety.get_controls_allowed())
      priority = fields in self.PRIORITY_FRAMES
      self.assertEqual(0 if priority else -1, self.fwd_bus, fields)
      self.assertEqual(not priority, self._tx_seq(stock), fields)
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals(), fields)
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter(), fields)

  def test_tx_requires_controls_allowed(self):
    stock = self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_das_3(stock))

    stock = self._engage(self.STOCK_CRUISE)
    self.assertTrue(self._tx_das_3(stock))
    self.safety.set_controls_allowed(False)
    self.assertFalse(self._tx_das_3(stock))  # an exact copy, but of a frame that reached bus 0
    self.assertFalse(self._tx_das_3(stock, **self._added_braking(3000)))

  def test_tx_requires_factory_frame(self):
    # an all-zero DAS_3 with a valid checksum: what an unseen stored frame holds
    zero = {"ENGINE_TORQUE_REQUEST": 0, "ACC_DECEL": 0, "COUNTER": 0}

    # controls allowed, but no factory DAS_3 received yet
    self.safety.set_controls_allowed(True)
    self.assertFalse(self._tx(common.make_msg(0, 0x99, 8)))
    self.assertFalse(self._tx_das_3(zero))
    for fields in self.STOCK_FRAMES:
      for counter in range(16):
        self.assertFalse(self._tx_das_3({**fields, "COUNTER": counter}))

    # one factory frame: nothing before it to match
    stock = self._engage(self.STOCK_CRUISE)
    self.assertNotEqual(0, stock["COUNTER"])
    self.assertFalse(self._tx_das_3(zero))
    self.assertTrue(self._tx_das_3(stock))

  def test_tx_requires_fresh_factory_frame(self):
    stock = self._engage(self.STOCK_CRUISE)
    t = self.now
    for elapsed in (0, 20000, STOCK_MAX_AGE, STOCK_MAX_AGE + 1, 100000):
      self._set_time(t + elapsed)
      self.assertEqual(elapsed <= STOCK_MAX_AGE, self._tx_das_3(stock), elapsed)

    # a new factory frame makes it fresh again
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self._tx_das_3(stock))

  def test_tx_previous_frame_freshness(self):
    # the frame before the newest: within 60 ms, while the newest is fresh (within 40 ms)
    previous = self._engage(self.STOCK_CRUISE)
    t = self.now
    latest = self._rx_stock(self.STOCK_CRUISE, dt=30000)
    for elapsed, allowed in ((30000, True), (STOCK_PREV_MAX_AGE, True), (STOCK_PREV_MAX_AGE + 1, False)):
      self._set_time(t + elapsed)
      self.assertEqual(allowed, self._tx_das_3(previous), elapsed)
      self.assertTrue(self._tx_das_3(latest), elapsed)

    # newest frame 5 ms after the one before: once the newest is stale, the one before is refused too
    previous = self._rx_stock(self.STOCK_CRUISE)
    t = self.now
    latest = self._rx_stock(self.STOCK_CRUISE, dt=5000)
    self._set_time(t + 5000 + STOCK_MAX_AGE)
    self.assertTrue(self._tx_das_3(previous))
    self.assertTrue(self._tx_das_3(latest))
    self._set_time(t + 5000 + STOCK_MAX_AGE + 1)
    self.assertFalse(self._tx_das_3(previous))
    self.assertFalse(self._tx_das_3(latest))

  def test_tx_counter_must_match(self):
    stock = self._engage(self.STOCK_COAST)
    for counter in range(16):
      self.assertEqual(counter == stock["COUNTER"], self._tx_das_3(stock, COUNTER=counter), counter)

    # with a newer factory frame: its COUNTER or the one before
    previous, latest = stock, self._rx_stock(self.STOCK_COAST)
    for counter in range(16):
      allowed = counter in (previous["COUNTER"], latest["COUNTER"])
      self.assertEqual(allowed, self._tx_das_3(latest, COUNTER=counter), counter)

  def test_tx_copied_bits_must_match(self):
    copied_bits = [bit for bit in range(56) if bit not in self._das_3_bits(DAS_3_CHANGEABLE)]
    undefined_bits = [bit for bit in copied_bits if bit not in self._das_3_bits(DAS_3_COPIED + ("COUNTER",))]
    self.assertEqual([22, 39, 40, 41, 42, 43, 44, 45, 50, 51], undefined_bits)

    # SETTLE: the frame before is stale, so a flipped COUNTER bit can't match it instead
    for fields in self.CONTENT_FRAMES:
      stock = self._rx_stock_settled(fields)
      self.assertTrue(self._tx_das_3(stock))
      for bit in copied_bits:
        self.assertFalse(self._tx_das_3(stock, flip_bits=(bit,)), (fields, bit))

    # factory frames with bits no signal defines: the comma copies them
    for bit in undefined_bits:
      stock = self._rx_stock_settled(self.STOCK_CRUISE, flip_bits=(bit,))
      self.assertTrue(self._tx_das_3(stock, flip_bits=(bit,)), bit)
      self.assertFalse(self._tx_das_3(stock), bit)

  def test_tx_copied_signals_must_match(self):
    # every value of every copied signal, including the collision and brake-prep (AEB) bits set and cleared. A factory
    # frame with a brake-prep bit reaches bus 0 itself and opens the emergency window: no copy of it is accepted.
    msg = self.packer.dbc.name_to_msg["DAS_3"]
    for base in (self.STOCK_COAST, self.STOCK_BRAKING):
      for name in DAS_3_COPIED:
        stock_values = [1] if name == "ACC_ACTIVE" else range(1 << msg.sigs[name].size)  # controls need ACC_ACTIVE
        for stock_value in stock_values:
          stock = self._rx_stock_settled({**base, name: stock_value})
          self.assertTrue(self.safety.get_controls_allowed())
          brake_prep = name in ("COLLISION_BRK_PREP", "ACC_BRK_PREP") and stock_value == 1
          for value in range(1 << msg.sigs[name].size):
            allowed = value == stock_value and not brake_prep
            self.assertEqual(allowed, self._tx_das_3(stock, **{name: value}), (base, name, stock_value, value))

  def test_tx_factory_braking_kept(self):
    # The content rule on its own (bus 0 COUNTER history cleared): in the panda a factory braking frame reaches bus 0
    # itself, so a copy of it is a repeat (see test_comma_copy_of_forwarded_brake_frame_refused)
    stock = self._engage(self.STOCK_BRAKING)
    for accel in range(2400, 3200):
      allowed = ACC_DECEL_MIN <= accel <= stock["ACC_DECEL"]
      self.assertEqual(allowed, self._tx_das_3(stock, ACC_DECEL=accel), accel)

    # dropping or changing the factory deceleration request
    self.assertFalse(self._tx_das_3(stock, ACC_DECEL_REQ=0))
    self.assertFalse(self._tx_das_3(stock, ACC_DECEL_REQ=0, ACC_DECEL=ACC_DECEL_INACTIVE))
    for decel_req in range(2, 8):
      self.assertFalse(self._tx_das_3(stock, ACC_DECEL_REQ=decel_req), decel_req)

  def test_tx_factory_braking_beyond_comma_limit_not_copied(self):
    # a factory request stronger than -3.5 m/s^2 (AEB) reaches bus 0 itself and opens the emergency window: the comma
    # can't change it either way, and no copy of it is accepted
    self._engage(self.STOCK_COAST)
    for fields in (self.STOCK_AEB, {**self.STOCK_BRAKING, "ACC_DECEL": 2000}):
      stock = self._rx_stock_settled(fields)
      self.assertEqual(0, self.fwd_bus)
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
      for accel in range(1900, 2700):
        self.assertFalse(self._tx_das_3(stock, ACC_DECEL=accel), (fields, accel))
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
      self.assertTrue(self.safety.get_controls_allowed())

  def test_tx_added_braking(self):
    # braking the factory doesn't request: -3.5 m/s^2 (raw 2558) to 0 m/s^2 (raw 3275, -0.002 m/s^2)
    stock = self._engage(self.STOCK_COAST)
    for accel in (0, 1000, ACC_DECEL_MIN - 100, ACC_DECEL_MIN - 1, ACC_DECEL_MIN, 3000, ACC_DECEL_ZERO, ACC_DECEL_ZERO + 1,
                  3685, ACC_DECEL_INACTIVE - 1, ACC_DECEL_INACTIVE, ACC_DECEL_INACTIVE + 1):
      allowed = ACC_DECEL_MIN <= accel <= ACC_DECEL_ZERO
      self.assertEqual(allowed, self._tx_das_3(stock, **self._added_braking(accel)), accel)

    # added braking uses ACC_DECEL_REQ 1
    for decel_req in range(2, 8):
      self.assertFalse(self._tx_das_3(stock, **{**self._added_braking(3000), "ACC_DECEL_REQ": decel_req}), decel_req)

    # without a deceleration request ACC_DECEL stays as the factory's
    for accel in (ACC_DECEL_MIN, 3000, ACC_DECEL_INACTIVE - 1, ACC_DECEL_INACTIVE + 1):
      self.assertFalse(self._tx_das_3(stock, ACC_DECEL=accel), accel)
    self.assertTrue(self._tx_das_3(stock, ACC_DECEL=ACC_DECEL_INACTIVE))

    # a factory ACC_DECEL below -3.5 m/s^2 without a request doesn't widen the comma's range
    stock = self._rx_stock({**self.STOCK_COAST, "ACC_DECEL": 2000})
    self.assertTrue(self._tx_das_3(stock))
    for accel in (2000, 2100, ACC_DECEL_MIN - 1, ACC_DECEL_MIN, 3000):
      self.assertEqual(accel >= ACC_DECEL_MIN, self._tx_das_3(stock, **self._added_braking(accel)), accel)

  def test_tx_added_braking_cuts_torque(self):
    # factory requests +100 Nm: added braking only with torque at most 0 Nm
    stock = self._engage(self.STOCK_CRUISE)
    for torque in range(1900, 2500):
      for torque_max in (0, 1):
        self.assertEqual(torque <= TORQUE_INACTIVE, self._tx_das_3(stock, **{**self._added_braking(3000),
                         "ENGINE_TORQUE_REQUEST": torque, "ENGINE_TORQUE_REQUEST_MAX": torque_max}), (torque, torque_max))

    # factory requests -50 Nm: added braking only with torque at most the factory's
    stock = self._rx_stock({**self.STOCK_CRUISE, "ENGINE_TORQUE_REQUEST": 1800})
    for torque in range(1700, 2100):
      self.assertEqual(torque <= 1800, self._tx_das_3(stock, **{**self._added_braking(3000), "ENGINE_TORQUE_REQUEST": torque}),
                       torque)

  def test_tx_torque_never_above_factory(self):
    for fields in self.CONTENT_FRAMES:
      stock = self._rx_stock_settled(fields)
      for torque in (0, 1000, stock["ENGINE_TORQUE_REQUEST"] - 1, stock["ENGINE_TORQUE_REQUEST"],
                     stock["ENGINE_TORQUE_REQUEST"] + 1, 4000, 8191):
        allowed = torque <= stock["ENGINE_TORQUE_REQUEST"]
        self.assertEqual(allowed, self._tx_das_3(stock, ENGINE_TORQUE_REQUEST=torque), (fields, torque))

      for torque_max in (0, 1):
        allowed = torque_max <= stock["ENGINE_TORQUE_REQUEST_MAX"]
        self.assertEqual(allowed, self._tx_das_3(stock, ENGINE_TORQUE_REQUEST_MAX=torque_max), (fields, torque_max))

  def test_tx_exact_copy_with_gas_pressed(self):
    for fields in (self.STOCK_CRUISE, self.STOCK_COAST, self.STOCK_BRAKING):
      stock = self._engage(fields)
      self._rx(self._user_gas_msg(1))
      self.assertTrue(self.safety.get_controls_allowed())
      self.assertFalse(self.safety.get_longitudinal_allowed())

      # while the driver presses the gas: the factory frame itself, nothing else (not even less torque)
      changes = [{"ENGINE_TORQUE_REQUEST": 0}, {"ENGINE_TORQUE_REQUEST": stock["ENGINE_TORQUE_REQUEST"] - 1},
                 self._added_braking(3000) if not stock["ACC_DECEL_REQ"] else {"ACC_DECEL": stock["ACC_DECEL"] - 1}]
      if stock["ENGINE_TORQUE_REQUEST_MAX"]:
        changes.append({"ENGINE_TORQUE_REQUEST_MAX": 0})
      self.assertTrue(self._tx_das_3(stock))
      for change in changes:
        self.assertFalse(self._tx_das_3(stock, **change), (fields, change))

      self._rx(self._user_gas_msg(0))
      self.assertTrue(self.safety.get_longitudinal_allowed())
      for change in changes:
        self.assertTrue(self._tx_das_3(stock, **change), (fields, change))

      # reset engagement for the next frame
      self._rx_stock({**fields, "ACC_ACTIVE": 0})

  def test_brake_press_ends_das_3(self):
    self._rx(self._vehicle_moving_msg(10))
    stock = self._engage(self.STOCK_BRAKING)
    self.assertTrue(self._tx_das_3(stock, ACC_DECEL=2800))
    self._rx(self._user_brake_msg(True))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_das_3(stock))
    self.assertFalse(self._tx_das_3(stock, ACC_DECEL=2800))
    self.assertEqual(0, self._fwd_das_3())

    # brake released with the factory still active: no re-entry without a new ACC_ACTIVE rising edge
    self._rx(self._user_brake_msg(False))
    stock = self._rx_stock(self.STOCK_BRAKING)
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_das_3(stock))
    self.assertEqual(0, self._fwd_das_3())

  def test_cancel_and_main_off_end_das_3(self):
    for off in ({"ACC_ACTIVE": 0}, {"ACC_ACTIVE": 0, "ACC_AVAILABLE": 0}):
      stock = self._engage(self.STOCK_COAST)
      self.assertTrue(self._tx_das_3(stock, **self._added_braking(2800)))
      stock = self._rx_stock({**self.STOCK_COAST, **off})
      self.assertEqual(-1, self.fwd_bus)  # controls were allowed when it arrived
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(self._tx_das_3(stock, **self._added_braking(2800)))
      self.assertFalse(self._tx_das_3(stock, **{**self._added_braking(2800), "ACC_ACTIVE": 1}))
      self.assertTrue(self._tx_das_3(stock))  # an exact copy of the blocked frame fills the gap
      self.assertEqual(0, self._fwd_das_3())

      # a factory braking frame that cancels reaches bus 0 itself: no gap, no copy
      self._rx_stock({**self.STOCK_COAST, "ACC_ACTIVE": 0})
      stock = self._engage(self.STOCK_COAST)
      self.assertTrue(self._tx_seq(self._rx_stock(self.STOCK_COAST)))
      stock = self._rx_stock({**self.STOCK_BRAKING, **off})
      self.assertEqual(0, self.fwd_bus)
      self.assertFalse(self.safety.get_controls_allowed())
      self.assertFalse(self._tx_seq(stock))
      self.assertFalse(self._tx_das_3(stock))
      self._rx_stock({**self.STOCK_COAST, "ACC_ACTIVE": 0})

  def test_factory_frame_with_bad_checksum_not_used(self):
    stock = self._engage(self.STOCK_CRUISE)
    self.stock_counter = (self.stock_counter + 1) % 16
    bad = {**self.STOCK_AEB, "COUNTER": self.stock_counter}
    msg = self._das_3_msg(2, bad)
    msg[0].data[7] ^= 0xFF
    self.assertFalse(self._rx(msg))
    self.assertFalse(self.safety.get_controls_allowed())

    # the bad frame was not stored: the comma can't copy it, the last good frame still counts
    self.safety.set_controls_allowed(True)
    self.assertFalse(self._tx_das_3(bad))
    self.assertTrue(self._tx_das_3(stock))

  def test_factory_counter_errors_end_das_3(self):
    stock = self._engage(self.STOCK_CRUISE)
    for _ in range(common.MAX_WRONG_COUNTERS):
      self._rx(self._das_3_msg(2, stock))  # same counter again
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_das_3(stock))
    self.assertEqual(0, self._fwd_das_3())

  # COUNTER sequence on bus 0: forwarded factory frames and accepted comma frames

  def test_bus0_counter_tracks_forwarded_and_accepted_frames(self):
    self.assertEqual(-1, self.safety.get_chrysler_das_3_bus0_counter())

    # not engaged: factory frames are forwarded
    stock = self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # a frame the mode's fwd hook never passed on (with a relay malfunction it isn't called) doesn't count
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0}, fwd=False)
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    stock = self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # the engaging frame is forwarded too (the fwd hook runs before the rx hook)
    engaged = self._engage(self.STOCK_CRUISE)
    self.assertEqual(engaged["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # engaged: factory frames are blocked; refused comma frames don't count, accepted ones do
    blocked = self._rx_stock(self.STOCK_CRUISE)
    self.assertEqual(engaged["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertFalse(self._tx_seq(blocked, ENGINE_TORQUE_REQUEST=4000))
    self.assertEqual(engaged["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertTrue(self._tx_seq(blocked))
    self.assertEqual(blocked["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # the frame that disengages arrives while blocked; the next one is forwarded
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(blocked["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    stock = self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

  def test_tx_late_copy_of_previous_frame(self):
    self._engage(self.STOCK_CRUISE)  # reaches bus 0
    for _ in range(4):
      previous = self._rx_stock(self.STOCK_CRUISE)
      latest = self._rx_stock(self.STOCK_CRUISE)
      self.assertTrue(self._tx_seq(previous))  # a frame late
      self.assertTrue(self._tx_seq(latest))

  def test_tx_copy_two_frames_back_refused(self):
    self._engage(self.STOCK_CRUISE)
    two_back = self._rx_stock(self.STOCK_CRUISE)
    self._rx_stock(self.STOCK_CRUISE)
    self._rx_stock(self.STOCK_CRUISE)
    self.assertFalse(self._tx_seq(two_back))
    self.assertFalse(self._tx_das_3(two_back))

  def test_tx_repeated_counter_refused(self):
    # the forwarded engaging frame can't be sent again
    engaged = self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertFalse(self._tx_seq(engaged))

    self.assertTrue(self._tx_seq(stock))
    self.assertFalse(self._tx_seq(stock))
    self.assertFalse(self._tx_seq(stock, ACC_DECEL_REQ=1, ACC_DECEL=3000, ENGINE_TORQUE_REQUEST=TORQUE_INACTIVE))

  def test_tx_backward_counter_refused(self):
    self._engage(self.STOCK_CRUISE)
    previous = self._rx_stock(self.STOCK_CRUISE)
    latest = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self._tx_seq(latest))
    self.assertFalse(self._tx_seq(previous))  # matches the frame before, but behind bus 0

  def test_tx_counter_steps(self):
    # 1 to 7 ahead of the last COUNTER on bus 0 (3 and 7 included): accepted; a repeat (0) or a step back (8 to 15):
    # refused
    stock = self._engage(self.STOCK_CRUISE)
    for step in range(16):
      self.safety.set_chrysler_das_3_bus0_counter((stock["COUNTER"] - step) % 16)
      self.safety.reset_chrysler_das_3_fallback()
      self.assertEqual(1 <= step <= 7, self._tx_seq(stock), step)

  def test_tx_lost_frames_recover(self):
    # two factory frames lost on the way through the host: the next copy is 3 ahead, and the sequence continues
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self._tx_seq(stock))
    self._rx_stock(self.STOCK_CRUISE, comma=False)
    self._rx_stock(self.STOCK_CRUISE, comma=False)
    for _ in range(5):
      stock = self._rx_stock(self.STOCK_CRUISE)
      self.assertEqual(-1, self._fwd_das_3())
      self.assertTrue(self._tx_seq(stock))
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

  def test_host_silence_falls_back_to_factory_frames(self):
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self._tx_seq(stock))
    t = self.now

    # no attempt for up to 60 ms: factory frames still blocked
    for _ in range(3):
      self._rx_stock(self.STOCK_CRUISE, comma=False)
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertEqual(t + 60000, self.now)

    # longer: the factory frames reach bus 0 and the COUNTER follows them
    for _ in range(3):
      forwarded = self._rx_stock(self.STOCK_CRUISE, comma=False)
      self.assertEqual(forwarded["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
      self.assertEqual(0, self._fwd_das_3())
    self.assertTrue(self.safety.get_controls_allowed())

    # the comma resumes: its copy of the frame already on bus 0 is refused but counts as an attempt, so the next
    # factory frame is blocked and the copy one ahead is accepted
    self.assertFalse(self._tx_seq(forwarded))
    stock = self._rx_stock(self.STOCK_CRUISE, comma=False)
    self.assertEqual(forwarded["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertTrue(self._tx_seq(stock))
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

  # refusal latch: refused comma frames in a row hand bus 0 back to the factory

  def test_refusal_latch(self):
    # two refusals in a row keep blocking; the third hands bus 0 to the factory and ends the engagement
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    for refusals in (1, 2):
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
      self.assertEqual(refusals, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())
    self.assertTrue(self.safety.get_controls_allowed())
    blocked = self._rx_stock(self.STOCK_CRUISE)
    self.assertEqual(-1, self._fwd_das_3())
    self.assertFalse(self._tx_seq(blocked, ENGINE_TORQUE_REQUEST=4000))
    self.assertTrue(self.safety.get_chrysler_das_3_fallback())
    self.assertFalse(self.safety.get_controls_allowed())

    # controls not allowed: only the exact copy of the frame blocked before the latch fills its gap
    self.assertFalse(self._tx_seq(blocked, ENGINE_TORQUE_REQUEST=4000))
    self.assertTrue(self._tx_seq(blocked))

    # the factory frames reach bus 0; factory cruise is still on, but that doesn't engage again
    for _ in range(3):
      stock = self._rx_stock(self.STOCK_CRUISE)
      self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
      self.assertEqual(0, self._fwd_das_3())
      self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())  # cleared with controls not allowed

    # a new ACC_ACTIVE rising edge engages again, and the comma's frame replaces the factory's
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertEqual(-1, self._fwd_das_3())
    self.assertTrue(self._tx_seq(stock))

  def test_refusal_count_reset_by_accepted_copy(self):
    self._engage(self.STOCK_CRUISE)
    for _ in range(5):
      stock = self._rx_stock(self.STOCK_CRUISE)
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
      self.assertEqual(2, self.safety.get_chrysler_das_3_refusals())
      self.assertTrue(self._tx_seq(stock))
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())
    self.assertEqual(-1, self._fwd_das_3())

  def test_refusal_latch_clears_on_disengage(self):
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    for _ in range(3):
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
    self.assertTrue(self.safety.get_chrysler_das_3_fallback())

    # cancel: once controls are not allowed, the latch and the count clear
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())
    self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())

    # the next engagement blocks the factory frames again and takes the comma's copies
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertEqual(-1, self._fwd_das_3())
    self.assertTrue(self._tx_seq(stock))

  def test_behind_and_repeat_refusals_never_latch(self):
    # recorded drive 3f8/95 at 35.84 s with 15 ms host latency: the host's CAN receive paused for 61 ms, so the factory
    # frames that came in meanwhile were answered late. The frame after the 60 ms attempt timeout reached the truck, and
    # the late copies of the frames before it were then behind or repeats: refused, but none counts toward the latch.
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self._tx_seq(stock))
    paused = [self._rx_stock(self.STOCK_CRUISE, comma=False) for _ in range(3)]  # 20-60 ms: still blocked
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    forwarded = self._rx_stock(self.STOCK_CRUISE, dt=5, comma=False)  # 65 ms: the factory frame reaches the truck
    self.assertEqual(forwarded["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    for late in (paused[1], paused[2], forwarded):  # two back (behind), the one before (behind), the same (repeat)
      self.assertFalse(self._tx_seq(late))
    self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())

    # the comma is sending again: the next factory frame is blocked and its copy accepted
    stock = self._rx_stock(self.STOCK_CRUISE, comma=False)
    self.assertEqual(-1, self._fwd_das_3())
    self.assertTrue(self._tx_seq(stock))
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # behind and repeat refusals never latch, however many
    for _ in range(5):
      self.assertFalse(self._tx_seq(stock))
      self.assertFalse(self._tx_seq(forwarded))
    self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())

  def test_stale_ahead_refusals_latch(self):
    # refused copies ahead of the truck's COUNTER count: a copy of a frame two back
    engaged = self._engage(self.STOCK_CRUISE)
    frames = [self._rx_stock(self.STOCK_CRUISE) for _ in range(3)]
    self.assertIn((frames[0]["COUNTER"] - engaged["COUNTER"]) % 16, range(1, 8))
    self.assertFalse(self._tx_seq(frames[0]))
    self.assertEqual(1, self.safety.get_chrysler_das_3_refusals())

    # a copy of the newest frame that is too old: stale and ahead; three in a row latch and end the engagement
    self._reset_safety_hooks()
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self._set_time(self.now + STOCK_MAX_AGE + 1)
    for refusals in (1, 2, 3):
      self.assertFalse(self._tx_seq(stock))
      self.assertEqual(refusals, self.safety.get_chrysler_das_3_refusals())
    self.assertTrue(self.safety.get_chrysler_das_3_fallback())
    self.assertFalse(self.safety.get_controls_allowed())

  def test_refusals_count_with_no_truck_counter(self):
    # no DAS_3 on bus 0 yet: every refused copy counts
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    for refusals in (1, 2, 3):
      self.safety.set_chrysler_das_3_bus0_counter(-1)
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
      self.assertEqual(refusals, self.safety.get_chrysler_das_3_refusals())
    self.assertTrue(self.safety.get_chrysler_das_3_fallback())

  def test_refusals_while_not_allowed_do_not_count(self):
    stock = self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    for _ in range(5):
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
      self._comma_attempt()
    self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())

    # also copies that would move the truck forward (no DAS_3 on bus 0 yet)
    for _ in range(5):
      self.safety.set_chrysler_das_3_bus0_counter(-1)
      self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
    self.assertFalse(self.safety.get_chrysler_das_3_fallback())

    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertEqual(-1, self._fwd_das_3())
    self.assertTrue(self._tx_seq(stock))

  # factory emergency: the factory's own frames pass directly to bus 0

  def test_emergency_window(self):
    for trigger in ({"COLLISION_BRK_PREP": 1}, {"ACC_BRK_PREP": 1}, {"ACC_DECEL_REQ": 1, "ACC_DECEL": ACC_DECEL_EMERGENCY - 1}):
      self._reset_safety_hooks()
      self._engage(self.STOCK_COAST)
      self.assertTrue(self._tx_seq(self._rx_stock(self.STOCK_COAST)))

      # the frame that starts it reaches bus 0 itself (brake priority); the comma's copy repeats it and is refused
      emergency = self._rx_stock({**self.STOCK_COAST, **trigger})
      self.assertEqual(0, self.fwd_bus, trigger)
      self.assertEqual(emergency["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter(), trigger)
      self.assertFalse(self._tx_seq(emergency), trigger)
      t = self.now

      # for 2 s the factory frames pass directly and the comma's copies are refused, without counting toward the latch
      for elapsed in (STOCK_PERIOD, EMERGENCY_TIME // 2, EMERGENCY_TIME):
        stock = self._rx_stock(self.STOCK_COAST, dt=t + elapsed - self.now)
        self.assertEqual(0, self.fwd_bus, (trigger, elapsed))
        self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter(), (trigger, elapsed))
        self.safety.set_chrysler_das_3_bus0_counter(-1)
        for _ in range(4):
          self.assertFalse(self._tx_seq(stock), (trigger, elapsed))
          self.assertFalse(self._tx_seq(stock, **self._added_braking(2800)), (trigger, elapsed))
        self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
        self.assertFalse(self.safety.get_chrysler_das_3_fallback())

      # after 2 s the factory frame is blocked again (bus 0's COUNTER doesn't follow it) and the comma's frame replaces it
      stock = self._rx_stock(self.STOCK_COAST, dt=t + EMERGENCY_TIME + 1 - self.now)
      self.assertEqual(-1, self.fwd_bus, trigger)
      self.assertEqual(-1, self.safety.get_chrysler_das_3_bus0_counter(), trigger)
      self.assertTrue(self._tx_seq(stock, **self._added_braking(2800)), trigger)

  def test_emergency_window_extended(self):
    self._engage(self.STOCK_COAST)
    self._rx_stock(self.STOCK_AEB)  # opens the window
    t = self.now
    self._rx_stock_until(self.STOCK_COAST, t + EMERGENCY_TIME // 2)  # these pass directly
    self._rx_stock(self.STOCK_AEB)  # passes directly and extends the window
    t_extended = self.now

    stock = self._rx_stock_until(self.STOCK_COAST, t + EMERGENCY_TIME + STOCK_PERIOD)  # past the first window
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    last = self._rx_stock_until(self.STOCK_COAST, t_extended + EMERGENCY_TIME)
    self.assertEqual(t_extended + EMERGENCY_TIME, self.now)
    self.assertEqual(last["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertTrue(self.safety.get_controls_allowed())  # the window doesn't end the engagement

    stock = self._rx_stock(self.STOCK_COAST, dt=1)
    self.assertEqual(last["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertTrue(self._tx_seq(stock))

  # timestamps only count while fresh, also when the 32-bit microsecond timer wraps (every 2^32 us, 71.6 minutes)

  def test_attempt_timestamp_expires(self):
    self._engage(self.STOCK_CRUISE)
    self._rx_stock(self.STOCK_CRUISE)  # the comma's last attempt is now
    t = self.now
    for _ in range(3):  # up to 60 ms later: still blocked
      self._rx_stock(self.STOCK_CRUISE, comma=False)
      self.assertTrue(self.safety.get_chrysler_das_3_tx_seen())
    stock = self._rx_stock(self.STOCK_CRUISE, comma=False)  # 80 ms: expired and forwarded
    self.assertFalse(self.safety.get_chrysler_das_3_tx_seen())
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # exactly 2^32 us after the last attempt the timer reads the same value; that is not a fresh attempt
    self._set_time((t + 2**32) % 2**32)
    stock = self._rx_stock(self.STOCK_CRUISE, dt=0, comma=False)
    self.assertFalse(self.safety.get_chrysler_das_3_tx_seen())
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertEqual(0, self._fwd_das_3())

  def test_emergency_timestamp_expires(self):
    self._engage(self.STOCK_COAST)
    self._rx_stock(self.STOCK_AEB)  # opens the window
    t = self.now
    self.assertTrue(self.safety.get_chrysler_das_3_emergency_seen())
    self._rx_stock_until(self.STOCK_COAST, t + EMERGENCY_TIME)  # these pass directly
    blocked = self._rx_stock(self.STOCK_COAST, dt=t + EMERGENCY_TIME + 1 - self.now)  # expired: blocked again
    self.assertFalse(self.safety.get_chrysler_das_3_emergency_seen())
    self.assertNotEqual(blocked["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # exactly 2^32 us after the emergency frame: no window (no bus 0 history here, so no delivery timeout either)
    self.safety.set_chrysler_das_3_bus0_counter(-1)
    self._set_time((t + 2**32) % 2**32)
    self._rx_stock(self.STOCK_COAST, dt=0)
    self.assertFalse(self.safety.get_chrysler_das_3_emergency_seen())
    self.assertEqual(-1, self.safety.get_chrysler_das_3_bus0_counter())  # blocked: nothing reached bus 0
    self.assertEqual(-1, self._fwd_das_3())

  def test_refusals_count_after_emergency_window(self):
    # the window closes on time also with no factory frame since: refusals count toward the latch again
    self._engage(self.STOCK_COAST)
    aeb = self._rx_stock(self.STOCK_AEB)
    self._set_time(self.now + EMERGENCY_TIME + 1)
    ahead = {**self.STOCK_COAST, "COUNTER": (aeb["COUNTER"] + 1) % 16}
    for _ in range(3):
      self.assertFalse(self._tx_seq(ahead))  # no fresh factory frame
    self.assertFalse(self.safety.get_chrysler_das_3_emergency_seen())
    self.assertTrue(self.safety.get_chrysler_das_3_fallback())

  def test_no_emergency_window(self):
    # a request of exactly -2.5 m/s^2 (raw 2764), the factory's hardest recorded request (-2.16 m/s^2), or a value
    # stronger than -2.5 m/s^2 without a deceleration request; the requests reach bus 0 themselves (brake priority)
    self._engage(self.STOCK_COAST)
    for fields, fwd_bus in (({**self.STOCK_BRAKING, "ACC_DECEL": ACC_DECEL_EMERGENCY}, 0),
                            ({**self.STOCK_BRAKING, "ACC_DECEL": ACC_DECEL_FACTORY_HARDEST}, 0),
                            ({**self.STOCK_COAST, "ACC_DECEL": ACC_DECEL_EMERGENCY - 1}, -1)):
      self._rx_stock(fields)
      self.assertEqual(fwd_bus, self.fwd_bus, fields)
      self.assertFalse(self.safety.get_chrysler_das_3_emergency_seen(), fields)
      stock = self._rx_stock(self.STOCK_COAST)
      self.assertEqual(-1, self.fwd_bus, fields)
      self.assertTrue(self._tx_seq(stock), fields)

  # gap fill: with controls not allowed, an exact copy stands in for a factory frame the fwd hook blocked

  def test_gap_fill_after_cancel(self):
    self._engage(self.STOCK_COAST)
    stock = self._rx_stock(self.STOCK_COAST)
    self.assertTrue(self._tx_seq(stock))
    off = self._rx_stock({**self.STOCK_COAST, "ACC_ACTIVE": 0})  # blocked: controls were allowed when it arrived
    self.assertFalse(self.safety.get_controls_allowed())

    # any changed bit, the checksum's included, is refused
    for bit in range(64):
      msg = self._das_3_msg(0, off)
      msg[0].data[bit // 8] ^= 1 << (bit % 8)
      self.assertFalse(self._tx(msg), bit)
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    # the exact copy fills the gap on bus 0, once
    self.assertTrue(self._tx_seq(off))
    self.assertEqual(off["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertFalse(self._tx_seq(off))

    # later factory frames reach bus 0 themselves: a copy is a repeat, and never stands in for a forwarded frame
    forwarded = self._rx_stock({**self.STOCK_COAST, "ACC_ACTIVE": 0})
    self.assertEqual(forwarded["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())
    self.assertFalse(self._tx_seq(forwarded))
    self.safety.set_chrysler_das_3_bus0_counter(-1)
    self.assertFalse(self._tx_seq(forwarded))

  def test_gap_fill_needs_blocked_frame(self):
    # a factory frame the mode's fwd hook never decided on (with a relay malfunction it isn't called) isn't blocked
    self._engage(self.STOCK_CRUISE)
    self._rx_stock(self.STOCK_CRUISE)  # blocked
    undecided = self._rx_stock(self.STOCK_CRUISE, fwd=False)
    self.safety.set_controls_allowed(False)
    self.safety.set_chrysler_das_3_bus0_counter(-1)
    self.assertFalse(self._tx_seq(undecided))

  def test_gap_fill_after_brake_press(self):
    self._rx(self._vehicle_moving_msg(10))
    self._engage(self.STOCK_COAST)
    stock = self._rx_stock(self.STOCK_COAST)  # blocked; the comma's copy arrives after the brake press
    self.assertEqual(-1, self.fwd_bus)
    self._rx(self._user_brake_msg(True))
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_seq(stock, **self._added_braking(2800)))
    self.assertTrue(self._tx_seq(stock))

  def test_tx_content_checked_against_matched_frame(self):
    self._engage(self.STOCK_CRUISE)
    previous = self._rx_stock({**self.STOCK_BRAKING, "ACC_DECEL": 2800})
    latest = self._rx_stock(self.STOCK_BRAKING)  # ACC_DECEL 3000, weaker
    # fine against the newest frame, but weaker braking than the frame it copies
    self.assertFalse(self._tx_das_3(previous, ACC_DECEL=3000))
    self.assertTrue(self._tx_das_3(previous))
    self.assertTrue(self._tx_das_3(latest))

    # torque: the frame before allowed +100 Nm, the newest none; each copy is held to its own frame
    previous = self._rx_stock(self.STOCK_CRUISE)
    latest = self._rx_stock(self.STOCK_COAST)
    self.assertTrue(self._tx_das_3(previous))
    self.assertFalse(self._tx_das_3(latest, ENGINE_TORQUE_REQUEST=2400, ENGINE_TORQUE_REQUEST_MAX=1))
    self.assertTrue(self._tx_das_3(latest))

    # a copy of the frame before is not held to the newest: it is a stale but valid frame, and the newest one's own
    # copy follows it (refusing it would only turn it into a missing frame)
    previous = self._rx_stock(self.STOCK_COAST)
    self._rx_stock(self.STOCK_BRAKING)
    self.assertTrue(self._tx_das_3(previous))

  def test_tx_checksum_must_be_valid(self):
    # the truck's modules discard a frame with a bad checksum: no replacement for the factory frame
    self._engage(self.STOCK_CRUISE)
    for fields in (self.STOCK_CRUISE, self.STOCK_BRAKING):
      stock = self._rx_stock(fields)
      for change in ({}, self._added_braking(2900) if not fields["ACC_DECEL_REQ"] else {"ACC_DECEL": 2900}):
        self.assertFalse(self._tx_das_3(stock, bad_checksum=True, **change), (fields, change))
        self.assertTrue(self._tx_das_3(stock, **change), (fields, change))

  # delivery timeout: while the comma replaces the factory frame, a DAS_3 must reach bus 0 at least every 60 ms

  def test_delivery_timeout_latches(self):
    # The comma keeps attempting, but only with copies the truck already has (repeats): refused without counting,
    # every factory frame stays blocked and nothing reaches bus 0. After more than 60 ms the engagement ends.
    engaged = self._engage(self.STOCK_CRUISE)  # reaches bus 0
    for _ in range(3):  # 20, 40 and 60 ms: still blocked
      self._rx_stock(self.STOCK_CRUISE)
      self.assertFalse(self._tx_seq(engaged))
      self.assertEqual(0, self.safety.get_chrysler_das_3_refusals())
      self.assertTrue(self.safety.get_controls_allowed())
    self.assertEqual(engaged["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())

    stock = self._rx_stock(self.STOCK_CRUISE)  # 80 ms with nothing on bus 0
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertEqual(stock["COUNTER"], self.safety.get_chrysler_das_3_bus0_counter())  # this frame reached the truck
    self.assertEqual(0, self._fwd_das_3())

    # factory cruise is still on, but engaging again needs a new ACC_ACTIVE rising edge
    for _ in range(3):
      self._rx_stock(self.STOCK_CRUISE)
      self.assertFalse(self.safety.get_controls_allowed())
    self._rx_stock({**self.STOCK_CRUISE, "ACC_ACTIVE": 0})
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self._tx_seq(stock))

  def test_delivery_timeout_boundary(self):
    # exactly 60 ms without a DAS_3 on bus 0 is still fine; an accepted copy starts the time again
    self._engage(self.STOCK_CRUISE)
    for _ in range(3):  # 20, 40, 60 ms: no copies
      stock = self._rx_stock(self.STOCK_CRUISE)
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx_seq(stock))
    for _ in range(3):  # 20, 40, 60 ms after that copy
      self._rx_stock(self.STOCK_CRUISE)
      self.assertTrue(self.safety.get_controls_allowed())
    self._rx_stock(self.STOCK_CRUISE)  # 80 ms
    self.assertFalse(self.safety.get_controls_allowed())

  def test_latch_and_lateral(self):
    # A fallback latch ends the engagement (controls_allowed false). Lateral in the Panda: with MADS enabled and
    # engaged it stays allowed (MADS doesn't act on controls_allowed falling); without MADS it ends with it.
    try:
      for mads in (True, False):
        self._reset_safety_hooks()
        self.safety.set_mads_params(mads, False, False)
        self._engage(self.STOCK_CRUISE)
        stock = self._rx_stock(self.STOCK_CRUISE)
        self.assertEqual(mads, self.safety.get_controls_allowed_lat())
        self.assertTrue(self.safety.get_lat_active())
        for _ in range(3):
          self.assertFalse(self._tx_seq(stock, ENGINE_TORQUE_REQUEST=4000))
        self.assertFalse(self.safety.get_controls_allowed())
        self._rx_stock(self.STOCK_CRUISE)
        self.assertEqual(mads, self.safety.get_controls_allowed_lat(), mads)
        self.assertEqual(mads, self.safety.get_lat_active(), mads)
    finally:
      self.safety.set_mads_params(False, False, False)

  def test_stored_factory_frames_expire(self):
    # a stored factory frame older than 100 ms is forgotten, so its timestamp can't read as fresh after the 32-bit
    # microsecond timer comes around again (2^32 us later)
    self._engage(self.STOCK_CRUISE)
    stock = self._rx_stock(self.STOCK_CRUISE)  # blocked
    t = self.now
    for elapsed, seen in ((STOCK_EXPIRY, True), (STOCK_EXPIRY + 1, False)):
      self._set_time(t + elapsed)
      self._fwd_das_3()
      self.assertEqual(seen, self.safety.get_chrysler_das_3_stock_seen(0), elapsed)
    self.assertFalse(self.safety.get_chrysler_das_3_stock_seen(1))

    self._set_time((t + 2**32) % 2**32)
    self.safety.set_chrysler_das_3_bus0_counter(-1)
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertFalse(self._tx_seq(stock))


if __name__ == "__main__":
  unittest.main()
