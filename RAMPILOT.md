# RamPilot

RamPilot is Mark Werner's own build of [sunnypilot](https://github.com/sunnypilot/sunnypilot), itself a fork of comma.ai's [openpilot](https://github.com/commaai/openpilot), for his 2021 Ram 1500 Limited EcoDiesel (platform `RAM_1500_5TH_GEN` / RAM_DT).

**This is driver-assistance software. The driver stays responsible and keeps hands on the wheel.**

## Branches

The public comma three branch is `rampilot`. Its sunnypilot 0.10.1 base is `1f7233cb` on `staging-tici`. The restore branch `baseline-2026-11-21-staging-tici` pins the exact build that ran on the truck. The reviewed advisor is published on `rampilot` with live parking logging disabled.

The lane-change work is a local candidate in the `rampilot-tici` checkout. The owner reviews each change before authorizing a separate parked installation.

## Lane-change candidate

Two drivers described a swing into the target lane followed by a correction. The baseline derives its request from the model's planned heading and initial yaw rate. In `drive_helpers.curv_from_psis`, it doubles the average curvature over the look-ahead interval and subtracts the initial yaw rate divided by speed.

The candidate smoother runs in `controlsd.py` before stock curvature clipping. It limits the change per control tick in the requested curvature using a lateral jerk setting of 0.5 m/s³. It activates at or above 9 m/s during the model's starting and finishing states and has a one-second tail. The first tick below 9 m/s clears that tail and passes through the stock request.

**Offline review.** The original 38-event comparison used a recorded request sampled at 50 Hz after stock clipping. The live smoother receives the model's pre-clip request at 100 Hz. The owner's shared review handoff tracks the replay using the live hook's input and cadence. Open-loop comparisons measure requested curvature and its timing. Vehicle response, lane completion, driver comfort and warning behavior under the candidate remain unmeasured.

The threshold and tail regression tests run offline in `selfdrive/controls/lib/tests/test_rampilot_lane_change.py`.

## Steering-limit flag (sunnypilot #1716 backport)

The installed build updates `steer_limited_by_safety` only while cruise is engaged. In steering-only mode the flag froze at its last value, which holds the steering-limit warning timer and the torque integrator. RamPilot updates it whenever steering is active, as newer sunnypilot does (7a43e2cb). It reuses the lateral-active value from `state_control()` instead of a second `get_lat_active()` call, because newer sunnypilot's blinker-pause timer counts down on each call.

A replay of the recorded drives found no warning the fix would have added. On this truck the flag is on most of the time even when fully engaged, because the torque sent trails the request by more than its 1% threshold. Tests: `selfdrive/controls/lib/tests/test_rampilot_steer_limited.py`.

## 3X and four

The comma four is on order. Newer sunnypilot already includes features the comma three build lacks:
- cruise-button speed control
- steering-tune settings
- newer driving models
- map speed limits

RamPilot's changes will be ported to that base after a baseline period on stock. The research notes are in the owner's private project folder. Old-build measurements are re-checked on the new device before reuse.

## Provenance

Changes and their evidence come from the owner's offline analysis of his own drives (2026-11-21). The advisor provides drive monitoring and reporting. Its reviewed source is included on the public `rampilot` branch.
