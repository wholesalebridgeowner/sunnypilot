# Exact installed-build cross-review: longitudinal ownership and lane-change behavior

Updated 2026-11-17. Source evidence pinned to recorded `sunnypilot/sunnypilot@1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (`staging-tici`), **not** this fork's newer master. The runtime reported a dirty tree, so these source findings still need comparison with installed local modifications.

## Why this matters

The original reports in this development branch inspected later `opendbc_repo@df807f8` code. Claude's independent review found **no common Git history** between the recorded drive build and this fork's current master. The available event counts are valid as observations, but later-build controller logic should not be used to explain a 2026-11-14 route without reconciling actual running files.

Primary independent review (not yet merged into this development branch):
- Branch: `claude/ram1500-review-2026-11-17`
- `ram1500-development/reports/2026-11-17-claude-independent-review.md`
- 31 offline tests and pinned-schema tools under `ram1500-development/ramtools/`
- Claude reported it did **not** obtain private Q-/R-logs; no numerical root cause proved.

## Specific correction to longitudinal / throttle hypotheses

At the actual build `1f7233cb`:
- `opendbc_repo/opendbc/car/chrysler/carcontroller.py` performs LKAS and basic cruise `cancel` / `resume`; it **does not import or call** `IntelligentCruiseButtonManagementInterface` used in newer source.
- The actual Ram controller implementation does **not** send direct brake or accelerator commands; factory ACC remains the primary longitudinal actuator on this platform.
- `selfdrive/controls/lib/longitudinal_planner.py` computes acceleration targets, desired speed and jerk. A computed `longitudinalPlan` does not prove those outputs were actuated by openpilot.
- To establish longitudinal ownership on the drive, decode `carParams.openpilotLongitudinalControl`, `carControl.longActive`, `carState.cruiseState.enabled` and check CAN/sendcan messages in the exact build.
- `radarState.leadOne` may be model-derived because the Ram port sets `radarUnavailable=True`. Validate provenance; do not call an inferred headway estimate a measured stock ACC gap target.
- Do **not** attribute speed variations on the recorded run to newer ICBM cruise-speed button injection. The installed build may have other user modifications (dirty tree), which must be inspected before excluding all set-speed modifications.

## Lane-change source confirmed against recorded build

`selfdrive/controls/lib/desire_helper.py` at `1f7233cb`:
- Minimum initiation speed is 20 mph.
- `laneChangeStarting` fades lane-line influence from 1 to 0 over 0.5 seconds.
- `laneChangeFinishing` restores influence to 1 over approximately 1 second.
- Lane-change initiation requires user steering torque or permitted automatic lane-change state; blind-spot detection suppresses progression.
- `sunnypilot/selfdrive/controls/lib/auto_lane_change.py` includes timers and checks for brake, prior lane change, blindspot and user toggle.

The fade rates are **planner design settings**, not a measured lateral jerk or physical lane-change duration. Changes to them should be proposed only after route replay and unit tests: quantify lane-change entry peak steering rate, lane-crossing transition, exit recentering, oscillation/corrections, and lane-marking confidence.

## Steering insight from Claude to validate with Q-logs

At the actual build, the Ram `latAccelFactor=2.0` and `friction=0.05` are provisional, and `liveTorqueParameters` were not applied to Chrysler steering by that version's allow-list. Claude's source audit suggests the +171s `steerSaturated` alert could represent (A) requested controller torque reaching the 350-command-unit limit **without car-side limitation**, or (B) a curvature/jerk clip. The algorithm alone does not prove which path happened.

Use the pinned-schema `ramtools/investigate_route.py` from Claude's branch to extract the whole 51-segment route and classify the +171s event. Match the requested normalized torque, applied CAN torque, EPS motor feedback, vehicle speed, steering press and lateral plan. Only then select a tuning experiment.

## Testable comfort analysis plan

1. Validate provenance: installed commit, `git status`/diff, EPS firmware, `carParams` and actual longitudinal flags.
2. Run both independent decoders (current diagnostics script and Claude's pinned-schema `ramtools`) separately; avoid importing mismatched Cap'n Proto schemas into the same process.
3. From private Q-logs, calculate lead-headway distributions only where `radarState.leadOne.status` is true, ego speed > 2 m/s, lead distance > 0 and matched `carState` is fresh; keep model vs radar provenance, cruise enabled and driver pedals separate.
4. Quantify longitudinal jerk from filtered speed/accel with sample-rate and confidence handling. Separate driving with human gas/brake inputs from factory cruise enabled and driver pedals inactive.
5. For each lane-change-start / finish interval, correlate `drivingModelData.meta`, `carState.steeringRateDeg`, `controlsState` and steering-torque requests. Flag short/aborted changes separately.
6. Derive candidate changes with offline replay, comparable conditions and no regression in lane tracking, headway safety or overrides. **No live actuation patch is proposed.**

## Explicit open evidence

No numeric, route-specific headway, throttle/ACC response, jerk distribution, lane-change lateral jerk or +171s torque attribution has yet been verified on private Q-/R-logs. Do not label this source review an experimental driving improvement.

Relevant source references are under the drive-build commit and Claude's independent report; the newer code inspected in the other `2026-11-14` reports describes different software.
