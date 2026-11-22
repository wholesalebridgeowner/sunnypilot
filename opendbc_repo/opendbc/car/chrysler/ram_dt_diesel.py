"""Optional display data from the recorded US Ram 1500 EcoDiesel configuration."""
from opendbc.car.chrysler.chryslercan import chrysler_checksum
from opendbc.car.chrysler.values import RAM_DT


# Each reading requires its own independent vehicle reference before publication.
REGEN_VALIDATED = False
DEF_VALIDATED = False
FUEL_LEVEL_VALIDATED = False
AVERAGE_MPG_VALIDATED = False
FUEL_FLOW_VALIDATED = False
INSTANT_MPG_VALIDATED = False
IDLE_RATE_VALIDATED = False
RANGE_VALIDATED = False

RPM, SPEED, EXHAUST, FLOW, LEVEL, RANGE, AVERAGE = 0x25, 0x79, 0x12D, 0x13D, 0x27A, 0x27B, 0x3AA
# The dependencies arrive at ~100 Hz, exhaust and flow at ~10 Hz, and the cluster values at ~1 Hz.
MAX_AGE_NS = {RPM: 250_000_000, SPEED: 250_000_000, EXHAUST: 500_000_000, FLOW: 500_000_000,
              LEVEL: 2_000_000_000, RANGE: 2_000_000_000, AVERAGE: 2_000_000_000}
RANGE_WARMUP_NS = 5_000_000_000  # Initial cluster range jumped through second four in the recordings.
LITERS_PER_US_GALLON = 3.785411784
KM_PER_MILE = 1.609344


def supports_diesel(CP) -> bool:
  # This exact engine calibration was recorded on the owner's confirmed EcoDiesel.
  # RAM_DT alone also includes gasoline trucks.
  if CP.carFingerprint not in RAM_DT:
    return False
  engine = {bytes(fw.fwVersion) for fw in CP.carFw
            if fw.ecu == "engine" and fw.address == 0x7e0 and fw.subAddress == 0 and fw.brand == "chrysler"}
  return engine == {b"68500630AF"}


class DieselReadouts:
  def __init__(self):
    # With the switches off the panel can display Unknown without opening a stream
    # or decoding, retaining, or rechecking high-rate powertrain frames.
    selected = {EXHAUST: REGEN_VALIDATED or DEF_VALIDATED, LEVEL: FUEL_LEVEL_VALIDATED,
                RANGE: RANGE_VALIDATED, AVERAGE: AVERAGE_MPG_VALIDATED}
    self.addresses = {address for address, enabled in selected.items() if enabled}
    if FUEL_FLOW_VALIDATED and (INSTANT_MPG_VALIDATED or IDLE_RATE_VALIDATED):
      self.addresses.update((FLOW, SPEED))
      if IDLE_RATE_VALIDATED:
        self.addresses.add(RPM)
    self.samples: dict[int, tuple[int, bytes | None]] = {}
    self.range_since_ns: int | None = None

  def observe(self, address: int, data: bytes, stamp: int, valid: bool):
    previous = self.samples.get(address)
    if previous is not None and stamp < previous[0]:
      return
    usable = valid and len(data) == 8 and data != b"\xff" * 8
    if usable and address in (RPM, SPEED):
      usable = data[-1] == chrysler_checksum(address, None, data)
    if address == EXHAUST and data == bytes(8):
      usable = False
    if address == RANGE:
      usable = usable and ((data[0] & 3) << 8 | data[1]) != 0x3ff
      if not usable:
        self.range_since_ns = None
      elif previous is None or self.range_since_ns is None or stamp - previous[0] > MAX_AGE_NS[RANGE]:
        self.range_since_ns = stamp
    # A bad matching frame deliberately clears the prior value. Other identifiers
    # cannot refresh it. Keep the timestamp to reject a late, older replacement.
    self.samples[address] = (stamp, bytes(data) if usable else None)

  def _fresh(self, address: int, now: int):
    sample = self.samples.get(address)
    if sample is not None and 0 <= now - sample[0] <= MAX_AGE_NS[address]:
      return sample[1]
    return None

  def publish(self, ret, now: int | None):
    ret.dieselSupported = True
    if now is None or not self.addresses:
      return
    exhaust = self._fresh(EXHAUST, now)
    if exhaust is not None:
      if REGEN_VALIDATED and exhaust[3] != 0xff:
        ret.dpfRegenActive = bool(exhaust[3] & 0x80)
        ret.dpfRegenValid = True
      if DEF_VALIDATED and exhaust[5] <= 100:
        ret.defPercent = float(exhaust[5])
        ret.defPercentValid = True
    level = self._fresh(LEVEL, now)
    if FUEL_LEVEL_VALIDATED and level is not None and level[1] != 0xff:
      ret.fuelRemainingUsGal = level[1] * 0.5 / LITERS_PER_US_GALLON
      ret.fuelRemainingUsGalValid = True
    average = self._fresh(AVERAGE, now)
    if AVERAGE_MPG_VALIDATED and average is not None:
      raw = (average[0] & 1) << 8 | average[1]
      if 0 < raw < 0x1ff:
        ret.avgFuelEconomyUsMpg = raw / 10
        ret.avgFuelEconomyUsMpgValid = True
    distance = self._fresh(RANGE, now)
    if RANGE_VALIDATED and distance is not None and self.range_since_ns is not None:
      if self.samples[RANGE][0] - self.range_since_ns >= RANGE_WARMUP_NS:
        ret.rangeMiles = ((distance[0] & 3) << 8 | distance[1]) / KM_PER_MILE
        ret.rangeMilesValid = True

    flow, speed = self._fresh(FLOW, now), self._fresh(SPEED, now)
    if not FUEL_FLOW_VALIDATED or flow is None or speed is None:
      return
    rate = int.from_bytes(flow[4:6], "big")
    velocity = int.from_bytes(speed[4:6], "big")
    if not 0 < rate < 0xffff or velocity == 0xffff:
      return
    gallons_per_hour = rate / 128 / LITERS_PER_US_GALLON
    if INSTANT_MPG_VALIDATED and velocity > 0:
      ret.instantFuelEconomyUsMpg = (velocity / 128 / KM_PER_MILE) / gallons_per_hour
      ret.instantFuelEconomyUsMpgValid = True
    elif IDLE_RATE_VALIDATED and velocity == 0:
      rpm = self._fresh(RPM, now)
      if rpm is not None and 0 < int.from_bytes(rpm[:2], "big") < 0xffff:
        ret.idleFuelUsGalPerHour = gallons_per_hour
        ret.idleFuelUsGalPerHourValid = True
