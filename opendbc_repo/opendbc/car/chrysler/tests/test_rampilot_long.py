"""RamPilot long for the Ram 1500 DT: the comma re-sends each factory DAS_3 and can only add braking.

Runs the real CarInterface (CarState, CarController, packer, parser) with simulated CAN at 100 Hz."""
import dataclasses
import math
import random

import pytest

from opendbc.can import CANPacker
from opendbc.can.dbc import DBC
from opendbc.can.packer import set_value
from opendbc.can.parser import get_raw_value
from opendbc.car import Bus, CanData, gen_empty_fingerprint, structs
from opendbc.car.chrysler import rampilot_long
from opendbc.car.chrysler.chryslercan import chrysler_checksum
from opendbc.car.chrysler.interface import CarInterface
from opendbc.car.chrysler.values import CAR, DBC as CHRYSLER_DBC, ChryslerSafetyFlags

RAM_DT = CAR.RAM_1500_5TH_GEN
DBC_NAME = CHRYSLER_DBC[RAM_DT][Bus.pt]
DAS_3 = DBC(DBC_NAME).name_to_msg["DAS_3"]
DT_NS = 10_000_000  # 100 Hz controller
ECHO = rampilot_long.DAS_3_ECHO_SRC  # a frame the Panda transmitted on bus 0, returned to the host
ButtonType = structs.CarState.ButtonEvent.Type

# Factory fields the comma must never change
COPIED = sorted(set(DAS_3.sigs) - set(rampilot_long.CHANGEABLE) - {"CHECKSUM"})

# Bits of DAS_3 that no DBC signal covers (outside the checksum byte)
_covered = bytearray(8)
for _sig in DAS_3.sigs.values():
  set_value(_covered, _sig, (1 << _sig.size) - 1)
UNDEFINED_BITS = bytes((~b) & 0xFF for b in _covered[:7]) + b"\x00"


def get_params(candidate, alpha_long):
  fingerprint = gen_empty_fingerprint()
  CP = CarInterface.get_params(candidate, fingerprint, [], alpha_long=alpha_long, is_release=False, docs=False)
  CP_SP = CarInterface.get_params_sp(CP, candidate, fingerprint, [], alpha_long=alpha_long, docs=False)
  return CP, CP_SP


def decode(dat: bytes) -> dict[str, int]:
  return {name: get_raw_value(dat, sig) for name, sig in DAS_3.sigs.items()}


def raw_decel(accel: float) -> int:
  sig = DAS_3.sigs["ACC_DECEL"]
  return int(math.floor((accel - sig.offset) / sig.factor + 0.5))


def raw_torque(nm: float) -> int:
  sig = DAS_3.sigs["ENGINE_TORQUE_REQUEST"]
  return int(math.floor((nm - sig.offset) / sig.factor + 0.5))


def with_undefined_bits(dat: bytes) -> bytes:
  """The same frame with every bit outside the DBC's signals set, checksum recomputed."""
  out = bytearray(a | b for a, b in zip(dat, UNDEFINED_BITS, strict=True))
  out[7] = chrysler_checksum(DAS_3.address, None, out)
  return bytes(out)


CRUISING = {  # factory ACC active and asking for engine torque
  "ENGINE_TORQUE_REQUEST": 150., "ENGINE_TORQUE_REQUEST_MAX": 1, "ACC_DECEL": 4.0, "ACC_DECEL_REQ": 0,
  "ACC_AVAILABLE": 1, "ACC_ACTIVE": 1, "GR_MAX_REQ": 8, "DISABLE_FUEL_SHUTOFF": 1, "ACC_FAULTED": 2,
}
COASTING = {**CRUISING, "ENGINE_TORQUE_REQUEST": -40., "ENGINE_TORQUE_REQUEST_MAX": 0}
BRAKING = {  # factory ACC active and requesting -1.5 m/s^2
  "ENGINE_TORQUE_REQUEST": -20., "ENGINE_TORQUE_REQUEST_MAX": 0, "ACC_DECEL": -1.5, "ACC_DECEL_REQ": 1,
  "ACC_AVAILABLE": 1, "ACC_ACTIVE": 1, "GR_MAX_REQ": 6, "DISABLE_FUEL_SHUTOFF": 1, "ACC_FAULTED": 2,
}
ACC_OFF = {"ENGINE_TORQUE_REQUEST": 0., "ACC_DECEL": 4.0, "ACC_AVAILABLE": 1, "ACC_ACTIVE": 0}


class Harness:
  """Ram DT CarInterface fed one 10 ms CAN packet per step: vehicle speed, gas pedal and cruise buttons on bus 0, plus
  any factory DAS_3 frames on bus 2."""

  def __init__(self, alpha_long=True, observe_raw=True):
    self.CP, self.CP_SP = get_params(RAM_DT, alpha_long)
    self.CI = CarInterface(self.CP, self.CP_SP)
    if not observe_raw:
      self.CI.CS.observe_can = None  # CarState then has no raw frames: the controller packs from parsed signals
    self.packer = CANPacker(DBC_NAME)
    self.t = 0
    self.counter = 0
    self.speed_kph = 72.  # moving unless a test stops the truck
    self.gas = False
    self.resume_button = False
    self.accel_button = False
    self.step([])  # the first update registers the messages in the parsers

  def factory(self, values, counter=None) -> bytes:
    """Next factory DAS_3 (bus 2) with a valid counter and checksum."""
    if counter is None:
      counter = self.counter
      self.counter = (self.counter + 1) % 16
    return self.packer.make_can_msg("DAS_3", 2, {**values, "COUNTER": counter})[1]

  def pt_frames(self):
    p = self.packer
    return [CanData(*p.make_can_msg("ESP_8", 0, {"Vehicle_Speed": self.speed_kph})),
            CanData(*p.make_can_msg("ECM_5", 0, {"Accelerator_Position": 20. if self.gas else 0.})),
            CanData(*p.make_can_msg("CRUISE_BUTTONS", 0, {"ACC_Resume": int(self.resume_button),
                                                           "ACC_Accel": int(self.accel_button)}))]

  def step(self, das_3_frames, long_active=False, accel=0., echoes=(), rejected=()):
    """`echoes`: DAS_3 frames the Panda transmitted on bus 0 since the last step, returned to the host as src 128.
    `rejected`: comma copies the Panda refused, returned as src 192."""
    self.t += DT_NS
    frames = (self.pt_frames() + [CanData(DAS_3.address, d, 2) for d in das_3_frames] +
              [CanData(DAS_3.address, d, ECHO) for d in echoes] + [CanData(DAS_3.address, d, 192) for d in rejected])
    self.CI.update([(self.t, frames)], now_nanos=self.t)
    CC = structs.CarControl()
    CC.enabled = long_active
    CC.latActive = long_active
    CC.longActive = long_active
    CC.actuators.accel = accel
    actuators, sends = self.CI.apply(CC.as_reader(), structs.CarControlSP(), self.t)
    das_3 = [s for s in sends if s[0] == DAS_3.address]
    assert all(s[2] == 0 for s in das_3), "DAS_3 goes to bus 0"
    return actuators, [bytes(s[1]) for s in das_3]

  def send_one(self, factory_frame, long_active, accel) -> tuple[bytes, float]:
    """One factory frame in, exactly one comma DAS_3 out."""
    actuators, out = self.step([factory_frame], long_active, accel)
    assert len(out) == 1
    return out[0], actuators.accel


@pytest.fixture(params=[True, False], ids=["raw", "packed"])
def harness(request):
  return Harness(observe_raw=request.param)


def assert_copied(out: bytes, factory: bytes):
  got, want = decode(out), decode(factory)
  assert {k: got[k] for k in COPIED} == {k: want[k] for k in COPIED}


def assert_checksum_valid(out: bytes):
  assert out[7] == chrysler_checksum(DAS_3.address, None, bytearray(out))


class TestPassThrough:
  @pytest.mark.parametrize("values, long_active, accel", [
    (CRUISING, False, -3.0),  # long not active: factory frame unchanged, even if the comma would brake
    (BRAKING, False, -3.0),
    (CRUISING, True, 0.5),  # comma does not want to brake
    (CRUISING, True, 0.),
    (COASTING, True, 0.),
    (BRAKING, True, -1.0),  # factory brakes harder than the comma wants
    (BRAKING, True, -1.5),
    (ACC_OFF, True, -2.0),  # factory cruise not active: no added braking
    (CRUISING, True, float("nan")),
  ])
  def test_byte_equal_when_not_adding_braking(self, harness, values, long_active, accel):
    for _ in range(3):
      factory = harness.factory(values)
      out, applied = harness.send_one(factory, long_active, accel)
      assert out == factory
      assert applied == 0.

  @pytest.mark.parametrize("values", [CRUISING, BRAKING, COASTING])
  def test_undefined_bits_pass_through_with_raw_frames(self, values):
    h = Harness()
    factory = with_undefined_bits(h.factory(values))
    assert h.CI.CS.das_3_raw is None
    out, _ = h.send_one(factory, True, 1.0)
    assert out == factory
    assert h.CI.CS.das_3_raw == factory


class TestAddedBraking:
  @pytest.mark.parametrize("values", [CRUISING, COASTING])
  @pytest.mark.parametrize("accel", [-0.3, -1.2, -3.5])
  def test_braking_added_and_torque_cut(self, harness, values, accel):
    factory = harness.factory(values)
    out, applied = harness.send_one(factory, True, accel)
    got, want = decode(out), decode(factory)
    assert got["ACC_DECEL_REQ"] == rampilot_long.DECEL_REQ_ACTIVE == 1
    assert got["ACC_DECEL"] == raw_decel(accel)
    assert got["ENGINE_TORQUE_REQUEST"] == min(want["ENGINE_TORQUE_REQUEST"], raw_torque(0.))
    assert got["ENGINE_TORQUE_REQUEST_MAX"] == 0
    assert_copied(out, factory)
    assert_checksum_valid(out)
    assert applied == pytest.approx(accel)

  def test_comma_request_limited_to_accel_min(self, harness):
    factory = harness.factory(CRUISING)
    out, applied = harness.send_one(factory, True, -6.0)
    raw = decode(out)["ACC_DECEL"]
    assert raw == raw_decel(rampilot_long.ACCEL_MIN) == 2559  # -3.4998 m/s^2
    assert raw >= 2558  # commaai/panda #1719 min_accel (-3.5042 m/s^2)
    assert applied == rampilot_long.ACCEL_MIN

  def test_undefined_bits_kept_when_braking(self):
    h = Harness()
    factory = with_undefined_bits(h.factory(CRUISING))
    out, _ = h.send_one(factory, True, -1.0)
    assert out != factory
    assert all((o & u) == u for o, u in zip(out, UNDEFINED_BITS, strict=True))
    assert_copied(out, factory)
    assert_checksum_valid(out)

  def test_never_more_torque_or_less_braking(self, harness):
    for values in (CRUISING, COASTING, BRAKING, ACC_OFF):
      for long_active in (False, True):
        for accel in (-5., -3.5, -2.0, -1.5, -1.0, -0.01, 0., 0.5, 2.0):
          factory = harness.factory(values)
          out, _ = harness.send_one(factory, long_active, accel)
          got, want = decode(out), decode(factory)
          assert got["ENGINE_TORQUE_REQUEST"] <= want["ENGINE_TORQUE_REQUEST"]
          assert got["ENGINE_TORQUE_REQUEST_MAX"] <= want["ENGINE_TORQUE_REQUEST_MAX"]
          if want["ACC_DECEL_REQ"]:
            assert got["ACC_DECEL_REQ"] == want["ACC_DECEL_REQ"]
            assert got["ACC_DECEL"] <= want["ACC_DECEL"]
          assert_copied(out, factory)
          assert_checksum_valid(out)


class TestFactoryBrakingFrames:
  """The Panda never blocks a factory frame that requests braking or brake prep: the comma adds nothing to it, still
  sends an unchanged copy (refused by the Panda), and keeps its braking and hold state for the next frame."""

  @pytest.mark.parametrize("fields", [
    {"ACC_DECEL_REQ": 1, "ACC_DECEL": -0.3}, {"ACC_DECEL_REQ": 1, "ACC_DECEL": -2.5},
    {"ACC_DECEL_REQ": 1, "ACC_DECEL": -4.0}, {"ACC_DECEL_REQ": 3, "ACC_DECEL": -1.0},
    {"ACC_DECEL_REQ": 1, "ACC_DECEL": 4.0},  # a request with no deceleration value is still a request
    {"COLLISION_BRK_PREP": 1}, {"ACC_BRK_PREP": 1},
    {"COLLISION_BRK_PREP": 1, "ACC_BRK_PREP": 1, "ACC_DECEL_REQ": 1, "ACC_DECEL": -1.0},
  ])
  @pytest.mark.parametrize("accel", [-6.0, -3.0, -1.0, 0.5])
  def test_pass_through(self, harness, fields, accel):
    factory = harness.factory({**CRUISING, **fields})
    out, applied = harness.send_one(factory, True, accel)
    assert out == factory
    assert applied == 0.

  def test_raw_and_parsed_tests_agree(self):
    rng = random.Random(7)
    p = CANPacker(DBC_NAME)
    for _ in range(2000):
      values = {**CRUISING, "ACC_DECEL_REQ": rng.choice([0, 0, 1, 2, 7]), "COLLISION_BRK_PREP": rng.choice([0, 0, 1]),
                "ACC_BRK_PREP": rng.choice([0, 0, 1]), "ACC_DECEL": rng.uniform(-16, 4), "COUNTER": rng.randrange(16)}
      dat = p.make_can_msg("DAS_3", 2, values)[1]
      parsed = {name: get_raw_value(dat, sig) * sig.factor + sig.offset for name, sig in DAS_3.sigs.items()}
      assert rampilot_long.factory_brakes_raw(dat) == rampilot_long.factory_brakes(parsed)

  def test_braking_state_carries_over(self, harness):
    assert run(harness, [(True, -0.5), (True, -0.1)]) == [True, True]  # braking, held by the keep rule
    assert run(harness, [(True, -0.1)] * 25, BRAKING) == [False] * 25  # 0.5 s of factory braking frames
    assert run(harness, [(True, -0.1)]) == [True]  # no restart of the hysteresis: -0.1 keeps braking

  def test_no_braking_state_is_started_by_them(self, harness):
    assert run(harness, [(True, -0.1)] * 10, BRAKING) == [False] * 10
    assert run(harness, [(True, -0.1)]) == [False]  # -0.1 does not start braking

  def test_copies_keep_coming_through_a_long_factory_braking_stretch(self, harness):
    sent = []
    for _ in range(100):  # 2 s of factory braking at 100 Hz updates, one factory frame per 20 ms
      factory = harness.factory(BRAKING)
      _, out = harness.step([factory], True, -2.0)
      assert out == [factory]
      sent += out
      _, out = harness.step([], True, -2.0)
      assert out == []
    counters = [decode(o)["COUNTER"] for o in sent]
    assert all((b - a) % 16 == 1 for a, b in zip(counters, counters[1:], strict=False))
    factory = harness.factory(CRUISING)  # the factory lets go: the comma's braking applies on this very frame
    out, _ = harness.send_one(factory, True, -2.0)
    assert decode(out)["ACC_DECEL"] == raw_decel(-2.0) and decode(out)["COUNTER"] == (counters[-1] + 1) % 16


class TestCopiedFields:
  @pytest.mark.parametrize("collision, brake_prep", [(0, 0), (1, 0), (0, 1), (1, 1)])
  @pytest.mark.parametrize("accel", [-3.0, 0.5])
  def test_aeb_and_brake_prep_bits_copied(self, harness, collision, brake_prep, accel):
    for values in (CRUISING, BRAKING):
      factory = harness.factory({**values, "COLLISION_BRK_PREP": collision, "ACC_BRK_PREP": brake_prep})
      out, _ = harness.send_one(factory, True, accel)
      got = decode(out)
      assert (got["COLLISION_BRK_PREP"], got["ACC_BRK_PREP"]) == (collision, brake_prep)
      assert_copied(out, factory)

  @pytest.mark.parametrize("fields", [
    {"ACC_STANDSTILL": 1, "ACC_GO": 0},
    {"ACC_STANDSTILL": 0, "ACC_GO": 1},
    {"GR_MAX_REQ": 15, "DISABLE_FUEL_SHUTOFF": 0, "ACC_FAULTED": 0},
    {"ACC_FAULTED": 3},
  ])
  def test_other_factory_fields_copied(self, harness, fields):
    factory = harness.factory({**CRUISING, **fields})
    out, _ = harness.send_one(factory, True, -2.0)
    assert_copied(out, factory)
    assert decode(out)["ACC_ACTIVE"] == 1

  def test_counter_copied(self, harness):
    for i in range(40):  # wraps the 4-bit counter twice
      long_active, accel = (True, -2.0) if i % 3 else (True, 1.0)
      factory = harness.factory(BRAKING if i % 2 else CRUISING)
      out, _ = harness.send_one(factory, long_active, accel)
      assert decode(out)["COUNTER"] == decode(factory)["COUNTER"] == i % 16
      assert_checksum_valid(out)

  def test_checksum_valid_and_parseable(self, harness):
    from opendbc.can import CANParser
    parser = CANParser(DBC_NAME, [("DAS_3", 50)], 0)
    for i in range(20):
      factory = harness.factory(CRUISING)
      out, _ = harness.send_one(factory, True, -1.0 - i * 0.1)
      assert_checksum_valid(out)
      assert parser.update([(harness.t, [CanData(DAS_3.address, out, 0)])]) == {DAS_3.address}
      assert parser.vl["DAS_3"]["ACC_DECEL"] == pytest.approx(-1.0 - i * 0.1, abs=DAS_3.sigs["ACC_DECEL"].factor)


class TestRate:
  def test_one_das_3_per_factory_frame(self, harness):
    sent = 0
    for i in range(100):  # 1 s at 100 Hz; the factory sends at 50 Hz
      frames = [harness.factory(CRUISING)] if i % 2 == 0 else []
      _, out = harness.step(frames, True, -1.0)
      assert len(out) == len(frames)
      sent += len(out)
    assert sent == 50

  def test_two_frame_batch_answers_both(self, harness):
    first, second = harness.factory(CRUISING), harness.factory({**BRAKING, "GR_MAX_REQ": 5})
    _, out = harness.step([first, second], True, 1.0)
    assert out == [first, second]

  def test_three_frame_batch_answers_the_last_two_in_order(self, harness):
    run(harness, [(True, 1.0)] * 10)  # counters 0-9 answered
    frames = [harness.factory({**CRUISING, "GR_MAX_REQ": 3}),  # counter 10: skipped
              harness.factory({**BRAKING, "GR_MAX_REQ": 4, "ACC_BRK_PREP": 1}),  # 11
              harness.factory({**CRUISING, "GR_MAX_REQ": 5, "ENGINE_TORQUE_REQUEST": 90.})]  # 12
    _, out = harness.step(frames, True, 1.0)
    assert out == frames[1:]  # pass-through: each copy is its own factory frame, byte for byte

    frames = [harness.factory({**CRUISING, "GR_MAX_REQ": 3}),  # 13: skipped
              harness.factory({**CRUISING, "GR_MAX_REQ": 4, "ENGINE_TORQUE_REQUEST": 120.}),  # 14
              harness.factory({**CRUISING, "GR_MAX_REQ": 5, "ENGINE_TORQUE_REQUEST": 90.})]  # 15
    actuators, out = harness.step(frames, True, -2.0)
    assert [decode(o)["COUNTER"] for o in out] == [14, 15]
    for o, factory in zip(out, frames[1:], strict=True):
      assert_copied(o, factory)
      assert_checksum_valid(o)
      got = decode(o)
      assert got["ACC_DECEL"] == raw_decel(-2.0)
      assert got["ACC_DECEL_REQ"] == 1
      assert got["ENGINE_TORQUE_REQUEST"] == min(decode(factory)["ENGINE_TORQUE_REQUEST"], raw_torque(0.))
      assert got["ENGINE_TORQUE_REQUEST_MAX"] == 0
    assert (decode(out[0])["GR_MAX_REQ"], decode(out[1])["GR_MAX_REQ"]) == (4, 5)
    assert actuators.accel == pytest.approx(-2.0)

    # a batch with a factory braking frame: that copy is unchanged, the other carries the comma's braking
    frames = [harness.factory(CRUISING), harness.factory({**BRAKING, "ACC_BRK_PREP": 1}), harness.factory(CRUISING)]
    _, out = harness.step(frames, True, -2.0)
    assert out[0] == frames[1]
    assert decode(out[1])["ACC_DECEL"] == raw_decel(-2.0) and decode(out[1])["COUNTER"] == 2

  def test_batch_state_carried_from_the_first_frame_to_the_second(self, harness):
    # In a hold, a resume press between factory frames: the first frame of the next batch uses it up and releases the
    # hold, so the second frame must pass through too (with the state from before the batch it would still hold).
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 3) == [True] * 3
    harness.speed_kph = 0.
    out, _ = harness.send_one(harness.factory(CRUISING), True, 0.5)
    assert decode(out)["ACC_DECEL"] == raw_decel(rampilot_long.HOLD_ACCEL)
    harness.resume_button = True
    harness.step([], True, 0.5)
    first, second = harness.factory(CRUISING), harness.factory(CRUISING)
    _, out = harness.step([first, second], True, 0.5)
    assert out == [first, second]

  def test_batch_older_frame_with_a_backward_counter_is_skipped(self, harness):
    run(harness, [(True, 1.0)] * 6)  # counters 0-5 answered
    older, newer = harness.factory(CRUISING, counter=5), harness.factory(CRUISING, counter=6)
    _, out = harness.step([older, newer], True, 1.0)
    assert out == [newer]
    older, newer = harness.factory(CRUISING, counter=4), harness.factory(CRUISING, counter=7)
    _, out = harness.step([older, newer], True, 1.0)
    assert out == [newer]

  def test_batch_with_undefined_bits_uses_each_frames_own_bytes(self):
    h = Harness()
    first = with_undefined_bits(h.factory(CRUISING))
    second = h.factory(CRUISING)
    _, out = h.step([first, second], True, 1.0)
    assert out == [first, second]
    _, out = h.step([h.factory(CRUISING), with_undefined_bits(h.factory(CRUISING))], True, -1.0)
    assert all((o & u) == 0 for o, u in zip(out[0][:7], UNDEFINED_BITS, strict=False))
    assert all((o & u) == u for o, u in zip(out[1], UNDEFINED_BITS, strict=True))

  def test_bad_factory_checksum_sends_nothing(self, harness):
    good = harness.factory(CRUISING)
    _, out = harness.step([good], True, -1.0)
    assert len(out) == 1
    bad = bytearray(harness.factory(CRUISING))
    bad[7] ^= 0xFF
    _, out = harness.step([bytes(bad)], True, -1.0)
    assert out == []

  def test_no_factory_frame_no_das_3(self, harness):
    for _ in range(10):
      _, out = harness.step([], True, -2.0)
      assert out == []


class TestLongOff:
  def test_no_das_3_when_long_control_off(self):
    h = Harness(alpha_long=False)
    for i in range(20):
      accel = -2.0 if i % 2 else 1.0
      actuators, out = h.step([h.factory(BRAKING if i % 3 else CRUISING)], True, accel)
      assert out == []
      assert actuators.accel == accel  # unchanged from before RamPilot long

  def test_carstate_identical_with_and_without_long(self):
    on, off = Harness(alpha_long=True), Harness(alpha_long=False)
    for i in range(30):
      values = BRAKING if i % 4 == 0 else CRUISING
      frames = [on.factory(values)]
      assert off.factory(values) == frames[0]
      on.step(frames, True, -1.0)
      off.step(frames, True, -1.0)
      assert on.CI.CS.out.to_dict() == off.CI.CS.out.to_dict()

  def test_no_das_3_history_without_long(self):
    h = Harness(alpha_long=False)
    CS = h.CI.CS
    assert CS.observe_can == CS.observe_display_data  # the hook as it was before RamPilot long
    assert not CS.rampilot_long and CS.das_3_delivery is None
    for _ in range(20):
      h.step([h.factory(CRUISING), h.factory(BRAKING)], True, -2.0, echoes=[h.factory(CRUISING, counter=0)])
      assert (CS.das_3, CS.das_3_raw, CS.das_3_frames, CS.das_3_seen, CS.das_3_count) == (None, None, [], [], 0)
      sp = h.CI.CS.out_sp  # no RamPilot long fields set
      assert (sp.rampilotFactoryEmergency, sp.rampilotDas3EchoAgeMs, sp.rampilotBrakingNotDelivered,
              sp.rampilotFactorySinceEcho) == (False, 0., 0, 0)
    assert "DAS_3" in h.CI.can_parsers[Bus.cam].vl  # parsed as before for the cruise state, nothing more


class TestCarState:
  def test_das_3_parsed_every_frame(self):
    h = Harness()
    assert h.CI.CS.das_3 is None
    for i in range(5):
      factory = h.factory({**BRAKING, "COLLISION_BRK_PREP": i % 2})
      h.step([factory])
      das_3 = h.CI.CS.das_3
      assert set(das_3) == set(DAS_3.sigs)  # every signal, COUNTER and CHECKSUM included
      assert das_3["COUNTER"] == i
      assert das_3["CHECKSUM"] == factory[7]
      assert das_3["ACC_DECEL"] == pytest.approx(-1.5, abs=DAS_3.sigs["ACC_DECEL"].factor)
      assert das_3["COLLISION_BRK_PREP"] == i % 2
      assert h.CI.CS.das_3_count == i + 1
      assert h.CI.CS.das_3_raw == factory
      assert h.CI.CS.das_3_frames == [(das_3, factory)]

  def test_every_frame_of_a_batch_with_its_own_values_and_bytes(self):
    h = Harness()
    frames = [h.factory({**CRUISING, "GR_MAX_REQ": g}) for g in (3, 4, 5, 6)]
    frames[1] = with_undefined_bits(frames[1])
    bad = bytearray(frames[2])
    bad[7] ^= 0xFF  # fails the checksum: not parsed
    frames[2] = bytes(bad)
    h.step(frames)
    got = h.CI.CS.das_3_frames
    assert [f["GR_MAX_REQ"] for f, _ in got] == [3, 4, 6]
    assert [f["COUNTER"] for f, _ in got] == [0, 1, 3]
    assert [raw for _, raw in got] == [frames[0], frames[1], frames[3]]
    assert h.CI.CS.das_3_count == 3
    h.step([])
    assert h.CI.CS.das_3_frames == []

  def test_factory_emergency_flag_in_carstatesp(self):
    def decel(raw):
      return raw * DAS_3.sigs["ACC_DECEL"].factor + DAS_3.sigs["ACC_DECEL"].offset

    h = Harness()
    for values, emergency in (
      (CRUISING, False),
      ({**CRUISING, "COLLISION_BRK_PREP": 1}, True),
      ({**CRUISING, "ACC_BRK_PREP": 1}, True),  # ACC brake prep alone is an emergency frame
      ({**BRAKING, "ACC_DECEL": decel(2763)}, True),  # -2.5027 m/s^2: stronger than -2.5
      ({**BRAKING, "ACC_DECEL": decel(2764)}, False),  # -2.4979 m/s^2: the Panda's limit itself
      ({**BRAKING, "ACC_DECEL": -2.16}, False),  # the strongest factory request in the recorded drives
      ({**BRAKING, "ACC_DECEL": -1.5}, False),
      ({**BRAKING, "ACC_DECEL": decel(2000), "ACC_DECEL_REQ": 0}, False),  # no decel request
    ):
      h.step([h.factory(values)])
      assert h.CI.CS.out_sp.rampilotFactoryEmergency is emergency, values
    h.step([h.factory({**CRUISING, "COLLISION_BRK_PREP": 1})])
    h.step([])  # no new factory frame: still the newest frame's state
    assert h.CI.CS.out_sp.rampilotFactoryEmergency
    h.step([h.factory({**CRUISING, "COLLISION_BRK_PREP": 1}), h.factory(CRUISING)])  # older frame of a batch
    assert h.CI.CS.out_sp.rampilotFactoryEmergency
    h.step([h.factory(CRUISING)])
    assert not h.CI.CS.out_sp.rampilotFactoryEmergency

  def test_factory_emergency_flag_only_with_long(self):
    assert "rampilotFactoryEmergency" in {f.name for f in dataclasses.fields(structs.CarStateSP)}
    h = Harness(alpha_long=False)
    h.step([h.factory({**CRUISING, "COLLISION_BRK_PREP": 1})])
    assert not h.CI.CS.out_sp.rampilotFactoryEmergency

  def test_emergency_threshold_is_the_pandas(self):
    assert raw_decel(rampilot_long.EMERGENCY_DECEL) == rampilot_long.EMERGENCY_DECEL_RAW == 2764
    sig = DAS_3.sigs["ACC_DECEL"]
    frame = {name: 0. for name in DAS_3.sigs} | {"ACC_DECEL_REQ": 1.}
    for raw in range(4096):  # every raw value: the parsed-value test equals the Panda's raw test
      assert rampilot_long.is_emergency(frame | {"ACC_DECEL": raw * sig.factor + sig.offset}) == (raw < 2764), raw
    assert rampilot_long.is_emergency(frame | {"ACC_DECEL_REQ": 0., "ACC_BRK_PREP": 1.})
    assert rampilot_long.is_emergency(frame | {"ACC_DECEL_REQ": 0., "COLLISION_BRK_PREP": 1.})
    assert not rampilot_long.is_emergency(frame | {"ACC_DECEL_REQ": 0., "ACC_DECEL": -10.})

  def test_only_camera_bus(self):
    h = Harness()
    frame = h.factory(CRUISING)
    h.CI.update([(h.t + DT_NS, [CanData(DAS_3.address, frame, 0)])], now_nanos=h.t + DT_NS)
    assert h.CI.CS.das_3 is None

  @pytest.mark.parametrize("candidate", [CAR.RAM_HD_5TH_GEN, CAR.CHRYSLER_PACIFICA_2020])
  def test_other_platforms_do_not_parse(self, candidate):
    CP, CP_SP = get_params(candidate, True)
    CI = CarInterface(CP, CP_SP)
    for _ in range(3):
      CI.update([(0, [])])
    assert CI.CS.das_3 is None


class TestInterface:
  def test_long_off_without_alpha(self):
    CP, _ = get_params(RAM_DT, False)
    assert CP.alphaLongitudinalAvailable
    assert not CP.openpilotLongitudinalControl
    assert CP.safetyConfigs[0].safetyParam == ChryslerSafetyFlags.RAM_DT
    assert CP.radarUnavailable
    assert CP.pcmCruise

  def test_long_on_with_alpha(self):
    CP, _ = get_params(RAM_DT, True)
    assert CP.alphaLongitudinalAvailable
    assert CP.openpilotLongitudinalControl
    assert CP.safetyConfigs[0].safetyParam == ChryslerSafetyFlags.RAM_DT | ChryslerSafetyFlags.RAMPILOT_LONG == 5
    assert CP.radarUnavailable
    assert CP.pcmCruise  # factory cruise keeps engagement
    assert CP.longitudinalActuatorDelay == pytest.approx(0.25)
    assert CP.vEgoStopping == pytest.approx(0.05)
    assert CP.vEgoStarting == pytest.approx(0.1)
    assert CP.stopAccel == pytest.approx(-2.0)
    assert CP.stoppingDecelRate == pytest.approx(2.0)
    assert CP.startingState
    assert CP.startAccel == pytest.approx(0.5)

  def test_alpha_changes_only_long_fields(self):
    off, off_sp = get_params(RAM_DT, False)
    on, on_sp = get_params(RAM_DT, True)
    a, b = off.to_dict(), on.to_dict()
    changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    # stopAccel is also set, but the prior-art -2.0 equals the default
    assert changed == {"openpilotLongitudinalControl", "safetyConfigs", "longitudinalActuatorDelay", "vEgoStopping",
                       "vEgoStarting", "stoppingDecelRate", "startingState", "startAccel"}
    assert off_sp == on_sp

  @pytest.mark.parametrize("candidate", sorted(set(CAR) - {RAM_DT}))
  def test_other_chrysler_platforms_unchanged(self, candidate):
    for alpha_long in (False, True):
      CP, _ = get_params(candidate, alpha_long)
      assert not CP.alphaLongitudinalAvailable
      assert not CP.openpilotLongitudinalControl
      assert not CP.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.RAMPILOT_LONG


class TestSafetyParam:
  def test_bits(self):
    assert ChryslerSafetyFlags.RAMPILOT_LONG == 4
    assert ChryslerSafetyFlags.RAM_DT == 1
    assert ChryslerSafetyFlags.RAM_HD == 2
    flags = [f.value for f in ChryslerSafetyFlags]
    assert all(bin(f).count("1") == 1 for f in flags)
    assert len(set(flags)) == len(flags)

  def test_das_3_address_matches_dbc(self):
    from opendbc.car.chrysler.carstate import RAM_DT_DAS_3_ADDR
    assert DAS_3.address == RAM_DT_DAS_3_ADDR == 0x99


def run(h: Harness, steps, values=CRUISING) -> list[bool]:
  """One factory frame per (long_active, accel) step; True where the comma changed the frame."""
  added = []
  for long_active, accel in steps:
    factory = h.factory(values)
    out, _ = h.send_one(factory, long_active, accel)
    added.append(out != factory)
  return added


class TestHysteresis:
  def test_start_at_minus_0_25(self, harness):
    assert run(harness, [(True, -0.2), (True, -0.24)]) == [False, False]
    factory = harness.factory(CRUISING)
    out, applied = harness.send_one(factory, True, -0.25)
    assert decode(out)["ACC_DECEL"] == raw_decel(-0.25)
    assert decode(out)["ACC_DECEL_REQ"] == 1
    assert applied == -0.25

  def test_nothing_added_to_a_factory_braking_frame(self, harness):
    # the Panda forwards a factory frame that requests braking: the comma's copy is unchanged however hard it asks
    assert run(harness, [(True, -1.6), (True, -1.8), (True, -6.0)], BRAKING) == [False, False, False]

  def test_keeps_braking_down_to_minus_0_05(self, harness):
    assert run(harness, [(True, -0.5), (True, -0.2), (True, -0.1), (True, -0.06)]) == [True] * 4
    factory = harness.factory(CRUISING)
    out, _ = harness.send_one(factory, True, -0.1)
    assert decode(out)["ACC_DECEL"] == raw_decel(-0.1)
    assert run(harness, [(True, -0.04)]) == [False]
    # once stopped, the start threshold applies again
    assert run(harness, [(True, -0.2), (True, -0.1)]) == [False, False]

  def test_factory_braking_frames_pause_and_keep_the_braking(self, harness):
    assert run(harness, [(True, -1.0)]) == [True]
    assert run(harness, [(True, -1.0), (True, -1.6)], BRAKING) == [False, False]  # the factory brakes: forwarded
    assert run(harness, [(True, -1.0)]) == [True]  # the comma's braking is back on the next frame
    assert run(harness, [(True, -0.04)]) == [False]  # and ends by the keep rule as usual

  def test_stops_when_long_is_left_or_factory_cruise_is_off(self, harness):
    assert run(harness, [(True, -1.0), (False, -1.0), (True, -0.2)]) == [True, False, False]
    assert run(harness, [(True, -1.0)]) == [True]
    assert run(harness, [(True, -1.0)], ACC_OFF) == [False]
    assert run(harness, [(True, -0.2)]) == [False]

  def test_no_flicker_on_noisy_accel_around_minus_0_1(self, harness):
    rng = random.Random(56)
    noise = [-0.1 + rng.uniform(-0.04, 0.04) for _ in range(200)]
    assert not any(run(harness, [(True, a) for a in noise]))  # never starts
    assert all(run(harness, [(True, -0.4)] + [(True, a) for a in noise]))  # once started, never drops out


class TestHold:
  def stop_with_comma_braking(self, h, accel=-1.5):
    h.speed_kph = 20.
    assert run(h, [(True, accel)] * 3) == [True] * 3
    h.speed_kph = 0.
    factory = h.factory(CRUISING)
    out, _ = h.send_one(factory, True, accel)
    assert decode(out)["ACC_DECEL"] == raw_decel(rampilot_long.HOLD_ACCEL)

  def assert_holding(self, h, values=CRUISING, accel=0.5, decel=rampilot_long.HOLD_ACCEL):
    factory = h.factory(values)
    out, applied = h.send_one(factory, True, accel)
    got = decode(out)
    assert got["ACC_DECEL_REQ"] == 1
    assert got["ACC_DECEL"] == raw_decel(decel)
    assert got["ENGINE_TORQUE_REQUEST"] <= raw_torque(0.)
    assert got["ENGINE_TORQUE_REQUEST_MAX"] == 0
    assert_copied(out, factory)
    assert applied == pytest.approx(decel, abs=DAS_3.sigs["ACC_DECEL"].factor)

  def assert_passing_through(self, h, values=CRUISING, accel=0.5):
    factory = h.factory(values)
    out, applied = h.send_one(factory, True, accel)
    assert out == factory
    assert applied == 0.

  def test_hold_after_a_comma_stop_while_the_planner_asks_to_go(self, harness):
    self.stop_with_comma_braking(harness)
    for _ in range(100):  # 2 s of the planner asking for +0.5
      self.assert_holding(harness)
    self.assert_holding(harness, accel=2.0)
    self.assert_holding(harness, accel=-0.1)

  def test_hold_survives_factory_braking(self, harness):
    self.stop_with_comma_braking(harness)
    for values in ({**BRAKING, "ACC_DECEL": -2.5}, {**BRAKING, "ACC_DECEL": -1.0}, {**CRUISING, "ACC_BRK_PREP": 1},
                   {**CRUISING, "COLLISION_BRK_PREP": 1}):
      for _ in range(5):
        self.assert_passing_through(harness, values)  # the factory brakes the stopped truck: forwarded to it
    for _ in range(20):
      self.assert_holding(harness)  # the factory lets go at the standstill: the comma's hold is back

  def test_confirm_during_factory_braking_releases_the_hold(self, harness):
    self.stop_with_comma_braking(harness)
    self.assert_passing_through(harness, BRAKING)
    harness.resume_button = True
    self.assert_passing_through(harness, BRAKING)  # a fresh press, used up by this frame's decision
    harness.resume_button = False
    for _ in range(10):
      self.assert_passing_through(harness)  # released: no hold when the factory lets go

  def test_stop_during_factory_braking_starts_no_hold(self, harness):
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 3) == [True] * 3  # the comma adds braking
    assert run(harness, [(True, -1.5)] * 2, BRAKING) == [False] * 2  # then the factory brakes too
    harness.speed_kph = 0.
    assert run(harness, [(True, -1.5)] * 2, BRAKING) == [False] * 2  # the truck stops on the factory's braking
    # the factory lets go at the standstill: its own stop, so no hold; the comma brakes only while its planner asks
    factory = harness.factory(CRUISING)
    out, _ = harness.send_one(factory, True, -1.5)
    assert decode(out)["ACC_DECEL"] == raw_decel(-1.5)
    for _ in range(10):
      self.assert_passing_through(harness)

  def test_hold_below_0_3_m_s_without_standstill(self, harness):
    harness.speed_kph = 20.
    run(harness, [(True, -1.0)] * 2)
    harness.speed_kph = 0.25 * 3.6  # 0.25 m/s
    run(harness, [(True, -1.0)])
    assert not harness.CI.CS.out.standstill
    self.assert_holding(harness)

  def test_released_on_gas(self, harness):
    self.stop_with_comma_braking(harness)
    harness.gas = True
    self.assert_passing_through(harness)
    harness.gas = False
    self.assert_passing_through(harness)

  @pytest.mark.parametrize("button", ["resume_button", "accel_button"])
  def test_released_on_resume_or_accel_button(self, harness, button):
    self.stop_with_comma_braking(harness)
    self.assert_holding(harness)
    setattr(harness, button, True)
    self.assert_passing_through(harness)
    setattr(harness, button, False)
    for _ in range(10):
      self.assert_passing_through(harness)

  @pytest.mark.parametrize("button", ["resume_button", "accel_button"])
  def test_button_held_across_the_stop_then_released_keeps_the_hold(self, harness, button):
    harness.speed_kph = 20.
    setattr(harness, button, True)  # pressed while still braking toward the stop, and held
    assert run(harness, [(True, -1.5)] * 3) == [True] * 3
    harness.speed_kph = 0.
    self.assert_holding(harness, accel=-1.5)  # the comma's stop: hold starts
    self.assert_holding(harness)
    setattr(harness, button, False)  # the release of a button pressed before the hold is not a confirm
    for _ in range(50):
      self.assert_holding(harness)
    setattr(harness, button, True)  # a fresh press after the hold started releases it
    self.assert_passing_through(harness)

  def test_stale_confirm_from_before_the_stop_keeps_the_hold(self, harness):
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 3) == [True] * 3
    harness.speed_kph, harness.resume_button = 0., True
    _, out = harness.step([], True, -1.5)  # pressed as the truck stops, in a frame with no factory DAS_3
    assert out == []
    self.assert_holding(harness, accel=-1.5)  # the decision that starts the hold drops the pending press
    harness.resume_button = False
    for _ in range(50):
      self.assert_holding(harness)

  def test_stale_confirm_after_a_data_gap_keeps_the_hold(self, harness):
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 3) == [True] * 3
    harness.resume_button = True
    harness.step([], True, -1.5)  # pressed while braking
    harness.resume_button = False
    for _ in range(5):
      harness.step([], True, -1.5)  # 50 ms without factory frames, still moving
    harness.speed_kph = 0.
    self.assert_holding(harness, accel=-1.5)
    for _ in range(20):
      self.assert_holding(harness)

  def test_gas_held_through_the_stop_then_released(self, harness):
    # With the gas pressed the comma adds no braking (the Panda refuses it), so the truck did not stop on the comma's
    # braking and no hold starts. Releasing the gas at the stop is not a confirm and starts nothing either.
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 2) == [True] * 2
    harness.gas = True
    assert run(harness, [(True, -1.5)] * 2) == [False] * 2
    harness.speed_kph = 0.
    assert run(harness, [(True, -1.5)] * 2) == [False] * 2
    harness.gas = False
    assert run(harness, [(True, 0.5)] * 20) == [False] * 20

  def test_gas_tap_before_the_stop_does_not_release_a_later_hold(self, harness):
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 2) == [True] * 2
    harness.gas = True
    run(harness, [(True, -1.5)])
    harness.gas = False
    assert run(harness, [(True, -1.5)] * 3) == [True] * 3
    harness.speed_kph = 0.
    self.assert_holding(harness, accel=-1.5)
    for _ in range(50):
      self.assert_holding(harness)
    harness.gas = True  # a new gas press after the hold started
    self.assert_passing_through(harness)

  def test_button_press_between_factory_frames_releases(self, harness):
    self.stop_with_comma_braking(harness)
    harness.resume_button = True
    _, out = harness.step([], True, 0.5)  # press in a 10 ms frame without a factory DAS_3
    assert out == []
    self.assert_passing_through(harness)  # still held down: no new button event in this frame
    harness.resume_button = False
    self.assert_passing_through(harness)

  def test_after_a_confirm_normal_rules_apply(self, harness):
    self.stop_with_comma_braking(harness)
    harness.resume_button = True
    self.assert_passing_through(harness, accel=-0.2)  # not past the start threshold
    harness.resume_button = False
    assert run(harness, [(True, -1.0), (True, 0.5), (True, -0.2)]) == [True, False, False]  # no new hold while stopped

  def test_cleared_on_disengage(self, harness):
    self.stop_with_comma_braking(harness)
    factory = harness.factory(CRUISING)
    out, _ = harness.send_one(factory, False, -1.0)  # brake, cancel or gas override
    assert out == factory
    self.assert_passing_through(harness)  # engaged again while stopped: no hold

  def test_cleared_when_factory_cruise_turns_off(self, harness):
    self.stop_with_comma_braking(harness)
    factory = harness.factory(ACC_OFF)
    out, _ = harness.send_one(factory, True, 0.5)
    assert out == factory
    self.assert_passing_through(harness)

  def test_no_hold_after_a_factory_stop(self, harness):
    # the factory brakes to a stop; the comma asks for less, or not past the margin, and never adds braking
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.0), (True, -1.6)], BRAKING) == [False, False]
    harness.speed_kph = 0.
    assert run(harness, [(True, -1.6)], BRAKING) == [False]
    self.assert_passing_through(harness)  # the factory's own stop-and-go stays the factory's

  def test_no_hold_when_comma_braking_ended_before_the_stop(self, harness):
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.0), (True, -0.04)]) == [True, False]
    harness.speed_kph = 0.
    assert run(harness, [(True, -0.04)]) == [False]
    self.assert_passing_through(harness)

  def test_no_hold_when_engaged_at_a_standstill(self, harness):
    harness.speed_kph = 0.
    run(harness, [(False, 0.)] * 3)
    assert run(harness, [(True, -2.0)]) == [True]  # ordinary added braking
    self.assert_passing_through(harness)


class TestGas:
  """The Panda refuses added braking while the gas is pressed: those frames pass through and reset the state."""

  def test_pass_through_while_the_planner_asks_for_braking(self, harness):
    harness.gas = True
    for values in (CRUISING, BRAKING):
      for accel in (-3.5, -2.0, -0.5):
        factory = harness.factory(values)
        out, applied = harness.send_one(factory, True, accel)
        assert out == factory
        assert applied == 0.

  def test_gas_ends_braking_and_start_threshold_applies_after(self, harness):
    assert run(harness, [(True, -0.5), (True, -0.1)]) == [True, True]
    harness.gas = True
    assert run(harness, [(True, -0.1)]) == [False]
    harness.gas = False
    assert run(harness, [(True, -0.1), (True, -0.3)]) == [False, True]

  def test_no_hold_when_stopping_with_the_gas_pressed(self, harness):
    harness.speed_kph = 20.
    assert run(harness, [(True, -1.5)] * 2) == [True, True]
    harness.speed_kph, harness.gas = 0., True
    assert run(harness, [(True, -1.5)]) == [False]
    harness.gas = False
    assert run(harness, [(True, 0.5)] * 5) == [False] * 5

  def test_gas_in_a_batch_applies_to_both_copies(self, harness):
    harness.gas = True
    frames = [harness.factory(CRUISING), harness.factory(CRUISING)]
    _, out = harness.step(frames, True, -2.0)
    assert out == frames


class TestDecisionUnit:
  """das_3_overrides called directly."""
  FACTORY = {name: 0. for name in DAS_3.sigs} | {"ACC_ACTIVE": 1., "ENGINE_TORQUE_REQUEST": 100.,
                                                 "ENGINE_TORQUE_REQUEST_MAX": 1., "ACC_DECEL": 4.}

  def test_confirm_at_the_stop_moment_is_stale_and_dropped(self):
    moving = rampilot_long.LongState(braking=True, stopped=False)
    overrides, state = rampilot_long.das_3_overrides(self.FACTORY, True, 0.5, moving,
                                                     rampilot_long.StandstillInputs(stopped=True, confirm=True))
    assert state.hold and overrides["ACC_DECEL"] == rampilot_long.HOLD_ACCEL
    _, state = rampilot_long.das_3_overrides(self.FACTORY, True, 0.5, state, rampilot_long.StandstillInputs(stopped=True))
    assert state.hold  # nothing pending carries over
    _, state = rampilot_long.das_3_overrides(self.FACTORY, True, 0.5, state,
                                             rampilot_long.StandstillInputs(stopped=True, confirm=True))
    assert not state.hold  # a confirm after the hold started releases it

  def test_hold_entered_on_the_stop_edge_only(self):
    moving = rampilot_long.LongState(braking=True, stopped=False)
    overrides, state = rampilot_long.das_3_overrides(self.FACTORY, True, 0.5, moving,
                                                     rampilot_long.StandstillInputs(stopped=True))
    assert state.hold
    assert overrides["ACC_DECEL"] == rampilot_long.HOLD_ACCEL
    already_stopped = rampilot_long.LongState(braking=True, stopped=True)
    _, state = rampilot_long.das_3_overrides(self.FACTORY, True, 0.5, already_stopped,
                                             rampilot_long.StandstillInputs(stopped=True))
    assert not state.hold

  def test_reset(self):
    held = rampilot_long.LongState(braking=True, hold=True, stopped=True)
    plain, gas = rampilot_long.StandstillInputs(), rampilot_long.StandstillInputs(gas_pressed=True)
    for long_active, das_3, inputs in ((False, self.FACTORY, plain), (True, self.FACTORY | {"ACC_ACTIVE": 0.}, plain),
                                       (True, self.FACTORY, gas)):
      overrides, state = rampilot_long.das_3_overrides(das_3, long_active, -1.0, held, inputs)
      assert overrides == {}
      assert state == rampilot_long.LongState()

  def test_gas_never_adds_braking(self):
    gas = rampilot_long.StandstillInputs(gas_pressed=True)
    for state in (rampilot_long.LongState(), rampilot_long.LongState(braking=True, stopped=False)):
      for accel in (-3.5, -1.0, -0.3):
        overrides, new_state = rampilot_long.das_3_overrides(self.FACTORY, True, accel, state, gas)
        assert overrides == {}
        assert not new_state.hold
    moving = rampilot_long.LongState(braking=True, stopped=False)
    _, state = rampilot_long.das_3_overrides(self.FACTORY, True, -1.0, moving,
                                             rampilot_long.StandstillInputs(stopped=True, gas_pressed=True))
    assert not state.hold

  def test_nan_never_starts_and_stops(self):
    for braking in (False, True):
      overrides, state = rampilot_long.das_3_overrides(self.FACTORY, True, float("nan"),
                                                       rampilot_long.LongState(braking=braking),
                                                       rampilot_long.StandstillInputs())
      assert overrides == {}
      assert not state.braking


class TestCounterOrder:
  def send_counters(self, h, counters, gap_after=None, gap_steps=0):
    sent = []
    for i, c in enumerate(counters):
      factory = h.factory(CRUISING if i % 2 else BRAKING, counter=c)
      _, out = h.step([factory], True, -2.0 if i % 3 else 0.5)
      sent += [decode(o)["COUNTER"] for o in out]
      if i == gap_after:
        for _ in range(gap_steps):
          _, out = h.step([], True, 0.)
          assert out == []
    return sent

  def test_never_repeats_or_goes_back(self, harness):
    counters = [0, 1, 2, 3, 3, 4, 5, 6, 7, 8, 6, 9, 10, 11, 12, 13, 14, 15, 0, 1, 1, 0, 2]
    sent = self.send_counters(harness, counters)
    assert sent == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2]
    for a, b in zip(sent, sent[1:], strict=False):
      assert 1 <= (b - a) % 16 <= rampilot_long.COUNTER_FORWARD_MAX

  def test_skipped_counters_still_move_forward(self, harness):
    assert self.send_counters(harness, [0, 2, 3, 6, 7]) == [0, 2, 3, 6, 7]

  def test_new_sequence_after_a_gap(self, harness):
    # 150 ms without factory frames, then the counter looks older: a new sequence starts
    assert self.send_counters(harness, [0, 1, 2, 3, 4, 2, 3], gap_after=4, gap_steps=15) == [0, 1, 2, 3, 4, 2, 3]

  def test_random_sequences(self):
    rng = random.Random(3)
    h = Harness()
    counter, last, last_step, checked = 0, None, 0, 0
    for step in range(2000):
      counter = (counter + rng.choice([1] * 12 + [2, 0, -1, -3])) % 16
      _, out = h.step([h.factory(CRUISING, counter=counter)], True, rng.uniform(-3, 1))
      for o in out:
        c = decode(o)["COUNTER"]
        if last is not None and step - last_step <= rampilot_long.COUNTER_RESYNC_FRAMES:
          assert 1 <= (c - last) % 16 <= rampilot_long.COUNTER_FORWARD_MAX
          checked += 1
        last, last_step = c, step
    assert checked > 1000


class TestButtons:
  def events(self, h):
    return [(be.type, be.pressed) for be in h.CI.CS.out.buttonEvents]

  def test_resume_and_accel_events_with_long(self):
    h = Harness()
    h.resume_button = True
    h.step([])
    assert self.events(h) == [(ButtonType.resumeCruise, True)]
    h.resume_button, h.accel_button = False, True
    h.step([])
    assert self.events(h) == [(ButtonType.resumeCruise, False), (ButtonType.accelCruise, True)]

  def test_no_new_events_without_long(self):
    h = Harness(alpha_long=False)
    for resume, accel in ((True, False), (False, True), (False, False)):
      h.resume_button, h.accel_button = resume, accel
      h.step([])
      assert self.events(h) == []

  def test_hold_accel_is_the_stop_accel(self):
    CP, _ = get_params(RAM_DT, True)
    assert CP.stopAccel == rampilot_long.HOLD_ACCEL

  def test_cancel_goes_to_the_factory_cruise_module(self):
    # what openpilot does to factory cruise after it disengages (controlsd sets cruiseControl.cancel)
    h = Harness()
    for _ in range(10):
      h.step([])
    CC = structs.CarControl()
    CC.cruiseControl.cancel = True
    _, sends = h.CI.apply(CC.as_reader(), structs.CarControlSP(), h.t)
    buttons = [s for s in sends if s[0] == DBC(DBC_NAME).name_to_msg["CRUISE_BUTTONS"].address]
    assert len(buttons) == 1 and buttons[0][2] == 2 and buttons[0][1][0] & 0x01  # ACC_Cancel to the DASM (bus 2)


class TestDelivery:
  """What reached the truck: bus-0 DAS_3 echoes (src 128) compared with the comma's copies and the factory frames."""

  def drive(self, h, steps, accel=-2.0, echo="copy", same_step=False, values=CRUISING):
    """`echo`: what the Panda transmits for each factory frame: the comma's copy, the factory frame, or nothing.
    The echo arrives with the next step, or with the factory frame itself when `same_step` (forwarded at once)."""
    counts, pending = [], []
    for _ in range(steps):
      factory = h.factory(values)
      now_echo = [factory] if echo == "factory" and same_step else []
      _, out = h.step([factory], True, accel, echoes=pending + now_echo)
      pending = {"copy": out, "factory": [] if same_step else [factory], None: []}[echo]
      counts.append(h.CI.CS.out_sp.rampilotBrakingNotDelivered)
    return counts

  def test_delivered_copies_count_nothing(self):
    h = Harness()
    assert self.drive(h, 50) == [0] * 50
    assert h.CI.CS.out_sp.rampilotDas3EchoAgeMs == 0.

  def test_factory_frame_instead_of_added_braking_is_counted(self):
    h = Harness()
    counts = self.drive(h, 6, echo="factory")
    assert counts == [0, 1, 2, 3, 4, 5]  # each echo judged when it arrives with the next step

  def test_forwarded_before_the_copy_is_counted(self):
    h = Harness()
    counts = self.drive(h, 6, echo="factory", same_step=True)
    assert counts == [0, 1, 2, 3, 4, 5]  # judged when the copy is sent; shows in the next update

  def test_pass_through_copies_never_count(self):
    h = Harness()
    assert self.drive(h, 30, accel=0.5, echo="factory") == [0] * 30
    assert self.drive(h, 30, accel=0.5, echo="factory", same_step=True) == [0] * 30

  def test_count_covers_the_last_0_5_s(self):
    h = Harness()
    assert self.drive(h, 3, echo="factory")[-1] == 2
    counts = self.drive(h, 60)  # delivered from here on: the count drains as the 0.5 s window passes
    assert counts[0] == 2 and counts[-1] == 0
    assert counts.index(0) <= 51

  def test_echo_age(self):
    h = Harness()
    self.drive(h, 5)
    assert h.CI.CS.out_sp.rampilotDas3EchoAgeMs == 0.
    for i in range(1, 13):
      h.step([h.factory(CRUISING)], True, -2.0)  # nothing reaches the truck
      assert h.CI.CS.out_sp.rampilotDas3EchoAgeMs == pytest.approx(i * 10.)
    self.drive(h, 2)
    assert h.CI.CS.out_sp.rampilotDas3EchoAgeMs == 0.

  def test_forwarded_factory_braking_frames_never_count(self):
    # The comma brakes before, during and after a stretch of factory braking frames. The Panda forwards those at once
    # and refuses their copies (returned as src 192); nothing counts and the echo rules see a steady stream.
    h = Harness()
    counts = self.drive(h, 5)
    rejected = []
    for i in range(50):
      factory = h.factory(BRAKING if i % 2 else {**CRUISING, "ACC_BRK_PREP": 1})
      _, out = h.step([factory], True, -2.0, echoes=[factory], rejected=rejected)
      assert out == [factory]  # an unchanged copy
      rejected = out
      sp = h.CI.CS.out_sp
      counts.append(sp.rampilotBrakingNotDelivered)
      assert (sp.rampilotDas3EchoAgeMs, sp.rampilotFactorySinceEcho) == (0., 0)
    counts += self.drive(h, 30)  # the factory lets go: the comma's braking copies reach the truck again
    assert counts == [0] * len(counts)

  def test_added_braking_on_a_factory_braking_frame_never_counts(self):
    # rule (a) on its own: even a copy that added braking to a factory braking/prep frame (the comma does not send
    # one) is not a misdelivery when the truck gets that factory frame
    p = CANPacker(DBC_NAME)
    added = p.make_can_msg("DAS_3", 0, {**CRUISING, "COUNTER": 3, "ACC_DECEL_REQ": 1, "ACC_DECEL": -3.0,
                                        "ENGINE_TORQUE_REQUEST": 0., "ENGINE_TORQUE_REQUEST_MAX": 0})[1]
    for fields, counted in (({}, True), ({"ACC_DECEL_REQ": 1, "ACC_DECEL": -1.0}, False), ({"ACC_BRK_PREP": 1}, False),
                            ({"COLLISION_BRK_PREP": 1}, False)):
      d = rampilot_long.Das3Delivery()
      d.tick(0)
      factory = p.make_can_msg("DAS_3", 2, {**CRUISING, **fields, "COUNTER": 3})[1]
      d.observe(1, factory, 2)
      d.copy_sent(added, added_braking=True)
      d.observe(2, factory, ECHO)
      assert d.not_delivered_recent() == int(counted), fields

  def test_factory_frames_since_the_last_echo(self):
    h = Harness()
    self.drive(h, 3)
    assert h.CI.CS.out_sp.rampilotFactorySinceEcho == 0
    for i in range(1, 6):
      h.step([h.factory(CRUISING)], True, 0.5)  # the host receives factory frames, no echo
      assert h.CI.CS.out_sp.rampilotFactorySinceEcho == i
    for _ in range(10):
      h.step([], True, 0.5)  # no factory frames either: the count does not grow
    assert h.CI.CS.out_sp.rampilotFactorySinceEcho == 5
    factory = h.factory(CRUISING)
    h.step([factory], True, 0.5, echoes=[factory])
    assert h.CI.CS.out_sp.rampilotFactorySinceEcho == 0

  def test_host_pause_then_catch_up_batch(self):
    h = Harness()
    self.drive(h, 10, accel=0.5, echo="factory", same_step=True)
    batch = []
    for _ in range(8):  # 160 ms of frames held back by a host receive pause, then delivered in one batch
      factory = h.factory(CRUISING)
      batch += [CanData(DAS_3.address, factory, 2), CanData(DAS_3.address, factory, ECHO)]
    h.t += 16 * DT_NS
    h.CI.update([(h.t, h.pt_frames() + batch)], now_nanos=h.t)
    sp = h.CI.CS.out_sp
    assert (sp.rampilotDas3EchoAgeMs, sp.rampilotFactorySinceEcho) == (0., 0)

  def test_echo_age_counts_from_start_without_echoes(self):
    h = Harness()
    for _ in range(15):
      h.step([h.factory(CRUISING)], True, 0.5)
    assert h.CI.CS.out_sp.rampilotDas3EchoAgeMs == pytest.approx(150.)

  def test_refused_copies_and_other_buses_are_not_echoes(self):
    h = Harness()
    self.drive(h, 3)
    for _ in range(12):
      factory = h.factory(CRUISING)
      h.CI.update([(h.t + DT_NS, [CanData(DAS_3.address, factory, 2), CanData(DAS_3.address, factory, 192),
                                  CanData(DAS_3.address, factory, 0)])], now_nanos=h.t + DT_NS)
      h.t += DT_NS
    assert h.CI.CS.out_sp.rampilotDas3EchoAgeMs == pytest.approx(120.)

  def test_unit_orders(self):
    d = rampilot_long.Das3Delivery()
    p = Harness().packer
    factory = p.make_can_msg("DAS_3", 2, {**CRUISING, "COUNTER": 5})[1]
    copy = p.make_can_msg("DAS_3", 0, {**CRUISING, "COUNTER": 5, "ACC_DECEL_REQ": 1, "ACC_DECEL": -2.0,
                                       "ENGINE_TORQUE_REQUEST": 0., "ENGINE_TORQUE_REQUEST_MAX": 0})[1]
    d.tick(0)
    for order in ("echo first", "copy first"):
      d.observe(1, factory, 2)
      if order == "echo first":
        d.observe(2, factory, ECHO)
        d.copy_sent(copy, added_braking=True)
      else:
        d.copy_sent(copy, added_braking=True)
        d.observe(2, factory, ECHO)
    d.tick(3)
    assert d.not_delivered_recent() == 2
    d.observe(4, factory, 2)
    d.copy_sent(copy, added_braking=True)
    d.observe(5, copy, ECHO)  # the copy reached the truck
    d.observe(6, factory, ECHO)  # a later duplicate for a judged counter is not counted again
    assert d.not_delivered_recent() == 2
    # a copy without added braking that still differs from the factory bytes (packed without the raw frame) never counts
    d.observe(7, factory, 2)
    d.copy_sent(copy, added_braking=False)
    d.observe(8, factory, ECHO)
    assert d.not_delivered_recent() == 2
    d.tick(5 + rampilot_long.NOT_DELIVERED_WINDOW_NS + 1)
    assert d.not_delivered_recent() == 0
    assert d.echo_age_ms() == pytest.approx(rampilot_long.NOT_DELIVERED_WINDOW_NS / 1e6, abs=1e-3)
