# Lane-change entry and finishing-phase comfort audit — November 18, 2026

**Status: read-only analysis code and synthetic tests; real-drive numerical lane-change results not yet measured. This is not a control or installer change.**

## Source and execution
- Development remains separate from the protected baseline branch `ram1500-custom-development`, the steering/private-logging work in draft PR #1, and Claude's independent-review branch. This work is in draft PR #2.
- `ramtools/lane_phase_quality.py` consumes the preexisting whitelist-only records from `ramtools/logio.py`, decoded with the recorded-build `1f7233cb` schema by `investigate_comfort.py`. It does not read VIN, location, raw CAN, or camera payloads and emits only counts, speed/duration percentiles, and steering-wheel-rate statistics.
- `investigate_comfort.py` now includes an additive `laneModelPhases` summary, so existing `comfortEpisodes` consumers are unchanged. Private output stays outside the public git repository.
- `tests/test_lane_phase_quality.py` uses synthetic messages only. GitHub Actions' pinned comfort workflow explicitly executes these tests.

## Required interpretive boundaries
- `laneChangeStarting` is a **model entry/crossing-phase proxy**; `laneChangeFinishing` is a **model recentering proxy**. Neither verifies actual lane boundary crossing or stable final vehicle position.
- The observed `steeringRateDeg` derivative has units degrees/s² (steering-wheel angular acceleration), **not** chassis lateral jerk. Direct inertial and lane-geometry confirmation remain outstanding.
- A phase must transition out to qualify; invalid/delayed model messages censor it. Missing/stale car-state coverage, driver steering intervention or unknown intervention status, bad kinematics, too few samples, and low speed reject its comfort statistics.
- Starting and finishing are measured separately. Report `physicalLaneBoundaryCrossing=not_observable_from_logged_model_state`; do not interpolate crossing time.
- No normalized torque replay choice determines controller scheduling phase. Do not infer causation from previously ambiguous rate-limited replay matches.

## Offline next steps
1. Run the pinned decoder on private, ordered R-log segments with **full maneuver coverage**, rather than relying only on the seven-second steering-cap window. Verify actual `initData.gitCommit` and segment continuity. Retain private logs outside this repository.
2. Count qualified starting and finishing intervals and rejection reasons before comparing percentiles. Stratify by speed, direction, driver override, and sampling completeness; do not overinterpret sparse windows.
3. To measure physical crossing and chassis lateral jerk, acquire and independently validate synchronized lane geometry, pose and inertial streams; model state alone is insufficient.
4. Before proposing a tuning change, require replay plus independent safety review, hardware tests, and OEM/Panda-gate preservation. No on-device changes or installer addresses are authorized.

## Validation
Six local offline synthetic tests passed before commit. CI must be checked at the exact latest PR #2 commit; results for older heads do not validate later edits.
