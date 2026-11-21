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

## Timeout sound in steering-only mode (sunnypilot #1717 backport)

If the driving software stops responding, the "stopped responding" warning sounded only when cruise was engaged. RamPilot also sounds it when steering-only mode was engaged, as newer sunnypilot does (538e1e8a). No full-rate recorded drive showed a gap of over 1 s in the driving state while engaged. Tests: `selfdrive/ui/tests/test_rampilot_timeout_sound.py`, plus upstream's messaging test in `test_soundd.py`.

## Blind-spot startup

Blind-spot support turns on only if the blind-spot message (BSM_1, address 720) is seen during the startup fingerprint, which listens for about a second. On this truck BSM_1 arrives every 0.5 s, and on one recorded start its first frame came 0.31 s after the window closed. Blind-spot support then stayed off for the whole drive, so sunnypilot could start a lane change with a vehicle in the blind spot. The truck's own blind-spot lights kept working.

RamPilot keeps listening for up to 3 s more, only on `RAM_1500_5TH_GEN` and only when BSM_1 is missing, before the car settings are built (`opendbc/car/chrysler/rampilot_bsm.py`, called from `car_helpers.get_car`). Once enabled, the stock checks apply: if BSM_1 stops, CAN validity fails as before. If BSM_1 never arrives, blind-spot support stays off as before and a log line records it.

Replaying the logged CAN after each recorded start: the missed start would have enabled blind-spot support after 0.31 s. The 19 other logged starts were unchanged and waited 0 s. Tests: `opendbc/car/chrysler/tests/test_rampilot_bsm.py`, including the real `get_car` path with simulated CAN.

## Missing blind-spot data notice

When a `RAM_1500_5TH_GEN` starts without blind-spot data for the Comma, the first 15 seconds include a “Comma Blind-Spot Data Unavailable” notice with a reminder to check mirrors. During a left or right lane-change prompt, the same title appears above the original directional instruction. Both displays are silent and give way to higher-priority alerts. The notice has no role in engagement decisions. RamPilot's schema names the recorded event `rampilotBlindSpotUnavailable` (19) in `cereal/custom.capnp`. Older readers see an unknown event number. Coverage is in `selfdrive/selfdrived/tests/test_rampilot_bsm_alert.py`.

## No calibration learning in reverse (openpilot #38573 backport)

The steering-calibration learner (`paramsd`) no longer updates steer ratio, stiffness or offset while the truck is in reverse, as in newer openpilot (612d97cf). On the recorded drives it had learned for 14.8 s in reverse across 35 reverse maneuvers, and the estimates moved little. Tests: `selfdrive/locationd/test/test_rampilot_paramsd_reverse.py`.

## Smaller backports

- **Chrysler checksum** (opendbc #3782): a table lookup gives the same checksum as the former bit loop. On 400,000 recorded frames it matched every one and was about 14 times faster per frame. Tests: `opendbc/car/chrysler/tests/test_rampilot_checksum.py`.
- **Fixed steering-delay setting** (sunnypilot #1906): with live delay learning off, controlsd now uses the fixed delay (steerActuatorDelay plus the software delay) as intended. The swapped branches made that setting have no effect. With learning on, as on this truck today, nothing changes. Tests: `sunnypilot/livedelay/test_rampilot_lat_delay.py`.
- **Loop catch-up** (openpilot #39095): after a stall of more than one frame, the Ratekeeper resyncs instead of running catch-up frames back to back. Tests: `common/tests/test_rampilot_ratekeeper.py`.

## Install and rollback

`rampilot/device.sh` installs RamPilot on the comma three from a git bundle and rolls back to the pinned baseline. Steps: `rampilot/INSTALL.md`. Offline tests: `rampilot/tests/test_device.sh`, plus an install and rollback of the real branch on a simulated device holding only the baseline.

## 3X and four

The comma four is on order. Newer sunnypilot already includes features the comma three build lacks:
- cruise-button speed control
- steering-tune settings
- newer driving models
- map speed limits

RamPilot's changes will be ported to that base after a baseline period on stock. The research notes are in the owner's private project folder. Old-build measurements are re-checked on the new device before reuse.

## Provenance

Changes and their evidence come from the owner's offline analysis of his own drives (2026-11-21). The advisor provides drive monitoring and reporting. Its reviewed source is included on the public `rampilot` branch.
