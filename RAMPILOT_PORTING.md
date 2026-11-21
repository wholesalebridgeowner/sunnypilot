# RamPilot: porting to comma 3X / comma four

**Status:** preparation. Nothing is ported yet. The comma four is on order.

## Newer-code review (2026-11-21)

The comma three runs the last sunnypilot build for that device (`staging-tici` is still 1f7233cb). Later sunnypilot releases ship only for the 3X and the four, so improvements reach the comma three only by hand. None of the newer code changes a Panda safety limit for RAM_DT.

Small Python-only fixes that carry back to the comma three, each to be its own reviewed commit:

| # | Upstream change | What it fixes |
|---|---|---|
| A1 | sunnypilot #1716 (7a43e2cb) | `controlsd` updates `steer_limited_by_safety` only while cruise is engaged. In steering-only mode the flag freezes at its last value, which affects the steering-limit warning and the torque integrator. The fix gates it on lateral-active. Verified: the installed build gates on `selfdriveState.active`, and newer sunnypilot gates on `get_lat_active`, which the installed build already has. Built in `rampilot-tici` (reuses the value from `state_control()`); the recorded-drive replay found no added warning on this truck. |
| A2 | sunnypilot #1717 (538e1e8a) | The "stopped responding" sound also covers steering-only mode. Built in `rampilot-tici` with upstream's test and an offline test. |
| A3 | opendbc #3782 (057aee25) | Faster Chrysler checksum, same result. |
| A4 | openpilot #38573 (612d97cf) | No steer-ratio or offset learning while reversing. |
| A5 | openpilot lagd series | Steadier learned steering delay. It changes lane-change entry too, so the old and new estimators are compared over recorded drives first. |
| A6 | sunnypilot #1906 (53e13a7b) | Fixed steering-delay setting works when live learning is off. |
| A7 | openpilot #39095 (fe7ccf95) | Clean catch-up after a control-loop stall. |

Larger or newer-hardware items: the steering controller rewrite, Blendful lane centering, cruise-button speed control (needs a Panda safety change), and newer driving models. The WD40 model's runtime path was removed from newer sunnypilot, so WD40 does not carry forward unchanged.

Newer code adds nothing for the blind-spot startup miss or for lane-change smoothness. RamPilot's own blind-spot startup wait (`opendbc/car/chrysler/rampilot_bsm.py`) carries over as long as newer opendbc still sets `enableBsm` from the startup fingerprint; check that before porting.

## What is verified so far (2026-11-21)

**Lane-change smoother (`rampilot_lane_change.py`).** Newer sunnypilot (0.11.1, April 2026 checkout) has the same structure:
- `drive_helpers.curv_from_psis` still computes `2 * psi / (v * t) - psi_rate / v`
- `MAX_LATERAL_JERK` is still 5.0
- `controlsd` still computes `new_desired_curvature` right before `clip_curvature`

One difference: a `lateralManeuverPlan` source can replace `model_v2.action.desiredCurvature`. The smoother hooks in at the same place, after either source is chosen and before `clip_curvature`.

## Port plan

1. Run stock sunnypilot on the comma four for a baseline period. Re-measure on the new build and model:
   - steering-limit warnings
   - steering losses
   - lane confidence
   - lane-change force
   - the integrator "frozen by rate limit" share

   Old-build numbers are not reused without re-checking.
2. Create `rampilot` from the chosen sunnypilot release commit (pinned like the comma three baseline).
3. Re-apply each `rampilot-tici` change as its own commit, with its tests. Re-run its offline replay on comma-four recordings before any install.
4. Same rules as the comma three: no change to Panda safety, torque/rate limits, driver monitoring, override or disengagement. Each install needs the owner's go, with the truck parked, and a tested way back.
