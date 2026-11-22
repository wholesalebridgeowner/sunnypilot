"""Exercise diesel readouts through the Chrysler receive path and the carStateSP wire schema."""
from contextlib import ExitStack, contextmanager
from importlib import import_module
import unittest
from unittest.mock import patch

from cereal import custom
from opendbc.car import structs
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR
from openpilot.selfdrive.car.helpers import convert_to_capnp


T0 = 10_000_000_000
ENGINE = b"68500630AF"
MODULE = "opendbc.car.chrysler.ram_dt_diesel"
READOUTS = ("fuelRemainingUsGal", "avgFuelEconomyUsMpg", "instantFuelEconomyUsMpg", "idleFuelUsGalPerHour", "rangeMiles", "defPercent")
# Received examples stripped of vehicle identifiers and time. ESP_8 includes the original counter and checksum.
REGEN_ON = (0x12D, bytes.fromhex("0003008000420960"), 0)
REGEN_OFF = (0x12D, bytes.fromhex("0502084040481004"), 0)
STILL = (0x79, bytes.fromhex("849804a200006005"), 0)
MOVING = (0x79, bytes.fromhex("00000000328390d7"), 0)  # 101.0234375 km/h
RUNNING = (0x25, bytes.fromhex("02aa080c06f37071"), 0)  # 682 RPM
LEVEL = (0x27A, bytes.fromhex("90940000107cad07"), 0)  # raw 148
FLOW = (0x13D, bytes.fromhex("0500016d04003000"), 0)  # synthetic 1024 counts = 8 L/h
RANGE = (0x27B, bytes.fromhex("a23407ff00000000"), 0)  # 0x234 km
ECONOMY = (0x3AA, bytes.fromhex("0104000000000000"), 0)  # 0x104 tenths of US MPG
US_GALLON_L = 3.785411784
MILE_KM = 1.609344


def car_interface(platform=CAR.RAM_1500_5TH_GEN, firmware=ENGINE, ecu="engine", address=0x7E0, brand="chrysler", sub=0, conflict=False):
  cp = CarInterface.get_non_essential_params(platform)
  if firmware is not None:
    fw = [structs.CarParams.CarFw.new_message(ecu=ecu, address=address, subAddress=sub, fwVersion=firmware, brand=brand)]
    if conflict:
      fw.append(structs.CarParams.CarFw.new_message(ecu=ecu, address=address, fwVersion=b"different", brand=brand))
    cp.carFw = fw
  return CarInterface(cp, CarInterface.get_non_essential_params_sp(cp, platform))


def receive(ci, packets, now=T0, valid=lambda index: True):
  _, sp = ci.update(packets, now_nanos=now, packet_valid=valid)
  wire = convert_to_capnp(sp).to_bytes()
  with custom.CarStateSP.from_bytes(wire) as msg:
    return msg.to_dict()


@contextmanager
def validated(*names):
  with ExitStack() as patches:
    for name in names:
      patches.enter_context(patch(f"{MODULE}.{name}_VALIDATED", True))
    yield


class RamDieselTests(unittest.TestCase):
  def assert_unknowns(self, msg):
    self.assertFalse(msg["dpfRegenValid"])
    self.assertFalse(msg["dpfRegenActive"])
    for field in READOUTS:
      self.assertFalse(msg[field + "Valid"], field)
      self.assertEqual(msg[field], 0.0, field)

  def test_unvalidated_readouts_stay_unknown_even_with_live_inputs(self):
    module = import_module(MODULE)
    for name in ("REGEN", "FUEL_LEVEL", "AVERAGE_MPG", "FUEL_FLOW", "INSTANT_MPG", "IDLE_RATE", "RANGE", "DEF"):
      self.assertIs(getattr(module, name + "_VALIDATED"), False, name)
    def unexpected_read(index):
      self.fail("the disabled producer requested CAN envelope metadata")
    msg = receive(car_interface(), [(T0, [REGEN_ON, LEVEL, FLOW, RANGE, ECONOMY, MOVING, RUNNING])], valid=unexpected_read)
    self.assertTrue(msg["dieselSupported"])
    self.assert_unknowns(msg)

  def test_old_wire_message_keeps_speed_limit_and_defaults_diesel_fields(self):
    # c087e26's two-field schema serializing a fresh 70 mph limit.
    blob = bytes.fromhex("00000000020000000000000001000000a857fa4101000000")
    with custom.CarStateSP.from_bytes(blob) as msg:
      self.assertTrue(msg.speedLimitValid)
      self.assertAlmostEqual(msg.speedLimit, 31.2928, places=5)
      self.assertFalse(msg.dieselSupported)
      self.assert_unknowns(msg.to_dict())

  def test_only_the_identified_ecodiesel_on_ram_dt_is_supported(self):
    with validated("REGEN"):
      for ci in (car_interface(firmware=None), car_interface(firmware=b"unrelated"), car_interface(ecu="eps"),
                 car_interface(address=0x7E1), car_interface(sub=1), car_interface(brand=""), car_interface(conflict=True),
                 car_interface(platform=CAR.RAM_HD_5TH_GEN)):
        msg = receive(ci, [(T0, [REGEN_ON])])
        self.assertFalse(msg["dieselSupported"])
        self.assert_unknowns(msg)

  def test_verified_regen_transitions_need_a_real_sample(self):
    with validated("REGEN"):
      ci = car_interface()
      self.assert_unknowns(receive(ci, [(T0, [MOVING])]))
      for offset, frame, active in ((1, REGEN_OFF, False), (2, REGEN_ON, True), (3, REGEN_OFF, False)):
        msg = receive(ci, [(T0 + offset, [frame])], T0 + offset)
        self.assertTrue(msg["dpfRegenValid"])
        self.assertEqual(msg["dpfRegenActive"], active)

  def test_wrong_source_old_invalid_and_malformed_regen(self):
    with validated("REGEN"):
      for source in (2, 128, 130):
        frame = (REGEN_ON[0], REGEN_ON[1], source)
        self.assert_unknowns(receive(car_interface(), [(T0, [frame])]))
      for bad in (REGEN_OFF[1][:-1], REGEN_OFF[1] + b"\0", b"\xff" * 8, b"\0" * 8, bytes.fromhex("000300ff00420960")):
        ci = car_interface()
        msg = receive(ci, [(T0, [REGEN_ON]), (T0 + 1, [(0x12D, bad, 0)])], T0 + 1)
        self.assert_unknowns(msg)
      ci = car_interface()
      self.assertFalse(receive(ci, [(T0, [REGEN_ON])], valid=lambda i: False)["dpfRegenValid"])
      self.assertFalse(receive(ci, [(T0, [REGEN_ON])], valid=None)["dpfRegenValid"])
      receive(ci, [(T0 + 20, [REGEN_ON])], T0 + 20)
      delayed = receive(ci, [(T0, [REGEN_OFF])], T0 + 21)
      self.assertTrue(delayed["dpfRegenValid"] and delayed["dpfRegenActive"])

  def test_cluster_readouts_convert_to_the_declared_units(self):
    with validated("FUEL_LEVEL", "AVERAGE_MPG", "RANGE", "DEF"):
      ci = car_interface()
      for step in range(5):
        stamp = T0 + step * 1_000_000_000
        self.assertFalse(receive(ci, [(stamp, [RANGE])], stamp)["rangeMilesValid"])
      now = T0 + 5_000_000_000
      msg = receive(ci, [(now, [LEVEL, ECONOMY, RANGE, REGEN_OFF])], now)
    for field, expected in (("fuelRemainingUsGal", 74 / US_GALLON_L), ("avgFuelEconomyUsMpg", 26.0),
                            ("rangeMiles", 564 / MILE_KM), ("defPercent", 72.0)):
      self.assertTrue(msg[field + "Valid"], field)
      self.assertAlmostEqual(msg[field], expected, places=4, msg=field)
    self.assertFalse(msg["dpfRegenValid"])

  def test_flow_readouts_require_their_own_validation_and_fresh_speed(self):
    options = ("FUEL_FLOW", "INSTANT_MPG", "IDLE_RATE")
    for speed, field in ((MOVING, "instantFuelEconomyUsMpg"), (STILL, "idleFuelUsGalPerHour")):
      with self.subTest(field=field):
        mode = "INSTANT_MPG" if speed == MOVING else "IDLE_RATE"
        for flags in (("FUEL_FLOW",), (mode,)):
          with validated(*flags):
            self.assertFalse(receive(car_interface(), [(T0, [speed, RUNNING, FLOW])])[field + "Valid"])
        with validated(*options):
          self.assertFalse(receive(car_interface(), [(T0, [FLOW])])[field + "Valid"])
          msg = receive(car_interface(), [(T0, [speed, RUNNING, FLOW])])
          expected = (101.0234375 / MILE_KM) / (8 / US_GALLON_L) if speed == MOVING else 8 / US_GALLON_L
          other = "instantFuelEconomyUsMpg" if speed == STILL else "idleFuelUsGalPerHour"
          self.assertTrue(msg[field + "Valid"])
          self.assertAlmostEqual(msg[field], expected, places=4)
          self.assertFalse(msg[other + "Valid"])
    with validated(*options):
      zero_flow = (FLOW[0], bytes.fromhex("0500016d00003000"), 0)
      self.assertFalse(receive(car_interface(), [(T0, [MOVING, zero_flow])])["instantFuelEconomyUsMpgValid"])
      self.assertFalse(receive(car_interface(), [(T0, [STILL, FLOW])])["idleFuelUsGalPerHourValid"])

  def test_each_value_expires_from_its_own_valid_sample(self):
    with validated("REGEN", "FUEL_LEVEL", "FUEL_FLOW", "INSTANT_MPG", "IDLE_RATE", "DEF"):
      ci = car_interface()
      receive(ci, [(T0, [LEVEL, FLOW, REGEN_ON, STILL, RUNNING])])
      now = T0 + 250_000_000
      self.assertTrue(receive(ci, [], now)["idleFuelUsGalPerHourValid"])
      self.assertFalse(receive(ci, [(now + 1, [REGEN_ON])], now + 1)["idleFuelUsGalPerHourValid"])
      now = T0 + 500_000_000
      self.assertTrue(receive(ci, [(now, [STILL, RUNNING])], now)["idleFuelUsGalPerHourValid"])
      self.assertFalse(receive(ci, [(now + 1, [STILL, RUNNING])], now + 1)["idleFuelUsGalPerHourValid"])
      now = T0 + 750_000_001
      self.assertTrue(receive(ci, [], now)["dpfRegenValid"])
      stale = receive(ci, [(now + 1, [FLOW])], now + 1)
      self.assertFalse(stale["dpfRegenValid"])
      self.assertFalse(stale["defPercentValid"])
      self.assertTrue(stale["fuelRemainingUsGalValid"])
      self.assertTrue(receive(ci, [], T0 + 2_000_000_000)["fuelRemainingUsGalValid"])
      self.assertFalse(receive(ci, [], T0 + 2_000_000_001)["fuelRemainingUsGalValid"])

  def test_field_sentinels_and_bad_speed_or_rpm_checksum_remain_unknown(self):
    with validated("FUEL_LEVEL", "AVERAGE_MPG", "FUEL_FLOW", "INSTANT_MPG", "IDLE_RATE", "DEF"):
      for address, data, field in ((0x27A, "90ff0000107cad07", "fuelRemainingUsGal"), (0x3AA, "01ff000000000000", "avgFuelEconomyUsMpg"),
                                   (0x3AA, "0000000000000000", "avgFuelEconomyUsMpg"), (0x12D, "0003008000650960", "defPercent"),
                                   (0x13D, "0500016dffff3000", "instantFuelEconomyUsMpg")):
        with self.subTest(address=address, data=data):
          out = receive(car_interface(), [(T0, [(address, bytes.fromhex(data), 0), MOVING, RUNNING])])
          self.assertFalse(out[field + "Valid"])
      for frame in (STILL, RUNNING):
        other = RUNNING if frame == STILL else STILL
        corrupt = (frame[0], frame[1][:-1] + bytes([frame[1][-1] ^ 1]), 0)
        out = receive(car_interface(), [(T0, [corrupt, other, FLOW])])
        self.assertFalse(out["idleFuelUsGalPerHourValid"])

  def test_optional_messages_do_not_participate_in_required_can_health(self):
    with validated("REGEN", "FUEL_LEVEL", "AVERAGE_MPG", "FUEL_FLOW", "INSTANT_MPG", "IDLE_RATE", "RANGE", "DEF"):
      ci, without = car_interface(), car_interface()
      optional = [REGEN_ON, LEVEL, ECONOMY, RANGE, FLOW]
      for i in range(10):
        stamp = T0 + 10_000_000 * i
        a, _ = ci.update([(stamp, optional)], now_nanos=stamp, packet_valid=lambda index: True)
        b, _ = without.update([(stamp, [])], now_nanos=stamp, packet_valid=lambda index: True)
        self.assertEqual((a.canValid, a.canTimeout), (b.canValid, b.canTimeout))
      for parser in ci.can_parsers.values():
        for address, _, _ in optional:
          self.assertNotIn(address, parser.message_states)

  def test_range_restarts_warmup_after_gap_or_invalid_sentinel(self):
    with validated("RANGE"):
      ci = car_interface()
      for i in range(6):
        t = T0 + i * 1_000_000_000
        msg = receive(ci, [(t, [RANGE])], t)
      self.assertTrue(msg["rangeMilesValid"])
      t += 2_000_000_001
      self.assertFalse(receive(ci, [(t, [RANGE])], t)["rangeMilesValid"])
      for i in range(5):
        t += 1_000_000_000
        msg = receive(ci, [(t, [RANGE])], t)
      self.assertTrue(msg["rangeMilesValid"])
      missing = (0x27B, bytes.fromhex("a3ff07ff00000000"), 0)
      self.assertFalse(receive(ci, [(t + 1, [missing])], t + 1)["rangeMilesValid"])
      self.assertFalse(receive(ci, [(t + 2, [RANGE])], t + 2)["rangeMilesValid"])


if __name__ == "__main__":
  unittest.main()
