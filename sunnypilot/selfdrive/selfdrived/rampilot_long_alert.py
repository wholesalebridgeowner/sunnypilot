"""RamPilot long: tell the driver when the Panda has handed the truck back to factory cruise.

The Panda refuses a comma DAS_3 it cannot accept. After CHRYSLER_DAS_3_MAX_REFUSALS (3) refusals in a row while engaged
it latches stock fallback: factory DAS_3 frames reach the truck and the comma cannot add braking until controls are no
longer allowed. openpilot still shows engaged, and the host never sees the forwarded factory frames, so the only sign is
the Panda's blocked-transmit counter (pandaStates.safetyTxBlocked, 10 Hz). A rise of MIN_REFUSALS within WINDOW while
long control is active raises a warning that stays until openpilot disengages.

Refusals that are expected are not counted:
- outside a clean engagement (not engaged, long control not active or overridden, factory cruise off, brake or gas
  pressed): the Panda refuses the comma's copies then by design, and pandaStates lags the CAN data;
- for EMERGENCY_HOLDOFF after a factory emergency frame (carStateSP.rampilotFactoryEmergency): the Panda passes factory
  frames for 2 s and refuses the comma's copies, without counting them toward its latch.
"""
from collections import deque

from cereal import car
from openpilot.common.realtime import DT_CTRL
from opendbc.car.chrysler.values import ChryslerSafetyFlags

WINDOW_FRAMES = round(0.2 / DT_CTRL)  # a rise of MIN_REFUSALS within 0.2 s
MIN_REFUSALS = 3  # the Panda's latch count
SETTLE_FRAMES = round(0.2 / DT_CTRL)  # clean engagement before samples count: pandaStates is 10 Hz and lags the CAN data
EMERGENCY_HOLDOFF_FRAMES = round(2.5 / DT_CTRL)  # the Panda's 2 s factory window, plus pandaStates latency


def rampilot_long_enabled(CP: car.CarParams) -> bool:
  """openpilot long on the Ram 1500 DT with the Panda's RamPilot long safety mode."""
  return (CP.carFingerprint == "RAM_1500_5TH_GEN" and CP.openpilotLongitudinalControl and len(CP.safetyConfigs) > 0 and
          bool(CP.safetyConfigs[0].safetyParam & ChryslerSafetyFlags.RAMPILOT_LONG))


class RamPilotLongMonitor:
  def __init__(self):
    self.unavailable = False  # the comma's braking is refused: factory cruise only until disengaged
    self.samples: deque[tuple[int, int]] = deque()  # (frame, safetyTxBlocked) while engaged cleanly
    self.clean_since: int | None = None
    self.emergency_frame: int | None = None

  def update(self, frame: int, enabled: bool, long_active: bool, clean: bool, tx_blocked: int | None,
             emergency: bool) -> bool:
    """`tx_blocked` is pandaStates[0].safetyTxBlocked when pandaStates updated this frame, else None. Returns whether
    the warning is raised."""
    if not enabled:
      self.unavailable = False  # the Panda clears its latch when controls are no longer allowed
    if emergency:
      self.emergency_frame = frame
    in_emergency = self.emergency_frame is not None and frame - self.emergency_frame <= EMERGENCY_HOLDOFF_FRAMES

    if not (enabled and long_active and clean) or in_emergency:
      self.clean_since = None
      self.samples.clear()
      return self.unavailable
    if self.clean_since is None:
      self.clean_since = frame
    if tx_blocked is None or frame - self.clean_since < SETTLE_FRAMES:
      return self.unavailable

    if self.samples and tx_blocked < self.samples[-1][1]:
      self.samples.clear()  # the counter restarted (Panda reset)
    self.samples.append((frame, tx_blocked))
    while frame - self.samples[0][0] > WINDOW_FRAMES:
      self.samples.popleft()
    if tx_blocked - self.samples[0][1] >= MIN_REFUSALS:
      self.unavailable = True
    return self.unavailable
