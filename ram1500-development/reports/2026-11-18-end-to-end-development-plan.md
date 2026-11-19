# Ram 1500 assisted-driving development — complete staged engineering plan (2026-11-18)

**Owner instruction:** proceed through the steps autonomously, testing and validating each, and deliver a consolidated completion report rather than asking to continue after each step. **Engineering limitation:** some safety-critical release steps require a verified parked device, independent review and supervised hardware/vehicle validation. Those steps cannot be represented as complete merely because source-code tests pass. No active ECU/steering/brake changes may occur during driving.

## Release definitions

- **Offline-analysis complete** means private logs audited, exact source/version verified, tests passing, measurements documented, proposed values not yet written to car.
- **Calibration candidate ready for independent review** means multiple independent good-quality routes, a baseline and held-out replay comparison for each proposed numerical parameter, explicit ping-pong/overshoot/steering torque safety scorecards and no failed regressions.
- **Vehicle-release ready** requires additionally owner approval for parked install, source-exact on-device build and rollback plan, Panda/OEM security and driver-override acceptance tests, supervised closed-course validation and independent peer review. No such release has yet passed.

## Sequential work plan with acceptance tests

| Step | Development work | Validation / exit criteria | Vehicle action |
|---|---|---|---|
| 1 | Snapshot installed build, EPS firmware, CarParams, safety configuration and existing reports; keep an unmodified baseline | Source version `1f7233cb`, EPS `68466110AB`, torque/lateral and factory ACC authority corroborated | None |
| 2 | Inventory private Comma routes and finished R-log segments, distinguish route times from owner's approximate return-home time | One unambiguous route; contiguous completed segments; private source provenance and coverage counts | None |
| 3 | Independently decode factory Ram ACC status from exact installed source: camera bus-2 `DAS_3` CAN `0x99`, `ACC_ACTIVE`, compare with `carState.cruiseState.enabled` | Exact DBC bits verified, source pin checked, no stale/future joins, quantify disagreements and missing data | None |
| 4 | Analyze Comma `carControl.latActive`, `selfdriveState.enabled`, speed, and OEM cruise separately across **whole route**, not just cherry-picked windows | Per-portion and full-route counts; don't infer a full day or a faulty decoder from four short cruise-off windows | None |
| 5 | Measure steering control behavior only on genuine active and healthy segments, excluding driver takeover, OEM auto-park, CAN faults, EPS torque/rate limiting, invalid and stale events | Segmented route-level exposure; no false zero values used as calibration measurements | None |
| 6 | Identify candidate `latAccelFactor`, friction, actuator response delay and lane-change tuning from actual qualified data; include possible controller damping / lag design rationale | Minimum multiple independent drives; fit and evaluate **held-out route**; track lane-centering error, oscillatory steering reversals, frequency/energy, overshoot, alerts, interventions and torque clipping; reject speed-only improvement | None |
| 7 | Analyze factory ACC separately: actual following gap and observed acceleration/jerk; don't treat it as software-controlled throttle/brake | Credible lead source, factory cruise enabled, segment-consistent radar provenance, no driver pedal, sufficient independent episodes | None |
| 8 | Offline source-pinned replay each trial vs baseline, run synthetic, integration and regression suite and independent Claude review | CI success, security/static safety review, stable versus baseline on held-out drives; fail closed on ambiguous controller phase | None |
| 9 | Perform optional *parked* device validation only when owner approves; verify device logs, CPU/private RSS, msgq reader headroom and clean running tree; no realtime sidecars while moving | Approved maintenance window, no evicted services, CPU and memory thresholds, observed source consistency, reliable rollback | Parked and approved only |
| 10 | Release candidate and supervised controlled-area vehicle safety checks only after owner explicitly approves a proposed install | Correct CarParams/version, Panda safety, explicit driver override, fail-safe, alert handling, low-speed EPS authorization, measurable stability benefits and rollback | Not authorized until gates pass |
| 11 | Keep hourly GitHub/Railway workflow moving across remaining independently verifiable steps until all gates pass or evidence is exhausted | Commits, CI, private replay and scoped reports; never call an untested calibration production-ready | None unless separately approved |

### Recorded source facts for step 3

The actual recorded Sunnypilot fork `1f7233cb` uses `cp_cam=can_parsers[Bus.cam]` for RAM vehicles, with camera CAN parser on **bus 2**. `ret.cruiseState.enabled = cp_cruise.vl["DAS_3"]["ACC_ACTIVE"] == 1`; this is distinct from `carControl.latActive`. The installed DBC maps `DAS_3` to 153 (`0x99`) and `ACC_ACTIVE` to start bit `21|1@0+`, `ACC_AVAILABLE` to `20|1@0+`. The private analyzer compares these original bit values to the parsed CarState status, not to an independent sensor. Even full agreement with raw CAN cannot prove the driver used factory cruise on a different unrecorded trip.

### Parameter proposal safety constraints

The seven areas in PR #2 are intentionally `OFFLINE_PROPOSAL_NOT_INSTALLABLE`; no non-null new parameter values are permitted in a vehicle-writable file. The observed steering delay discrepancy (configured 0.20s vs learned ~0.32s) is **not** justification to shorten or lengthen delay on-road. We explicitly account for the owner's concern that steering delay/filtering could reduce lane ping-pong. Lower delay is not intrinsically better if it worsens stability. Preserve torque ceiling, rate/feedback limits, driver takeover and OEM/Panda safety.

### Work logs

- PR #1: factory parking EPS status, 14.6s OEM control episode, no Comma torque, steering/torque replay and PAM/EPS source evidence.
- PR #2: candidate calibration manifest, offline actuator-delay sensitivity grid, strict noninstallable validator.
- PRs #3–#12: source-pinned private comfort and lane-phase quality improvements.
- PRs #13–#17: private log acquisition/sidecar integrity gates.
- **This branch / draft PR #18:** complete-route raw camera ACC-state and parsed cruise/lateral-state audit, dedicated private one-shot Railway service and synthetic tests.

**Deliver a consolidated status only after running all currently accessible offline steps. Clearly identify blocked hardware/owner approval gates; do not claim that the overall on-road release plan is finished if they are outstanding.**


## Executed steps and validated exit gates — end-of-session audit

The following are measured outcomes, **not assumed progress**:

| Step | Outcome | Evidence / remaining blocker |
|---|---|---|
| 1: Exact installed source baseline | **Offline evidence verified** | `1f7233cb`, EPS `68466110AB`, factory longitudinal authority, installed 0.20-second delay and proposed hypotheses; dirty working tree and installed executable binary not independently rebuilt |
| 2: Private log inventory and acquisition | **Scoped completed** | Full 54/54 afternoon R-logs, and 20/20 still-present late-morning R-logs privately recovered by authorized Wi-Fi upload; early-morning 0–26 and the later return-home time remain unverified gaps |
| 3: Raw factory ACC source verification | **Verified** | Installed RAM `carstate.py` uses bus2 `DAS_3` 0x99 ACC_ACTIVE; 322,816 afternoon and 114,011 morning fresh raw/parsed comparisons had **zero discrepancies** |
| 4: Morning versus afternoon Comma states | **Verified for scoped recordings** | Morning 10,861 cruise-enabled and 10,764 lateral-active carState-aligned observations; afternoon 54-minute route 0/322,896 cruise enabled and 0 lateral-active. Do not conflate with whole day |
| 5: Steering oscillation/limit eligibility | **Instrumented, limited** | 20-morning-segment steering reversal proxy identified 1,903 clean eligible samples / 18.859 seconds, 0 reversals under strict gates; **not enough to prove no ping-pong or optimize response** |
| 6: New numeric steering calibration | **Blocked by evidence/release gate** | Five noninstallable delay hypotheses 0.20/0.23/0.26/0.29/0.32 s; no independently validated latAccelFactor, friction, damping, lane tracking or low-speed authorization value. Cannot responsibly write one |
| 7: Factory ACC observational comfort | **Partially measured** | Four-morning-segment pinned replay: 6 valid following episodes, 86.444 seconds, median sampled headway 2.259s. Factory selected gap and throttle/brake commands **not decoded** |
| 8: Regression / source review | **Fixture tests passed; control candidate review incomplete** | Final private source-pinned Docker image: **102/102 tests passed**, original installed CarState/DBC/RAM generator exact SHA blobs verified, private morning replay SUCCESS; independent Claude review already corroborated afternoon route. Not a validated controller replay or install approval |
| 9: Parked device hardware/CPU validation | **Not run; explicit approval still required** | Existing Comma msgq subscriber slots and first prototype's excessive CPU/RSS risk; vehicle not disturbed |
| 10: Steering/ACC vehicle release | **Not authorized, NOT complete** | OEM/Panda safety, approved build and rollback, independent controller review, supervised vehicle tests remain mandatory; no write/install attempted |
| 11: Continued orchestration | **Active** | Existing hourly `Ram Driving Software Development` automation updated with full plan, positive morning ACC evidence, owner ping-pong concern and no-unsafe-write constraints. GitHub PR #18 remains draft |

**Correct completion statement:** The authorized offline collection, source reconciliation, fixture testing and initial stability instrumenting have been completed. The *overall* requested on-road calibration-and-release plan **has not been completed** because the evidence, independent review, parked hardware and supervised vehicle validation gates are unfulfilled. Do not silently equate a green CI or one-shot private replay with an improved driving parameter or a completed vehicle release.

### Contiguous privacy and release safeguards

The exact evening route ID, dongle key, map coordinates, VIN, raw CAN payloads, device access credentials and original log files remain in private Comma/Railway systems and were never placed in this repository. No Comma model or EPS firmware write, Panda safety change, onroad sidecar, CAN injection or public installer was performed. The existing cloud upload API was invoked only to recover **previously completed** on-device R-log files over Wi-Fi while the vehicle was reportedly home.
