# November 18 — recorded-build following-distance and lane-change comfort quality gates

**Status:** new read-only analysis metrics and synthetic fixture tests complete. No real-route comfort numbers or vehicle-control improvements are claimed in this report.

## What is now measurable
This branch uses Claude's **actual installed-build schema** pinned to Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; do not substitute the fork's newer schema.

- `ramtools/comfort.py`: scalar cruise-enabled headway, longitudinal acceleration and jerk, basic lane-change-state steering rate.
- **New:** `ramtools/comfort_episodes.py`: separates sustained following episodes from isolated lead detections and applies an as-of carState freshness ceiling (150 ms), requiring positive plausible lead distance, speed > 2 m/s, cruise enabled, **known driver gas/brake both false**, and valid source messages. Records *why* observations were excluded. Reports observed gaps in seconds and a simple closing-speed `dRel / (-vRel)` diagnostic only when the lead is closing by more than 0.5 m/s. These are **not factory ACC targets and not collision-risk predictions**.
- **New:** lane-change state transition analysis isolates `laneChangeStarting` windows ending in a different model state; excludes incomplete windows at the end of available logs, insufficient steering samples, or missing steering-rate measurements. Aggregates peak wheel steering rate and the finite-difference steering-rate change in degree/s². It is **not lateral jerk** and should not be used alone to change the model's lane-line fade.
- `investigate_comfort.py` exports both a summary and a separate `comfortEpisodes` field in the private local JSON file. Private derived output cannot be written inside the GitHub repository.
- `tests/test_comfort_episodes.py`: checks exclusion of pedal-pressed and stale/future data, invalid lead, censored lane changes, and basic steering-rate derivatives; CI runs it alongside existing comfort and torque-cap fixtures.

## Strong attribution limitations
At installed-build `1f7233cb`, the Ram controller sends **LKAS torque and cruise cancel/resume**. It does not command direct throttle/braking; newer Intelligent Cruise Button Management is not present in that recorded controller source. Thus the derived acceleration/jerk measurements characterize **observed truck behavior**, not a Sunnypilot longitudinal PID, and any vehicle manufacturer ACC set speed/gap should be treated separately.

A `radarState.leadOne` observation may be model-derived because the Ram interface reports radar parsing unavailable. This analysis explicitly includes model-vs-radar source fractions; if no sufficiently credible lead observations are available, report **insufficient data**, not a target gap.

## Proposed offline acceptance protocol before any change
1. Run the exact-schema decoder on several **private R-log segments** covering sustained following, closing traffic, and multiple lane changes. Include a larger route window, not only the seven-second steering saturation cluster.
2. Require enough qualifying following episodes, matched and validated lead sources, and no driver pedal inputs. Compare median and 10th-percentile observed headway *within similar speed and closing-rate bands*, not a single global number.
3. For lane changes, stratify by speed, direction, road curvature, lane-line confidence and driver steering intervention. Compare entry, mid-phase, exit/recenter to distinguish overshoot/oscillation from driver corrections. The current metric only covers `laneChangeStarting`, so it does not yet establish full maneuver smoothness.
4. Design prospective A/B **offline replay** tests for any proposed controller/planner adjustment: lower high-frequency steering-rate changes **without losing lane tracking, clearance, driver override or alert behavior**. Avoid unvalidated tighter following.
5. Run independent review, hardware simulation and controlled safety acceptance before any road-use release.

## Current blockers and known progress
- The separate high-resolution steering replay on the November 17 saturation cluster demonstrates a measured source-level clipping explanation but **cannot determine the controller's exact execution phase**; phase ambiguity was independently reported in `ram1500-steering-forensics-20261117/reports/2026-11-18-steering-replay-phase-identifiability.md`. Do not use its 22 mismatches as an argument to retune limits.
- The optimized parked diagnostic sidecar's overhead still requires current hardware retest; its first prototype exceeded the CPU target.
- No new steering, ACC, brake, throttle or Panda safety commands were sent in creating these tools.

**GitHub branch:** `ram1500-pinned-comfort-20261117`. Existing separate diagnostic logger PR #1 and independent Claude review branch must not be overwritten by this work.
