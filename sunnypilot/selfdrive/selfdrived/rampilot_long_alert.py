"""RamPilot long: tell the driver when the comma's braking is not reaching the truck.

The measure is what reached the truck. The Panda returns every DAS_3 it transmits on bus 0 to the host (src 128): the
factory frame it forwarded, or the comma copy it accepted. CarState compares those echoes with the factory frames and the
comma's copies and publishes, in carStateSP:
- rampilotBrakingNotDelivered: frames in the last 0.5 s where the comma sent added braking but the truck got the
  factory frame instead (a factory frame that requests braking or brake prep is forwarded by design and never counts);
- rampilotDas3EchoAgeMs: ms since the last bus-0 DAS_3 echo;
- rampilotFactorySinceEcho: factory DAS_3 frames received on bus 2 since that echo.

While long control is active, the warning is raised when (a) NOT_DELIVERED_MIN or more such frames came in the last
0.5 s, outside the factory emergency window, or (b) no bus-0 DAS_3 echo came for ECHO_AGE_MAX_MS or more while the host
kept receiving factory frames (ECHO_FACTORY_FRAMES_MIN or more since the last echo). Echo times are host receive times:
a host receive pause (up to 154 ms in the recorded drives) delays both streams and does not raise (b). The warning
stays until openpilot disengages.

Not counted:
- outside a clean engagement (not engaged, long control not active or overridden, factory cruise off, brake or gas
  pressed), and in its first SETTLE: the Panda passes factory frames then by design;
- for rule (a), EMERGENCY_HOLDOFF after a factory emergency frame (carStateSP.rampilotFactoryEmergency): the Panda passes
  factory frames for 2 s by design.
"""
from cereal import car
from openpilot.common.realtime import DT_CTRL
from opendbc.car.chrysler.values import ChryslerSafetyFlags

NOT_DELIVERED_MIN = 3  # frames of added braking replaced by the factory frame, in the last 0.5 s
ECHO_AGE_MAX_MS = 100.  # no DAS_3 reached the truck for this long...
ECHO_FACTORY_FRAMES_MIN = 3  # ...while this many factory frames arrived since the last echo (not a host pause)
SETTLE_FRAMES = round(0.2 / DT_CTRL)  # clean engagement before the monitor counts
EMERGENCY_HOLDOFF_FRAMES = round(2.5 / DT_CTRL)  # the Panda's 2 s factory window, plus latency


def rampilot_long_enabled(CP: car.CarParams) -> bool:
  """openpilot long on the Ram 1500 DT with the Panda's RamPilot long safety mode."""
  return (CP.carFingerprint == "RAM_1500_5TH_GEN" and CP.openpilotLongitudinalControl and len(CP.safetyConfigs) > 0 and
          bool(CP.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.RAMPILOT_LONG))


class RamPilotLongMonitor:
  def __init__(self):
    self.unavailable = False  # the comma's braking is not reaching the truck: factory cruise only until disengaged
    self.reason = ""  # "not delivered" or "no echo", for logs and tests
    self.clean_since: int | None = None
    self.emergency_frame: int | None = None

  def update(self, frame: int, enabled: bool, long_active: bool, clean: bool, not_delivered: int, echo_age_ms: float,
             factory_since_echo: int, emergency: bool) -> bool:
    """Returns whether the warning is raised."""
    if not enabled:
      self.unavailable, self.reason = False, ""  # the Panda clears its fallback when controls are no longer allowed
    if emergency:
      self.emergency_frame = frame
    in_emergency = self.emergency_frame is not None and frame - self.emergency_frame <= EMERGENCY_HOLDOFF_FRAMES

    if not (enabled and long_active and clean):
      self.clean_since = None
      return self.unavailable
    if self.clean_since is None:
      self.clean_since = frame
    if frame - self.clean_since < SETTLE_FRAMES or self.unavailable:
      return self.unavailable

    if not in_emergency and not_delivered >= NOT_DELIVERED_MIN:
      self.unavailable, self.reason = True, "not delivered"
    elif echo_age_ms >= ECHO_AGE_MAX_MS and factory_since_echo >= ECHO_FACTORY_FRAMES_MIN:
      self.unavailable, self.reason = True, "no echo"
    return self.unavailable
