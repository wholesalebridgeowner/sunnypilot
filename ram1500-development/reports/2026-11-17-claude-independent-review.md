# Claude independent engineering review — Ram 1500 / route 000003cc--27a46b2116

Status: **DRAFT / IN PROGRESS** (checkpoint pushed early because the workstation may restart).
Reviewer: Claude (Opus 5.5), 2026-11-17. Branch: `claude/ram1500-review-2026-11-17` (off `ram1500-custom-development`).

**Data access:** I did **not** read the Nov 14 logs. The Railway bucket read was blocked by my tool
permissions, and no local copy exists. Everything below is grounded in source code at the exact drive
build plus the summary numbers in `PROJECT_PLAN.md`. Claims that need the data are marked *needs data*.

## 1. The most important correction: the reviewed code is not the code that drove

- The drive ran **sunnypilot v0.10.1 staging-tici, commit `1f7233cb983f…`** (built 2025-10-13, upstream
  master `737a6c42…`). That commit is a squashed release commit with **no common ancestor** with this
  fork's `master` (GitHub compare returns "No common ancestor").
- This fork's `master`/`ram1500-custom-development` is the **April 2026** sunnypilot stack. Every
  steering-relevant file differs from the drive build: `latcontrol.py`, `latcontrol_torque.py`
  (completely different torque controller), `controlsd.py`, `selfdrived.py`, Chrysler
  `carcontroller.py`/`interface.py`/`values.py`/`carstate.py`, `lateral.py` (FRICTION_THRESHOLD 0.3 → 0.2),
  panda `chrysler.h`, and the sunnypilot Chrysler extension.
- Consequence: `PROJECT_PLAN.md`'s pointer to `selfdrive/controls/lib/latcontrol_torque.py` on this branch
  is the wrong reference for explaining the Nov 14 drive. Any replay or "baseline" must pin `1f7233cb`.
  The drive also reported a **dirty working tree**, so even `1f7233cb` is only an upper bound on what ran.

## 2. Code facts at the drive build (citations are `path:line` at 1f7233cb)

| Fact | Source |
|---|---|
| Ram 1500 torque cap `STEER_MAX = 350` raw EPS units, comment "EPS allows more, up to 350?" | `opendbc_repo/opendbc/car/chrysler/values.py:107` |
| Rate limit 6 units per 50 Hz frame up/down → ≥1.17 s from 0 to full torque | `values.py:105-106` |
| Command may not exceed **measured EPS motor torque ± 80** (`STEER_ERROR_MAX`) | `values.py:99`, `opendbc/car/lateral.py:50-69` |
| Panda safety enforces the same 350 / 6 / 80 limits | `opendbc/safety/modes/chrysler.h:126-133` |
| Torque signals are raw DBC units (factor 1, offset −1024); **no Nm calibration exists** | `opendbc/dbc/generator/chrysler/_stellantis_common.dbc` EPS_2 |
| `steeringPressed` = \|EPS_2.COLUMN_TORQUE\| > 120 | `values.py:114`, `carstate.py:72-74` |
| Offline torque tune for RAM_1500_5TH_GEN = `[2.0, 2.0, 0.05]` under the heading **"# Totally new cars"** (placeholder, not measured) | `opendbc/car/torque_data/override.toml:43-44` |
| Learned torque params are **not applied** to Chrysler: torqued `ALLOWED_CARS = toyota/hyundai/rivian/honda` → `useParams = False` | `selfdrive/locationd/torqued.py:36,77`; applied only if `useParams` at `controlsd.py:94` |
| `steerLimitTimer = 0.4 s`, `steerActuatorDelay = 0.2 s` | `chrysler/interface.py:24,60` |
| Engagement floor `minEnableSpeed = 14.5 m/s` (~32 mph); `minSteerSpeed = 0.5 m/s` (0 only if CAN 0x4FF seen) | `interface.py:86-94` |
| A Ram 1500 NNLC model ships in this build; it is used only if the `NeuralNetworkLateralControl` toggle was on | `sunnypilot/selfdrive/controls/lib/nnlc/nnlc.py:38,63-64` |

## 3. What `steerSaturated` at ~171 s can and cannot mean (from the drive-build logic)

The alert fires only when **all** of these held (`latcontrol.py:24-31`, `latcontrol_torque.py:97`,
`selfdrived.py:402-415`, `controlsd.py:190-195`):

1. Lateral control active, speed > 10 m/s, driver **not** pressing (|column torque| ≤ 120) and no press in the prior 2.0 s.
2. For a net ~0.4 s, either the torque output was at the ±1.0 cap **or** the desired curvature was being clipped by the
   3.0 m/s² lateral-accel / 5 m/s³ jerk limits (`curvature_limited`).
3. `steer_limited_by_safety` was **false**: the car interface's output torque was within 0.01 of the command.
4. Model desired lateral accel > 1.0 m/s² and more than 1.2× the achieved lateral accel.

Inference (code-derived, *needs data to confirm*): if the trigger was path (A) torque cap, condition 3
means the car controller was actually sending ≈ ±347–350 units, which the ±80 measured-torque window only
allows when the EPS was reporting ≥ ~267 units of motor torque. In that case the truck was at the
**software authority ceiling shared by the car controller and panda safety (350)** and still under-turning.
That is a controller-authority limit, not a CAN fault and not evidence of EPS failure. Path (B) would
instead point at the planner's lateral-accel/jerk clip. qlog fields can separate A from B
(`controlsState.lateralControlState.torqueState.output`, `controlsState.desiredCurvature`,
`carOutput.actuatorsOutput.torque`, `carState.steeringTorqueEps`).

**Not distinguishable from qlogs:** whether 350 is the EPS's physical limit (the source comment itself is a
question), the model's unclipped desired curvature (`modelV2` is rlog-only), and exact 50 Hz command/rate-limit
interplay (qlogs keep every 10th sample of each 100 Hz service, sampled independently).

## 4. Corrections to earlier interpretations

- **"One steerSaturated sample" = one episode shorter than ~1 s.** `onroadEvents` is published every 1 s
  *and on every change* (`selfdrived.py:538-544`) and qlogs keep every message (decimation 1). So episodes and
  their durations can be reconstructed exactly from the on-change messages. Sample counts (e.g. 1,675
  `steerOverride`) mix 1 Hz repeats with change messages: they are neither incident counts nor seconds.
- **`selfdrived.initialized` is not an error condition.** It is a routine startup marker that is always logged
  with `error=True` (`selfdrived.py:466-485`). Only its `timeout` flag and the listed invalid/not-alive services matter.
- **End-of-route `controlsMismatch` + `canError` match normal ignition-off behaviour in this code:** when the truck
  powers down, panda RX checks go invalid (`safetyRxChecksInvalid` → `controlsMismatch`, `selfdrived.py:321-322`)
  and `carState.canValid` drops (`canError`, `selfdrived.py:357-358`). *Needs data*: speed, gear, lateral-active and
  ignition at those timestamps to confirm.
- **`liveTorqueParameters` (~11,995 samples) did not steer the truck** in this build (see §2).
- **Raw CAN in qlogs is ~3 frames per segment** (`services.py:26`, decimation 2053), so qlogs cannot support bus-level CAN diagnosis.
- **Schema check:** the event ordinals for steerSaturated(66), canError(0), controlsMismatch(22), steerOverride(14),
  gasPressedOverride(13) are identical in the drive-build and branch schemas; driver-monitoring events 33-38 were
  renamed, so decoding those with the branch schema mislabels them.

## 5. Existing `analyze_route.py` audit (fixed in this branch — work in progress)

- Every sample's `file` is `qlog.zst` → segment is ambiguous; needs the segment number from the path.
- Counts samples, not episodes; no durations.
- Imports `openpilot.tools.lib.logreader` from this branch → decodes with the April 2026 schema, not the pinned drive schema (README claims otherwise).
- No per-file error handling for truncated segments; no wall-clock anchor; times relative to first message of whatever files were passed.

## 6. Work remaining in this branch

- `ramtools/` decoder with pinned-schema verification (git blob SHA1), episode reconstruction, saturation forensics,
  fault-context classification, controller-mode (stock vs NNLC) inference, unit tests.
- Final report, PROJECT_PLAN cross-review section, and REVIEW_LOG for ChatGPT ↔ Claude.
