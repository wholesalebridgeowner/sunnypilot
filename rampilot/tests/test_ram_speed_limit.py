import unittest

from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR
from openpilot.selfdrive.car.helpers import convert_to_capnp
from cereal import custom


ADDRESS = 0x265
SIGN = bytes.fromhex("0000000a000ec2ff")
T0 = 10_000_000_000
MAX_AGE = 1_500_000_000

# Anonymized decoder vectors. Each row retains only its frame, event validity and expected m/s.
# The two identical 0x0d rows came from separate recorded samples.
VECTORS = (
  (ADDRESS, 0, "0000000a000002ff", True, None),
  (ADDRESS, 0, "0000000a0005c2ff", True, 11.176),
  (ADDRESS, 0, "0000000a0006c2ff", True, 13.4112),
  (ADDRESS, 0, "0000000a0007c2ff", True, 15.6464),
  (ADDRESS, 0, "0000000a0008c2ff", True, 17.8816),
  (ADDRESS, 0, "0000000a0009c2ff", True, 20.1168),
  (ADDRESS, 0, "0000000a000ac2ff", True, 22.352),
  (ADDRESS, 0, "0000000a000bc2ff", True, 24.5872),
  (ADDRESS, 0, "0000000a000cc2ff", True, 26.8224),
  (ADDRESS, 0, "0000000a000ec2ff", True, 31.2928),
  (ADDRESS, 0, "0000000a003fc2ff", True, None),
  (ADDRESS, 2, "0000000a000ec2ff", True, None),
  (ADDRESS, 130, "0000000a000ec2ff", True, None),
  (0x264, 0, "0000000a000ec2ff", True, None),
  (ADDRESS, 0, "0000000a000ec2ff", False, None),
  (ADDRESS, 0, "0000000a000ec2", True, None),
  (ADDRESS, 0, "0000000a000ec2ff00", True, None),
  (ADDRESS, 0, "0000000a004ec2ff", True, None),
  (ADDRESS, 0, "0000000a001fc2ff", True, None),
  (ADDRESS, 0, "0000000a000dc2ff", True, None),
  (ADDRESS, 0, "0000000a000dc2ff", True, None),
  (ADDRESS, 0, "0000000a0004c2ff", True, None),
)


def interface(platform=CAR.RAM_1500_5TH_GEN):
  params = CarInterface.get_non_essential_params(platform)
  return CarInterface(params, CarInterface.get_non_essential_params_sp(params, platform))


class RamSpeedLimitTests(unittest.TestCase):
  def assert_limit(self, state, expected):
    # Use the same conversion as card.state_update and verify the value as received on the wire.
    with custom.CarStateSP.from_bytes(convert_to_capnp(state).to_bytes()) as message:
      self.assertEqual(message.speedLimitValid, expected is not None)
      self.assertAlmostEqual(message.speedLimit, 0.0 if expected is None else expected, places=5)

  def test_vectors_through_chrysler_interface(self):
    for index, (address, bus, payload, valid, expected) in enumerate(VECTORS):
      with self.subTest(index=index):
        frames = [(address, bytes.fromhex(payload), bus)]
        _, state = interface().update([(T0, frames)], now_nanos=T0, packet_valid=lambda _: valid)
        self.assert_limit(state, expected)

  def test_age_uses_the_matching_sample_across_publications(self):
    ci = interface()
    _, fresh = ci.update([(T0, [(ADDRESS, SIGN, 0)])], now_nanos=T0)
    self.assert_limit(fresh, 31.2928)
    now = T0 + MAX_AGE
    # Other buses, echo and different addresses are active. The original sample still expires on time.
    traffic = [(ADDRESS, SIGN, 2), (ADDRESS, SIGN, 130), (0x264, SIGN, 0)]
    _, last = ci.update([(now, traffic)], now_nanos=now)
    self.assert_limit(last, 31.2928)
    _, expired = ci.update([], now_nanos=now + 1)
    self.assert_limit(expired, None)
    _, backlogged = ci.update([(T0, [(ADDRESS, SIGN, 0)]), (now + 2, traffic)], now_nanos=now + 2)
    self.assert_limit(backlogged, None)

  def test_unusable_sample_replaces_previous_sign(self):
    for payload, valid in ((bytes.fromhex("0000000a003fc2ff"), True), (SIGN[:-1], True), (SIGN, False)):
      with self.subTest(payload=payload, valid=valid):
        ci = interface()
        events = [(T0, [(ADDRESS, SIGN, 0)]), (T0 + 1, [(ADDRESS, payload, 0)])]
        _, state = ci.update(events, now_nanos=T0 + 1, packet_valid=lambda index: index == 0 or valid)
        self.assert_limit(state, None)
        _, recovered = ci.update([(T0 + 2, [(ADDRESS, SIGN, 0)])], now_nanos=T0 + 2)
        self.assert_limit(recovered, 31.2928)

  def test_wrong_clock_and_restart_withhold(self):
    ci = interface()
    _, future = ci.update([(T0, [(ADDRESS, SIGN, 0)])], now_nanos=T0 - 1)
    self.assert_limit(future, None)
    _, restarted = interface().update([], now_nanos=T0)
    self.assert_limit(restarted, None)

  def test_replay_rewind_cannot_revive_an_expired_limit(self):
    ci = interface()
    _, fresh = ci.update([(T0, [(ADDRESS, SIGN, 0)])])
    self.assert_limit(fresh, 31.2928)
    expired_at = T0 + MAX_AGE + 1
    _, expired = ci.update([(expired_at, [])])
    self.assert_limit(expired, None)
    _, rewound = ci.update([(T0 + 1, [])])
    self.assert_limit(rewound, None)
    _, recovered = ci.update([(expired_at + 1, [(ADDRESS, SIGN, 0)])])
    self.assert_limit(recovered, 31.2928)

  def test_other_chrysler_platforms_do_not_publish_this_sign(self):
    for platform in (CAR.RAM_HD_5TH_GEN, CAR.CHRYSLER_PACIFICA_2018):
      with self.subTest(platform=platform):
        _, state = interface(platform).update([(T0, [(ADDRESS, SIGN, 0)])], now_nanos=T0)
        self.assert_limit(state, None)

  def test_optional_message_is_not_registered_for_can_health(self):
    with_sign, without_sign = interface(), interface()
    for i in range(12):
      now = T0 + 10_000_000 * i
      seen, _ = with_sign.update([(now, [(ADDRESS, SIGN, 0)])], now_nanos=now)
      missing, fields = without_sign.update([(now, [])], now_nanos=now)
      self.assertEqual((seen.canValid, seen.canTimeout), (missing.canValid, missing.canTimeout))
      self.assert_limit(fields, None)
    for parser in with_sign.can_parsers.values():
      self.assertNotIn(ADDRESS, parser.message_states)


if __name__ == "__main__":
  unittest.main()
