"""Constants shared by every Drive Advisor measure.

Each rule is chosen once and lives here. No measure defines its own copy, and
nothing received over the network can change a value.

The only clock is the time stamp of the carState message behind a sample.
Every duration is a sum of capped sample intervals; nothing is derived from a
sample count. Constants that used to be sample counts carry half a nominal
sample (0.05 s), so that time-stamp jitter cannot decide whether the edge
sample is in or out.
"""
from __future__ import annotations

# --- time base ---------------------------------------------------------------
DT_CAP_S = 0.25               # longest interval one sample may stand for; a longer spacing is a gap
FIRST_DT_S = 0.10             # interval of the first sample of a drive
MODEL_DT_CAP_S = 1.0          # longest interval one model message may stand for
WINDOW_S = 60.0               # summary windows: a fixed grid from the first sample of the drive

# --- joining streams ---------------------------------------------------------
FUTURE_TOL_S = 0.100          # a message this much newer than its carState still belongs to it
FRESH_S = 0.500               # a message older than this is missing (live and full-rate logs)
FRESH_MODEL_QLOG_S = 1.25     # model stream in decimated logs (2 Hz)
FRESH_LEAD_QLOG_S = 0.75      # lead stream in decimated logs (4 Hz)
ALERTS_FRESH_S = 5.0          # the last alert list is trusted this long

# --- what "steering on" means --------------------------------------------------
ALERT_BELOW_STEER_SPEED = "belowSteerSpeed"   # steering is switched on but held at zero
ALERT_STEER_SATURATED = "steerSaturated"      # the one counted steering warning
LATCH_SPEED_MS = 14.5         # fallback while no alert list is available: steering starts after this speed
LATCH_RESET_GAP_S = 5.0       # a hole this long hides what the truck did; the fallback forgets

# --- driver steering -----------------------------------------------------------
PRESS_MIN_SPAN_S = 0.08       # consecutive pressed samples must span this before they count
DRIVER_RECENT_S = 2.05        # used only to split steering-limit time

# --- clean, eligible, straight, curve ------------------------------------------
HOLD_AFTER_S = 5.0            # nothing is scored this long after a disturbance (closed interval)
HOLD_BEFORE_S = 2.0           # nor this long before one
EVAL_LATE_S = 6.0             # wheel swings, lateral-motion RMS and curve sums evaluate this late
EVAL_LATE_LANE_S = 8.6        # lane sway evaluates this late
V_MIN_QUALITY_MS = 15.0       # speed floor of the four quality measures
STRAIGHT_HALF_S = 5.05        # straight: mean desired curvature over this much each side
STRAIGHT_MEAN_CURV = 0.0002   # 1/m
STRAIGHT_MIN_COVER_S = 8.0    # least time with a known curvature in that window
SWING_TREND_HALF_S = 5.05     # wheel-swing trend window, each side
LANE_HALF_S = 5.0             # lane-sway window, each side (closed interval, deliberately not padded)
CURVE_DEMAND_MS2 = 0.5        # curve sample: speed^2 x |desired curvature| at least this
SHARP_DEMAND_MS2 = 1.5        # label on steering-limit events only
CURVE_EVENT_GAP_S = 3.15      # a curve sample this long after the previous one starts a new curve
LAG_TRAIL_S = 3.05            # lag samples continue this long after a curve

CURVE_LOW_MIN_MS = 5.0        # low-speed curve line: from this speed up to the quality floor
LAG_STEP_S = 0.1              # response lag: the demand is compared at whole steps of this ...
LAG_STEPS = 10                # ... from none to this many

# --- steering command at its limit, warnings -------------------------------------
AT_LIMIT_TORQUE = 343         # 0.98 x 350, the truck's largest steering command
ASK_LIMIT = 0.98              # of the full command asked for
EVENT_MERGE_S = 10.0          # warnings or limit samples closer than this are one event
LIMIT_EVENT_MIN_S = 0.4       # a shorter limit event is not recorded

# --- learned steering delay ---------------------------------------------------------
DELAY_TRUST_MIN_S = 0.05      # a learned delay is trusted only strictly between these two ...
DELAY_TRUST_MAX_S = 0.95      # ... and only with the learner's status `estimated`
DELAY_BAND_MIN_S = 0.28       # every recorded drive stayed inside this band; a value outside it is marked
DELAY_BAND_MAX_S = 0.38

# --- driving time ------------------------------------------------------------------
MOVING_ABOVE_MS = 0.5         # above this speed the truck is driving; "driving" always means this
LOSS_WINDOW_S = 0.3           # steering losses: evidence looked for this long before and after the edge
LOSS_MIN_SPEED_MS = 5.0       # steering losses: only edges at or above this speed are recorded
MIN_DRIVE_MOVING_S = 60       # service: a session with less movement is not a drive

# --- service: least data behind a stored quality value ----------------------------
LANE_MIN_SCORED_S = 20.0      # lane sway of a window
SWING_MIN_STRAIGHT_S = 15.0   # swing rate of a window
AY_MIN_S = 30.0               # lateral-motion RMS of a window ...
AY_PERIOD_MIN_S = 0.095       # ... whose loop period (windowS / n) lies in this range
AY_PERIOD_MAX_S = 0.125
LAG_MIN_EVENTS = 30           # response lag of a span: curve events ...
LAG_MIN_S = 200.0             # ... and seconds of lag data

# --- model stream: fast (live, full-rate logs) or slow (decimated logs) ------------
MODEL_SPEED_SPACINGS = 20     # stream speed: median of this many spacings between new messages
MODEL_SLOW_PERIOD_S = 0.2     # a median spacing above this is a slow stream
MODEL_PERIOD_MIN_S = 0.05     # the median is clamped to this range
MODEL_PERIOD_MAX_S = 0.5
MODEL_GAP_FAST_S = 0.35       # two new messages further apart than this are a gap in the stream
MODEL_GAP_SLOW_S = 1.25
