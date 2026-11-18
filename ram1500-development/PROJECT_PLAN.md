# Ram 1500 Custom Driving — Engineering Project Plan
Updated: 2026-11-17
Status: RESEARCH / OFFLINE DEVELOPMENT ONLY. No vehicle-ready release is authorized.

## Goal
Build a measured, Ram 1500-specific assisted-driving software stack based on the existing Sunnypilot fork, progressively improving lane centering, behavior on curves, and control smoothness while retaining the vehicle manufacturer's actuator protections, driver supervision and disengagement safeguards. Investigate low-speed operation and navigation-guided capabilities only as separate feasibility studies; do not assume either is supported or approved.

## Source of truth
- Repository: `wholesalebridgeowner/sunnypilot`
- Development branch: `ram1500-custom-development`
- Starting documentation: `ram1500-development/README.md`
- Existing read-only decoder: `ram1500-development/analyze_route.py`
- Relevant controls implementation: `selfdrive/controls/lib/latcontrol_torque.py`; inspect the actual RAM platform integration and its upstream dependency/submodule rather than assuming a particular path.
  - **Claude review 2026-11-17 (disputed, see REVIEW_LOG C-01):** the Nov 14 drive ran `sunnypilot/sunnypilot@1f7233cb` (v0.10.1), which shares no git history with this fork's master; every steering file differs. Explanations and replays of that drive must use the `1f7233cb` copies. Pinned schema and cited constants: `ramtools/schema.py`, `ramtools/constants.py`.
- Live vehicle fingerprint reported in logs: `RAM_1500_5TH_GEN`
- Recorded Saturday drive build: Sunnypilot 0.10.1, `staging-tici` commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, dirty working tree. This is **not** evidence that the `ram1500-custom-development` branch was installed on the vehicle.
- Hardware: telemetry reports `tici`; confirm hardware identity from device instead of making assumptions about Comma version.

## Verified data and key timestamps
Saturday Nov 14 2026, evening route `000003cc--27a46b2116`:
- Start: 2026-11-15 00:29:53 UTC = Nov 14 18:29:53 US Central; about 50 minutes; 51 segments.
- Available Q-logs: 51/51 were parsed; 728,719 Cap'n Proto messages; zero framing failures. 30,000 samples each of `carState`, `carControl`, and `controlsState`; ~11,995 `liveTorqueParameters`.
- One `steerSaturated` event sample at ~171 seconds (~18:32:44 Central), segment 2.
- Six `controlsMismatch` samples near startup and end of route; two `canError` samples at ~3002 sec, near parking/shutdown, not proven to be in-motion failures.
- `steerOverride` 1,675 samples, `gasPressedOverride` 916 samples: do not label these counts as separate driver takeovers.
- Log errors at startup include a skipped model evaluation / dropped 64 frames and `selfdrived.initialized` with `canValid=true` and other invalid or absent process signals. Determine whether they persisted before assigning causal significance.
- Full R-logs for this route were not listed by comma API during the latest direct availability check; requests for 2 R-log segments of this and an earlier route were accepted by Athena, but an accepted request is not a confirmed upload.
- Earlier Saturday evening route `000003cb--26235ffa36`: approximately 36 minutes, 37 Q-log segments, start 17:39:01 Central; do not conflate with the main route.
- Railway storage project: `ram-drive-log-bridge`, collector service `comma-log-sync`, read-only study service `comma-log-inventory`, bucket `ram-drive-logs`. Private data stay private. Collector runs approximately every five minutes; previous successful cycles reported zero retrieval errors. The study service has been used for ad-hoc analysis; its current code should be reviewed before re-use.

## Phase 1: Reproducible telemetry analysis (immediate)
1. Determine the cereal / Cap'n Proto schema and exact software commit appropriate to the recorded `staging-tici` build; document compatibility and fields.
2. Read the two Saturday route Q-log manifests; distinguish segment duration from events; align all timestamps by `logMonoTime`.
3. Inspect the 171-second event with +/- 15 and +/- 30 seconds of `carState`, `carControl.actuators`, `carOutput.actuators`, `controlsState.lateralControlState`, `liveTorqueParameters`, `selfdriveState`, `onroadEvents`, and any available CAN / sendcan. Confirm units and valid field names using the pinned schema.
4. Produce CSV/JSON per-sample timelines and a readable annotated investigation: commanded/actual steering, speed, steering angle, EPS torque/driver torque, lateral-controller output, saturation flag, route conditions where available, events.
5. Detect episode transitions rather than reporting every repeated event sample as a unique failure. Document missing or invalid sensors and zero data clearly.
6. Compare across multiple conditions/segments and repeat on the 36-minute earlier route. Do not use absence of alerts as proof of safety.
Deliverable: `ram1500-development/reports/2026-11-14-steering-analysis.md` plus analysis script and reproducible instructions. Never add private raw logs or VINs to GitHub.

## Phase 2: Vehicle interface / safety audit
- Identify exact Ram platform support, EPS message addresses and DBC, steering control timing and limits, driver override handling, faults, minimum speed conditions, braking and throttle interfaces and stock ACC interaction.
- Clearly distinguish physically possible low-speed EPS motion (e.g. parking) from openpilot's *authorized* LKAS operating envelope.
- Trace every safety gate, including Panda safety and OEM checks; do not remove, spoof or defeat them.
- Validate presence and consistency of CAN signal data; Q-log sampling may be insufficient for raw-bus diagnosis.
Deliverable: platform capability and constraint matrix, evidence citations to source lines, risks and unanswered questions.

## Phase 3: Offline replay and candidate improvements
- Reconstruct the recorded baseline in reproducible tests; assess lateral tracking error, control effort, steering saturation and disengagement conditions.
- Propose each controller or model change in a separate PR with before/after plots and regression/unit tests. Prefer better tuning and predictions over increased limits.
- Do not deploy to an active vehicle, publish an installer, or claim road readiness based solely on offline logs or simulation.

## Phase 4: Independent validation / later capabilities
- Code review, fault-injection tests, device-build verification, platform-specific safety validation, controlled test protocols, and explicit release gates.
- Explore low-speed assistance and destination navigation only after documenting interfaces, monitoring, localization, intersection handling and failure recovery. These features are long-term research targets, not shipping promises.

## Requested analysis outputs
- Executive summary: what is established, likely explanations, alternatives, confidence levels.
- 171-second saturation event forensic timeline.
- End-of-route CAN/controls-mismatch classification; distinguish normal shutdown transitions from dangerous in-motion issues.
- Software-vs-hardware limiting factors; separate stock vehicle limitations from Sunnypilot controller behavior.
- Prioritized, smallest safe changes for offline experiments, with test plans.
- Table of unknowns, required further logs, and evidence for each claim.

## Cross-review status (ChatGPT ↔ Claude)
Ledger: `ram1500-development/REVIEW_LOG.md` (one row per claim; replies appended, never overwritten).
Claude round 1 (2026-11-17, branch `claude/ram1500-review-2026-11-17`): report `reports/2026-11-17-claude-independent-review.md`. Logs were not read (access blocked); findings are source-grounded at `1f7233cb`.

| Phase 1 item | Status after Claude round 1 |
|---|---|
| 1. Schema / commit | Done: pinned to `1f7233cb`, five files verified by git blob SHA-1 (`ramtools/schema.py`). |
| 2. Manifests, `logMonoTime` alignment | Tooling done (`investigate_route.py`); run on data pending. |
| 3. ±15 / ±30 s around 171 s | Tooling done (`steerSaturated_<n>_pm15s.csv`, `_pm30s.csv`, mechanism A/B classification); run pending. |
| 4. CSV/JSON timelines | Done (per-row cross-service skew, units sidecar). |
| 5. Episodes vs samples | Done (`episodes.csv`, exact durations from on-change messages). |
| 6. Compare segments / earlier route | Pending data. |

Next three steps (owner: whoever has log access): (1) run `investigate_route.py` on the 51 qlogs; (2) fetch rlogs for segments 0, 2 and the last two, rerun; (3) offline lateral-accel-per-torque fit vs the 2.0 placeholder (experiment E1). No controls change is proposed in this round.

## Working rules
Keep baseline and changes separately versioned, no blind vehicle actuation, and no raw identifying driving data in public commits. Do not mistake route summaries for validated engineering diagnoses. Record command and observed actuator output together before reaching conclusions about steering torque limits.
