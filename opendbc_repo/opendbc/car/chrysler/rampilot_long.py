"""RamPilot longitudinal control for the Ram 1500 DT: the comma can only add braking.

The factory cruise module (DASM, camera side, bus 2) keeps running and computing its own DAS_3 at 50 Hz. With openpilot
longitudinal on, the comma re-sends every factory DAS_3 on bus 0, built from that frame (COUNTER included, checksum
recomputed):

- unchanged (byte for byte) unless openpilot longitudinal is active, the factory cruise is active, and the comma
  adds braking (with hysteresis) or holds a stop it made;
- when it adds braking: ACC_DECEL_REQ is set, ACC_DECEL is the stronger of the comma's request (limited to ACCEL_MIN)
  and the factory's own active request, ENGINE_TORQUE_REQUEST is at most 0 Nm and ENGINE_TORQUE_REQUEST_MAX is cleared;
- nothing else is ever changed: ACC_ACTIVE, ACC_AVAILABLE, ACC_FAULTED, COLLISION_BRK_PREP, ACC_BRK_PREP, GR_MAX_REQ,
  DISABLE_FUEL_SHUTOFF, ACC_STANDSTILL and ACC_GO stay the factory's.

So the comma never removes factory braking and never asks for more engine torque than the factory. The Panda's
RamPilot long safety mode (safety param ChryslerSafetyFlags.RAMPILOT_LONG) checks each comma DAS_3 against the latest
factory frames. Design notes: LONG-DESIGN.md (kept with the RamPilot project notes, outside this repository).

Hysteresis: braking starts at accel <= START_BELOW and at least START_MARGIN below the factory's request; once started
it continues while accel < KEEP_BELOW and below the factory's request.

Hold: factory cruise still wants its set speed, so releasing the comma's braking at a standstill would let the factory
drive off by itself. When the comma's added braking was active as the truck came to a stop, the comma keeps braking
(HOLD_ACCEL, torque cut) whatever the planner asks, until the driver confirms with the resume or accel button or the
gas pedal. Leaving long control or factory cruise ends it at once. A stop the factory made on its own is never held.

Gas pedal: the Panda refuses added braking while the gas is pressed, so a frame with the gas pressed is passed through
and resets the state, as if long control were not active.

Several factory frames in one update: the last COPIES_PER_UPDATE are answered, oldest first (the Panda accepts a copy of
the latest or the previous factory frame), each built from its own frame, with the state carried from one to the next.
"""
from dataclasses import dataclass

# Strongest deceleration the comma asks for. It packs to ACC_DECEL raw 2559 (-3.4998 m/s^2), inside commaai/panda #1719's
# min_accel (raw 2558, -3.5042 m/s^2).
ACCEL_MIN = -3.5  # m/s^2

# ENGINE_TORQUE_REQUEST with no engine torque requested (raw 2000).
TORQUE_INACTIVE = 0.  # Nm

# Factory ACC_DECEL_REQ value while the DASM requests braking: 1 was the only nonzero value in 1,656,260 factory DAS_3
# frames (Codex item 23, factory-acc-23/observed.json: 0 x 1,627,603, 1 x 28,657).
# TODO: Codex item 56 verifies the value across all recorded drives.
DECEL_REQ_ACTIVE = 1

# Hysteresis (m/s^2)
START_BELOW = -0.25  # start adding braking at or below this request...
START_MARGIN = 0.25  # ...that is also at least this far below the factory's request
KEEP_BELOW = -0.05  # keep adding braking while the request is below this and below the factory's request

# Hold after a comma stop
STOPPED_SPEED = 0.3  # m/s, below this (or CarState standstill) the truck counts as stopped
HOLD_ACCEL = -2.0  # m/s^2, the interface's stopAccel

# Factory emergency frame: COLLISION_BRK_PREP set, or a decel request stronger than -3.5 m/s^2 (ACC_DECEL below raw 2558,
# the Panda's test). After each one the Panda passes factory frames straight to the truck for 2 s and refuses the
# comma's copies; the factory shows its own forward collision warning.
EMERGENCY_DECEL = 2558 * 0.004885 - 16.  # -3.50417 m/s^2, ACC_DECEL raw 2558 as the parser computes it


def is_emergency(das_3: dict[str, float]) -> bool:
  return das_3["COLLISION_BRK_PREP"] != 0 or (das_3["ACC_DECEL_REQ"] != 0 and das_3["ACC_DECEL"] < EMERGENCY_DECEL)


# Signals the comma may change. Every other field is always copied from the factory frame.
CHANGEABLE = ("ACC_DECEL_REQ", "ACC_DECEL", "ENGINE_TORQUE_REQUEST", "ENGINE_TORQUE_REQUEST_MAX")


@dataclass(frozen=True)
class StandstillInputs:
  stopped: bool = False  # CarState standstill or vEgo < STOPPED_SPEED
  confirm: bool = False  # the driver's resume/accel button event or gas pedal since the last decision
  gas_pressed: bool = False  # CarState gasPressed now


@dataclass(frozen=True)
class LongState:
  braking: bool = False  # adding braking (hysteresis)
  hold: bool = False  # holding a stop the comma made, until the driver confirms
  # stopped at the last decision. True after a reset, so starting already stopped never counts as the comma's stop.
  stopped: bool = True


def factory_accel(das_3: dict[str, float]) -> float:
  """The factory's implied acceleration request in m/s^2: its ACC_DECEL while it requests braking, otherwise 0
  (no braking requested; any engine torque request is not expressed in m/s^2)."""
  return das_3["ACC_DECEL"] if das_3["ACC_DECEL_REQ"] != 0 else 0.


def _braking(das_3: dict[str, float], decel: float) -> dict[str, float]:
  factory_braking = das_3["ACC_DECEL_REQ"] != 0
  if factory_braking:
    decel = min(decel, das_3["ACC_DECEL"])  # never weaker than the factory's own active deceleration
  return {
    "ACC_DECEL_REQ": das_3["ACC_DECEL_REQ"] if factory_braking else DECEL_REQ_ACTIVE,
    "ACC_DECEL": decel,
    "ENGINE_TORQUE_REQUEST": min(das_3["ENGINE_TORQUE_REQUEST"], TORQUE_INACTIVE),
    "ENGINE_TORQUE_REQUEST_MAX": min(das_3["ENGINE_TORQUE_REQUEST_MAX"], 0),
  }


def das_3_overrides(das_3: dict[str, float], long_active: bool, accel: float, state: LongState,
                    inputs: StandstillInputs) -> tuple[dict[str, float], LongState]:
  """Signals to change in the factory DAS_3 `das_3` (parsed values), and the next state. An empty dict passes the
  factory frame through. `accel` is openpilot's requested acceleration (CarControl.actuators.accel)."""
  # Braking is added only inside an active factory cruise session: the frame's ACC_ACTIVE is never changed, and a
  # brake request in a frame that says ACC is off is not a combination the factory sends. Leaving either ends any
  # braking and hold at once. The gas pedal does the same: the Panda refuses added braking while it is pressed.
  if not long_active or inputs.gas_pressed or das_3["ACC_ACTIVE"] != 1:
    return {}, LongState()

  factory = factory_accel(das_3)
  hold, braking = state.hold, state.braking
  if hold and inputs.confirm:
    hold = braking = False  # the driver confirmed: braking only if the planner asks past the start threshold

  # comparisons are false for NaN: never starts, and stops
  if braking:
    braking = accel < KEEP_BELOW and accel < factory
  else:
    braking = accel <= START_BELOW and accel <= factory - START_MARGIN

  # the comma's braking was active as the truck came to a stop: hold it there
  if not hold and not inputs.confirm and inputs.stopped and not state.stopped and (state.braking or braking):
    hold = True

  if hold:
    overrides = _braking(das_3, HOLD_ACCEL)
  elif braking:
    overrides = _braking(das_3, max(accel, ACCEL_MIN))
  else:
    overrides = {}
  return overrides, LongState(braking=braking, hold=hold, stopped=inputs.stopped)


# Factory COUNTER order. The comma sends a copy only when the factory counter moved forward from its last copy, so it
# never repeats a counter or goes back to an older one. After a gap with no copy, any counter starts a new sequence.
COUNTER_FORWARD_MAX = 7  # a step of 1..7 is forward; 0 and 8..15 are a repeat or an older frame
COUNTER_RESYNC_FRAMES = 10  # 100 ms of controller frames without a copy
COPIES_PER_UPDATE = 2  # factory frames answered per update: the latest and the previous; older ones are skipped


def counter_moves_forward(counter: int, last_counter: int | None, frames_since_last: int) -> bool:
  if last_counter is None or frames_since_last > COUNTER_RESYNC_FRAMES:
    return True
  return 1 <= (counter - last_counter) % 16 <= COUNTER_FORWARD_MAX
