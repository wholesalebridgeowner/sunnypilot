# Ram 1500 — Following distance, acceleration and lane-change comfort audit
Date: 2026-11-17
Status: **source audit complete; real-route numeric comfort metrics not yet run and verified**.
Scope: Saturday Nov 14 evening route `000003cc--27a46b2116`, 51 Q-log segments (~50 minutes), and pinned vehicle platform code. **Offline-only; no live driving modifications.**

## Critical architecture finding
The inspected `RAM_1500_5TH_GEN` interface appears to use **stock PCM adaptive cruise control for longitudinal vehicle actuation**, not openpilot direct brake/accelerator control. Evidence:
- `opendbc/car/chrysler/carcontroller.py`: constructs LKAS commands and allowed cruise-control button messages (cancel, resume, and adaptive buttons in ICBM), but no direct throttle or brake message.
- `opendbc/car/chrysler/carstate.py`: reads `DAS_3.ACC_ACTIVE`, `DAS_4.ACC_SET_SPEED_KPH`, cruise standstill, pedal state, EPS and speed.
- `opendbc/car/chrysler/interface.py`: sets `radarUnavailable=True`, commenting that radar parser is not supported in the car DBC.
- `opendbc/sunnypilot/car/chrysler/icbm.py`: can request cruise-control speed-up/down button presses. This may influence factory ACC set speed but is not direct throttle actuation.
- `selfdrive/controls/lib/longitudinal_planner.py` still computes `longitudinalPlan`; such a plan existing in Q-logs does not prove the vehicle executes its acceleration request. Check actual `carControl.longActive` and `CarParams.openpilotLongitudinalControl` in recorded logs before drawing a per-drive conclusion.

**Engineering consequence:** Distance and throttle smoothness are not purely software PID-tuning tasks on this particular integration. We can objectively measure speed, headway, driver interventions and changes in the factory cruise set speed, then identify what is controlled by stock ACC versus Sunnypilot.

## Read-only telemetry signals now supported
`ram1500-development/analyze_comfort.py` exports:
- Vehicle speed `carState.vEgo` (m/s), acceleration `aEgo` (m/s²), driver gas/brake flags, cruise availability/enabled/set speed, steering angle and steering rate.
- `carControl.longActive`, `latActive` and requested acceleration/torque alongside `carOutput` actual controller-output fields.
- `radarState.leadOne` validity, relative distance `dRel` (m), relative speed `vRel` (m/s), lead radar source flag. Model-derived lead is possible: do not claim true radar-based following measurement.
- `longitudinalPlan` source, desired acceleration, predicted speed and jerk trajectories; explicitly do **not** equate these with actuation if `longActive` is false.
- `drivingModelData.meta.laneChangeState` and direction, lane-line geometry/confidence, steering-rate samples, each `laneChangeStarting` transition duration.
- Time gap estimate `dRel/vEgo` only for a positive, valid matched lead and matched `carState` within 0.35 s when speed exceeds 2 m/s. It is an observed time gap, not the factory ACC target.
- Median and p90 *absolute* observed longitudinal jerk from finite differences of `aEgo` sampled 0.03–0.3 s apart; a noisy estimate requiring filtering/validation.
- Number of valid observations and blanks when fields are unavailable; no fabricated metrics.

## Lane change path and hypotheses
From `selfdrive/controls/lib/desire_helper.py`:
- Minimum lane-change initiation speed is `20 mph` in the inspected source (not inferred from any specific drive).
- Lane-line probability is ramped down for 0.5s during `laneChangeStarting`, then up for approximately 1s during `laneChangeFinishing`.
- Pre-lane-change progression depends on driver steering torque or auto-lane-change timer/permission, blindspot state, and brake state.
- `sunnypilot/selfdrive/controls/lib/auto_lane_change.py` provides selectable delay behavior and safeguards.

**Potential comfort issues to test, not established problems:** abrupt trajectory curvature at the beginning/end of a lane change, high steering-rate peak, rapid direction changes, excessive delay and unexpected driver steering intervention. A steering rate or lateral jerk peak should be examined with lane-marking confidence, road curve, speed and driver corrections before changing planner behavior.

## Distance-control and acceleration investigation
Questions the data must answer:
1. How often is factory cruise enabled, and is `carControl.longActive` actually false throughout the representative run?
2. Is the observed lead primarily `radar=true` or model-generated? Is `leadOne.dRel` stable enough for credible headway estimates?
3. What are time-gap distributions during following, and how do they vary with relative lead speed, driver override, set-speed changes, lane changes, and braking?
4. Do observed acceleration and jerk peaks coincide with ACC changing set speed, a lead vehicle slowing, or driver pedal intervention?
5. Does the `intelligentCruiseButtonManagement` inject speed-increase/decrease commands during abrupt speed changes?
6. Where factory ACC owns throttle, how much improvement is actually possible with supported driver-configurable following gaps versus needing a separately validated, authorized longitudinal integration?

## Measurements still needed
No measured Saturday-drive headway percentile, acceleration jerk percentile, lane-change steering-rate percentile, or per-drive longitudinal-actuation rate has been published yet. The analyzer and fixture tests are available for collection/validation; **do not present placeholder numbers as real measurements**.

## Recommended offline analysis and acceptance criteria
1. Run `analyze_comfort.py` on all 51 ordered local Q-logs with the recorded build's compatible cereal schema. Save summary and CSV outputs privately. Confirm actual `longActive` and cruise states first.
2. Inspect every `laneChangeStarting` interval and +/-5 seconds around it; compare steering rate, normalized steering torque, driver torque and modeled lateral curvature. Identify valid full lane changes, interruptions, and recentering episodes.
3. Stratify following gaps by lead-valid status and traffic speed; exclude stopped traffic, invalid leads, stale synchronization, and driver override intervals.
4. Quantify acceleration/jerk under cruise-enabled driver-pedal-inactive conditions separately from all other conditions. Avoid overclaiming actuator attribution.
5. Build repeatable A/B **offline** evaluation criteria: lower jerk and peak steering rate with no degradation in path tracking, collision/spacing safety, intervention detection or disengagement performance.
6. Do not alter factory gap following logic, throttle/brake actuation, speed authorizations, lane-change safety checks or Panda limits without interface validation and appropriate independent testing.

## Commands
From a matching, provisioned openpilot checkout with Q-logs available privately:
```bash
python3 ram1500-development/analyze_comfort.py /private/sat/*/qlog.zst \
  --output-prefix /tmp/ram-comfort
python3 -m unittest discover -s ram1500-development -p 'test_analyze*.py' -v
```
Output: `/tmp/ram-comfort.json`, `/tmp/ram-comfort-samples.csv`, `/tmp/ram-comfort-headway.csv`.

## Boundaries
This is a source-level audit plus new offline analysis tooling, **not** evidence that factory distance control has been improved, that throttle is directly controlled, or that lane-change smoothness improved. The exact on-device software was a modified/dirty `staging-tici` build; review differences from pinned development code before proposing or testing control changes.
