# Claude independent engineering review — Ram 1500, route 000003cc--27a46b2116

Reviewer: Claude (Opus 5.5), 2026-11-17. Branch `claude/ram1500-review-2026-11-17` (off `ram1500-custom-development`).
Scope: read-only. No control code, panda safety, limits or vehicle behaviour were changed.

> **Data access.** I did not read the Nov 14 logs. A read of the Railway bucket was blocked by my tool
> permissions and no local copy exists. Every conclusion below comes from (a) source code at the exact build
> that drove and (b) the summary numbers already in `PROJECT_PLAN.md`. Each claim carries a confidence level;
> "needs data" marks what the new tools settle once the qlogs are supplied (section 9).

## 1. Executive summary

| # | Finding | Confidence |
|---|---|---|
| 1 | The Nov 14 drive ran **sunnypilot v0.10.1 staging-tici `1f7233cb`** (Oct 2025). This fork's `master` is the April 2026 stack and shares **no git history** with it. Every steering-relevant file differs. The plan's pointer to this branch's `latcontrol_torque.py` describes a controller that never ran on the truck. | High (GitHub compare + file diffs) |
| 2 | The Ram 1500 torque model in that build is a **placeholder**: `[latAccelFactor 2.0, maxLatAccel 2.0, friction 0.05]` under the heading `# Totally new cars`, and the online-learned values were **published but not applied** (Chrysler is not on the torque learner's allow-list). | High (source) |
| 3 | `steerSaturated` can only fire when the car-side limiter was **not** clipping the command. If the trigger was the torque cap, the truck was receiving ≈350 raw units (the ceiling shared by the car controller and panda safety) with the EPS reporting ≥ ~267 units, and still under-turning. That is a **controller-authority limit**, not a CAN fault and not evidence of an EPS failure. A second path (planner curvature clip) also exists; the qlogs separate the two. | High for the logic; mechanism needs data |
| 4 | "One sample" of `steerSaturated` = **one episode of under ~1 s**. Event messages are logged every second *and on every change*, so episode start/end are exact to 10 ms. Sample counts (1,675 `steerOverride`, 916 `gasPressedOverride`) are neither incident counts nor seconds. | High (source) |
| 5 | End-of-route `controlsMismatch` + `canError` match what this code does at **ignition-off** (panda RX checks fail; car CAN validity drops). `selfdrived.initialized` is logged with `error=True` on **every** start and is not a fault. | High for the mechanism; classification of this route needs data |
| 6 | qlogs cannot support raw-bus CAN diagnosis (~3 CAN frames per segment) and do not contain `modelV2`, the input to the alert's under-steer test. Segment-level **rlogs** are required for those questions. | High (source) |
| 7 | A supported low-speed path already exists upstream for specific Ram EPS firmware (engage from 0 m/s on `68273275AF/AG`, `68312176AE/AG`), present in this fork's master but **not** in the drive build. The truck's EPS part number is in the logged CarParams. | High (source); applicability needs data |

## 2. Provenance: what actually drove

- Recorded build: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, message "sunnypilot v0.10.1 (staging-tici)", built
  2025-10-13, upstream master `737a6c42…`, **dirty working tree** (local edits of unknown content).
- `gh api repos/sunnypilot/sunnypilot/compare/1f7233cb…master` → "No common ancestor" (release branches are squashed).
- Line-ending-insensitive diff, drive build vs. this fork's master (lines changed):
  `latcontrol_torque.py` 102, `chrysler.h` 214, `controlsd.py` 68, `carstate.py` 60, `selfdrived.py` 48,
  `interface.py` 32, `latcontrol.py` 22, `carcontroller.py` 21, `values.py` 11, `carcontroller_ext.py` 7,
  `latcontrol_torque_ext.py` 6, `lateral.py` 4 (`FRICTION_THRESHOLD` 0.3 → 0.2).
- Consequence: baseline replay must pin `1f7233cb`. The dirty tree means even that is an upper bound;
  `initData.dirty` and `gitCommit` are reported by the new tool for every segment.

## 3. Ram 1500 platform constraints at the drive build (Phase 2 starter matrix)

Citations are `path:line` at `1f7233cb`.

| Constraint | Value | Where | Enforced by |
|---|---|---|---|
| Max steering torque | 350 raw units; source comment: "EPS allows more, up to 350?" | `opendbc/car/chrysler/values.py:107` | car controller **and** panda safety (`safety/modes/chrysler.h:126-133`) |
| Torque rate | 6 units per 50 Hz frame, up and down (≥ 1.18 s from 0 to 350) | `values.py:105-106`, `chrysler.h:129-130` | both |
| Command vs. measured EPS torque | command clamped to measured motor torque ± 80 | `values.py:99`, `opendbc/car/lateral.py:50-75` | both (`TorqueMotorLimited`) |
| Real-time delta | 112 units | `chrysler.h:128` | panda |
| Units | DBC factor 1, offset −1024 for `COLUMN_TORQUE`, `EPS_TORQUE_MOTOR`, `STEERING_TORQUE`; **no Nm calibration exists** | `opendbc/dbc/generator/chrysler/_stellantis_common.dbc` (EPS_2), `chrysler_ram_dt.dbc` (LKAS_COMMAND 0xA6) | — |
| Driver override | `steeringPressed` when \|COLUMN_TORQUE\| > 120 | `values.py:114`, `carstate.py:72-74` | openpilot |
| EPS temporary fault | `EPS_3.DASM_FAULT` | `carstate.py:89` | openpilot (no permanent-fault signal mapped for Ram) |
| Engage speed | `minEnableSpeed` 14.5 m/s (~32 mph) | `interface.py:87` | openpilot |
| Steer floor | `minSteerSpeed` 0.5 m/s (0 only if CAN 0x4FF is present) | `interface.py:86,92-94`; `controlsd.py:112` | openpilot |
| LKAS control bit (RAM_DT) | latches on only while passing 14.5–15.0 m/s; stays on down to the steer floor; forced off outside Drive; cannot re-assert within 200 frames (2 s) of dropping | `sunnypilot/car/chrysler/carcontroller_ext.py:14-24`, `carcontroller.py:71` | openpilot |
| Saturation timer | `steerLimitTimer` 0.4 s; actuator delay 0.2 s | `interface.py:24,60` | openpilot |
| Torque tune | kp 1.0, ki 0.3, kf 1.0; latAccelFactor 2.0, friction 0.05 (placeholder) | `interfaces.py:263-265`, `torque_data/override.toml:43-44` | openpilot |
| Learned torque | not applied (`useParams` False for brand `chrysler`) | `locationd/torqued.py:36,77`, `controlsd.py:94` | openpilot |
| NNLC | a `RAM_1500_5TH_GEN` model ships; used only if the `NeuralNetworkLateralControl` toggle was on | `sunnypilot/.../nnlc/nnlc.py:38,63-64` | openpilot |

Notes:
- Older-EPS "steer to zero" (`interface.py:62-64`, FW ≤ 6831) is overwritten to 0.5 m/s by sunnypilot's
  `_get_params_sp` (`interface.py:86`) in this build; the fork's master fixes that ordering.
- The LKAS bit latch has an edge case: if the controls stack restarts while the truck is already above
  15 m/s, the bit stays off until the truck passes back through 14.5–15.0 m/s. Not observed; listed as a question.
- **Self-parking proves the EPS can move the rack at low speed; it does not show that the LKAS message path
  accepts torque at those speeds.** That remains a vehicle-interface question (section 8, U6).

## 4. The 171-second `steerSaturated` event

### 4.1 Exact firing conditions in the drive build

Per 100 Hz frame, `latcontrol.py:24-31` adds 10 ms to a timer when
`(|torque output| ≥ 0.999 OR curvature_limited) AND vEgo > 10 m/s AND NOT steer_limited_by_safety AND NOT steeringPressed`,
and subtracts 10 ms otherwise; `saturated` is true while the timer sits at 0.4 s. `selfdrived.py:402-415`
raises the alert when, additionally, lateral control is active, no steering press occurred in the last 2.0 s,
the **model's** desired lateral acceleration exceeds 1.0 m/s², and desired ÷ achieved > 1.2.
`steer_limited_by_safety` (`controlsd.py:195`) is true whenever the car interface's output torque differs
from the command by more than 0.01.

### 4.2 What that implies

- **Path A, torque cap.** Output pinned at ±1.0 → command 350 units. For `steer_limited_by_safety` to stay
  false, the car output must equal the command within 0.01 (≥ 347 units). The ±80 measured-torque window
  only allows that when the EPS reports ≥ ~267 units of motor torque. So: the EPS was delivering near the
  software ceiling, the driver was not intervening, and the truck still achieved < 1/1.2 of the requested
  lateral acceleration.
- **Path B, curvature clip.** `curvature_limited` (`drive_helpers.py:25-39`) is true when the planner's
  curvature is clipped at 3.0 m/s² (± roll) lateral acceleration or by the 5 m/s³ jerk-rate limit. Then the
  torque output need not be saturated at all.
- With the placeholder latAccelFactor of 2.0, the stock torque controller reaches its ±1.0 output once
  `p + i + f ≥ 2.0 m/s²`. If the truck's real gain is different, the controller's internal model of "how
  much torque this curve needs" is wrong in a speed- and load-dependent way. That is the first thing to
  measure (section 7, E1). It does not change the 350 ceiling.

### 4.3 Ranked explanations

| Rank | Explanation | Confidence now | Settled by |
|---|---|---|---|
| 1 | Controller authority limit: 350-unit ceiling reached on a curve requiring > ~2 m/s² while the vehicle under-turned (path A) | Medium (code-consistent; the ~0.4–1.4 s total saturation fits one curve entry) | `torqueState.output`, `carOutput.actuatorsOutput.torque/torqueOutputCan`, `steeringTorqueEps` in the ±15 s timeline |
| 2 | Planner curvature clip (path B) on a sharp transition | Low–medium | `controlsState.desiredCurvature × v²` at the 3.0 m/s² clip with unsaturated output |
| 3 | NNLC active, with its error-blend raising torque demand above 1–2 m/s² desired lateral accel (`nnlc.py:145-151`) | Low (toggle state unknown) | `initData.params.NeuralNetworkLateralControl` and the PID-ratio test (section 6) |
| 4 | Telemetry artefact (event mislabelled by schema mismatch) | Very low: ordinal 66 = `steerSaturated` in both schemas | — |
| 5 | EPS fault / CAN fault | Very low: a temporary EPS fault drops `latActive` (`controlsd.py:117`), which stops the saturation timer | `steerFaultTemporary`, `canValid` in the window |

**Cannot be distinguished from qlogs:** whether 350 is the EPS's physical limit; the model's unclipped
desired curvature (`modelV2` is rlog-only); 50 Hz command / rate-limit interplay (qlogs keep every 10th
sample of each 100 Hz service, decimated independently, so cross-service samples are up to ~100 ms apart —
the timelines report this skew per row).

## 5. End-of-route faults (~3002 s) and startup messages

- `controlsMismatch` (`selfdrived.py:311-322`): safety model/param mismatch after 10 s, **or**
  `pandaState.safetyRxChecksInvalid`, **or** enabled while panda `controlsAllowed` is false for 200 frames.
  At ignition-off the ECUs stop transmitting and RX checks go invalid.
- `canError` (`selfdrived.py:347-358`): `radarState.radarErrors.canError` or `carState.canValid` false
  (a full timeout raises `canBusMissing` instead). Car CAN validity drops as the bus goes quiet.
- Both events in the last seconds of a drive are therefore the expected shutdown signature, **provided**
  speed ≈ 0, lateral control inactive and/or ignition off at those timestamps. The new classifier never calls
  an event benign when speed is missing; an event while moving and engaged is classified `in_motion_engaged`, severity high.
- Startup `controlsMismatch` is compatible with the panda still switching safety modes / RX checks warming up.
- `selfdrived.initialized` (`selfdrived.py:458-485`) is always logged with `error=True`. Only its `timeout`
  flag and its `invalid/not_alive/not_freq_ok` lists carry information; the tool reports them and whether
  each error kind **persisted** beyond the first 60 s.

## 6. Tools added (all offline, read-only)

| File | Purpose |
|---|---|
| `ramtools/schema.py` | Fetches the five schema files of `1f7233cb` and verifies each against its **git blob SHA-1**; refuses to decode with anything else |
| `ramtools/logio.py` | `.zst`/`.bz2`/raw decoding, per-file truncation reporting, segment numbers from paths, privacy-minimised records (no VIN, dongle id, GPS position, raw CAN, device context; params whitelist) |
| `ramtools/episodes.py` | Episode reconstruction from on-change `onroadEvents`, exact durations, censoring and gap flags |
| `ramtools/timeline.py` | As-of joins with per-row skew columns; CSV + units sidecar |
| `ramtools/saturation.py` | Offline copy of the RAM_DT limiter (fuzz-tested against the drive-build logic and against opendbc), mechanism A/B classification, stock-vs-NNLC inference from PID terms (stock: −output ÷ (p+i+f) ≈ 1/latAccelFactor = 0.5; NNLC ≈ 1.0), route-wide near-saturation runs and lateral-accel tracking error by speed band |
| `ramtools/faults.py` | Fault-episode context (speed, gear, lateral-active, ignition, panda flags) and classification |
| `ramtools/summary.py` | Provenance, CarParams checks (placeholder tune, allow-list, EPS FW), live-torque `useParams`, wall-clock anchor (GPS time, not position), error-log persistence |
| `investigate_route.py` | One command → `summary.json`, `findings.md`, `episodes.csv`, `faults.csv`, `steerSaturated_<n>_pm15s.csv` / `_pm30s.csv`; refuses to write inside the git repo |
| `analyze_route.py` | Existing script fixed (section 6.1); old LogReader path kept behind `--unpinned` |
| `tests/` | 31 tests: episodes, limiter parity, mechanisms, controller-mode, faults, privacy, CLI guards, real Cap'n Proto end-to-end with the pinned schema |

### 6.1 Audit of the original `analyze_route.py`

1. Every sample's `file` was `qlog.zst` → the segment was unrecoverable. Fixed: segment from the path.
2. Counted samples only. Fixed: episodes with exact durations, censoring and gap flags.
3. Decoded with this branch's schema via `openpilot.tools.lib.logreader`, while README says the schema "at the
   observed commit" was used. Fixed: pinned schema with hash verification. (The relevant event ordinals happen to
   match; driver-monitoring events 33–38 do not.)
4. One unreadable/truncated segment aborted the run. Fixed: per-file error reporting.
5. Time base was "first message of whatever files were passed", unlabelled. Now explicit, plus GPS/initData wall-clock anchors.

### 6.2 Gotcha found while testing

Loading the pinned drive-build schema and this branch's `opendbc` (which loads its own `car.capnp`) in the
same Python process makes pycapnp **abort** (duplicate schema ids). Keep pinned-log analysis and branch code
in separate processes; the opendbc parity test runs in a subprocess for this reason.

## 7. Smallest safe offline experiments (no vehicle changes)

| ID | Experiment | Pass/fail evidence |
|---|---|---|
| E1 | Fit the truck's real lateral-accel-per-torque from the full route: steady segments, v > 15 m/s, no driver torque, roll-compensated, using `carOutput` torque and EPS motor torque vs. `actualLateralAccel` | Fitted factor with confidence interval vs. the 2.0 placeholder; a gap > 20 % justifies a separate, replay-tested PR to the torque data (not in this branch) |
| E2 | Route baseline: near-saturation runs and tracking error per speed band (already computed by the tool) | Numbers to beat in any later controller PR |
| E3 | Determine NNLC on/off and EPS firmware from the log | `initData.params`, PID-ratio test, `carParams.carFw` (eps) |
| E4 | Replay harness pinned to `1f7233cb` (openpilot process replay on rlogs) | Reproduces the logged torque command within tolerance before any change is evaluated |

Not recommended: raising `STEER_MAX`, rate or error limits, editing panda safety, lowering `minEnableSpeed`
by flag or spoofing, or porting master's low-speed rule without matching EPS firmware evidence.

## 8. Unknowns

| ID | Unknown | Needed evidence |
|---|---|---|
| U1 | Mechanism of the 171 s event (A vs B) | qlog timeline (have tool) |
| U2 | Model's unclipped desired curvature at 171 s | rlog segment 2 (`modelV2`) |
| U3 | NNLC toggle state | `initData.params` (qlog) |
| U4 | EPS firmware part number | `carParams.carFw` (qlog) |
| U5 | Whether 350 is the EPS physical limit | OEM documentation or controlled bench data; not a road experiment |
| U6 | LKAS acceptance below 14.5 m/s on this truck | EPS FW (U4) + upstream sunnypilot support status |
| U7 | Contents of the dirty working tree | device `git diff` |
| U8 | Speed / lateral-active / ignition at the ~3002 s faults | qlog fault classification (have tool) |
| U9 | Whether startup "dropped frames" errors persisted | error-log persistence (have tool) |

## 9. Next three testable steps

1. **Run the investigation on the 51 qlogs** (outside the repo):
   `python ram1500-development/investigate_route.py --fetch-schema --schema-dir ~/.cache/ram1500/schema-1f7233cb --out ~/ram-analysis/000003cc--27a46b2116 <dir>/000003cc--27a46b2116--*/qlog.zst`
   Settles U1, U3, U4, U8, U9. Expected if section 4 holds: mechanism `A_torque_authority`, `abs_can_torque_sent_max_raw` ≈ 350,
   EPS motor torque ≥ ~267 in the window; faults classified `stationary_shutdown_or_parked`.
2. **Fetch rlogs for segments 0, 2 and the last two segments**, rerun the same command on them: settles U2 and gives
   100 Hz command/EPS traces and bus-level CAN at shutdown. Confirm upload by listing files, not by an accepted Athena request.
3. **Run E1** on the full route and the earlier 36-minute route `000003cb--26235ffa36`; publish the fitted factor and
   residuals in a follow-up report. Only then propose any tuning change, as its own PR with replay evidence.
