"""Constants of the software that actually drove route 000003cc--27a46b2116 (Nov 14 2026).

Every value below is copied from sunnypilot v0.10.1 staging-tici, commit
1f7233cb983f3c13fdbba47a86f8fbc796ad9211 -- the build recorded in the drive's own logs -- and NOT from
this repository's master, which is a newer (April 2026) controls stack with a different torque
controller, a different FRICTION_THRESHOLD and different Ram 1500 low-speed gating.

Citations are `path:line` at that commit. These values are used only to *interpret* recorded data
offline. Nothing here is sent to a vehicle, and changing a value here changes no vehicle behaviour.
"""

DRIVE_BUILD_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
DRIVE_BUILD_VERSION = "sunnypilot v0.10.1 (staging-tici), master commit 737a6c4236e843034680c951005b38d15815363f"

# --- Car controller (opendbc_repo/opendbc/car/chrysler/values.py:97-107, RAM_DT branch) ---------------
STEER_MAX = 350          # values.py:107 "EPS allows more, up to 350?"  (raw EPS units, NOT Nm)
STEER_DELTA_UP = 6       # values.py:105  per 50 Hz steering frame
STEER_DELTA_DOWN = 6     # values.py:106
STEER_ERROR_MAX = 80     # values.py:99   command may not exceed measured EPS motor torque by more than this
STEER_STEP = 2           # values.py:98   steering CAN message every 2nd 100 Hz frame (50 Hz)
CONTROL_HZ = 100
STEER_THRESHOLD = 120    # values.py:114  |EPS_2.COLUMN_TORQUE| > 120 => carState.steeringPressed
LKAS_REENABLE_FRAMES = 200  # carcontroller.py:71  LKAS bit may not re-assert within 200 frames of a falling edge

# --- Panda safety (opendbc_repo/opendbc/safety/modes/chrysler.h:126-133) -----------------------------------
SAFETY_MAX_TORQUE = 350
SAFETY_MAX_RT_DELTA = 112
SAFETY_MAX_RATE_UP = 6
SAFETY_MAX_RATE_DOWN = 6
SAFETY_MAX_TORQUE_ERROR = 80

# --- CarParams for RAM_1500_5TH_GEN (opendbc_repo/opendbc/car/chrysler/interface.py) -----------------
STEER_LIMIT_TIMER_S = 0.4        # interface.py:24
STEER_ACTUATOR_DELAY_S = 0.2     # interface.py:60
MIN_ENABLE_SPEED_MS = 14.5       # interface.py:87 (sunnypilot _get_params_sp)
MIN_STEER_SPEED_MS = 0.5         # interface.py:86 (0.0 only if CAN 0x4FF is in the fingerprint, :92-94)

# --- Offline torque tune (opendbc_repo/opendbc/car/torque_data/override.toml:43-44, "# Totally new cars")
OFFLINE_LAT_ACCEL_FACTOR = 2.0   # m/s^2 of lateral acceleration assumed per 1.0 normalised torque
OFFLINE_MAX_LAT_ACCEL_MEASURED = 2.0
OFFLINE_FRICTION = 0.05
TORQUE_KP, TORQUE_KI, TORQUE_KF = 1.0, 0.3, 1.0  # opendbc_repo/opendbc/car/interfaces.py:263-265

# --- Lateral saturation logic --------------------------------------------------------------------------
SAT_CHECK_MIN_SPEED_MS = 10.0          # selfdrive/controls/lib/latcontrol.py:12
STEER_LIMITED_BY_SAFETY_EPS = 1e-2     # selfdrive/controls/controlsd.py:195
STEER_SAT_UNDERSHOOT_RATIO = 1.2       # selfdrive/selfdrived/selfdrived.py:411
STEER_SAT_TURNING_LAT_ACCEL = 1.0      # selfdrive/selfdrived/selfdrived.py:412
STEER_SAT_RECENT_PRESS_S = 2.0         # selfdrive/selfdrived/selfdrived.py:404
MAX_LATERAL_ACCEL_NO_ROLL = 3.0        # selfdrive/controls/lib/drive_helpers.py:14
MAX_LATERAL_JERK = 5.0                 # selfdrive/controls/lib/drive_helpers.py:13
GRAVITY = 9.81

# --- Live torque learner (selfdrive/locationd/torqued.py:36,77) -------------------------------------------
# Chrysler is not in this list, so liveTorqueParameters.useParams is False and the learned values are
# published but NOT applied to the controller in this build.
TORQUED_ALLOWED_BRANDS = ("toyota", "hyundai", "rivian", "honda")

# --- Logging (cereal/services.py at the drive commit): service -> (publish Hz, qlog decimation) ------------
# decimation None = not written to qlogs at all (rlog only).
SERVICES = {
  "carState": (100., 10),
  "carControl": (100., 10),
  "carOutput": (100., 10),
  "controlsState": (100., 10),
  "selfdriveState": (100., 10),
  "pandaStates": (10., 1),
  "onroadEvents": (1., 1),          # published every 1 s AND on every change of the event list (selfdrived.py:538-544)
  "liveTorqueParameters": (4., 1),
  "liveParameters": (20., 5),
  "liveDelay": (4., 1),
  "radarState": (20., 5),
  "can": (100., 2053),              # ~3 raw CAN frames per qlog segment: useless for bus diagnosis
  "sendcan": (100., 139),
  "modelV2": (20., None),           # absent from qlogs: model desired curvature needs rlogs
}

# Event names whose ordinals were verified identical between the drive-build schema and this branch's
# schema. Driver-monitoring events 33-38 were renamed upstream (e.g. preDriverDistracted ->
# driverDistracted1), so decoding those with the wrong schema mislabels them.
VERIFIED_EVENT_ORDINALS = {
  "canError": 0, "steerUnavailable": 1, "steerTempUnavailable": 7, "gasPressedOverride": 13,
  "steerOverride": 14, "controlsMismatch": 22, "radarFault": 25, "steerTempUnavailableSilent": 31,
  "belowSteerSpeed": 39, "commIssue": 43, "commIssueAvgFreq": 44, "steerSaturated": 66,
  "processNotRunning": 75, "usbError": 78, "canBusMissing": 80, "selfdrivedLagging": 81,
  "steerTimeLimit": 83, "steerDisengage": 94,
}
