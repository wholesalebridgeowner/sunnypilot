# Ram Q-log model lane-phase direction and EOF integrity — 2026-11-19

Status: **offline instrumentation correction; NOT a driving comfort result or calibration candidate**.

## Source and scope

- Original recorded Comma source commitment expected: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (historically dirty on device, so source-pin evidence is not a binary rebuild attestation).
- Source work based on read-only draft PR #41 at `020fc03af8bad268fe47666f494b86c162bbae76`. This separate draft PR #42 only changes `ram1500-development/auto_review.py`, its synthetic test file and this report.
- Code commit `a304f02f9f5c05213a199ae3f059b4ecb570eb53`; regression-test commit `ee449877bcedc1b54254a7b000c4ed424c9271e8`.
- Neither Comma/Panda nor the vehicle, the private S3 collector, original development branch, or Claude's concurrent PRs were changed.

## Observed code-level defect and correction

The prior sampled model-phase gate allowed `laneChangeStarting(left)` followed by `laneChangeFinishing(right)` to count as a completed left-side window, as long as lateral-active, health and steering-sample gates passed. It did not revalidate the finish direction. An open `laneChangeStarting` window at the end of the final Q-log segment also vanished from the rejection counters rather than being explicitly marked incomplete.

The corrected observer requires **the finishing model direction to match the starting direction**. Opposite or unknown finishing direction is excluded under `model_direction_conflict`. An unfinished final route window is counted as `incomplete_at_route_end`; repeated calls to `report()` do not increment it, and an interrupted window at a segment boundary is counted only under `segment_boundary_incomplete`. Previously sampled lane-phase metrics are **not comparable**: qualification marker advances from v3 to `model_direction_consistent_start_to_finish_active_healthy_segment_isolated_qlog_v4`.

These conditions are **model metadata consistency checks**. Even a positive window remains a sampled, Q-log-resolution phase transition with fresh Comma lateral-active and CAN/EPS-health evidence. It is not independent proof of lane-marking crossing, recentering, actual commanded steering origin, or improved lane-change comfort.

## Offline verification at the code/test commit

- [Dedicated automated-review GitHub Actions run #44640022269](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44640022269) — **49/49 tests passed**. Synthetic coverage includes conflicting and missing finishing directions, route-end exclusion and report idempotence, and backward-incompatible comparison gating.
- [General Ram analyzer Actions run #44640022046](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44640022046) — **195 tests run; 1 skipped, 0 failed**. Source-pinned EPS status DBC/layout verification step passed.
- These are synthetic tests only. A private replay on real multi-segment Q-/R-log recordings and a review by Claude are **not yet completed** for this code. No change in recorded lane-change counts or comfort is claimed.

## Private worker and blockers

The existing isolated Railway `ram-scheduled-route-review` worker remains pinned to prior source `2377c055e1254498307b45aa471dcbda7814f763`, not this new code. Its latest inspected scheduled result at 2026-11-19 23:36 UTC was `no_new_eligible_routes` with 16 inventory candidates; previously quarantined route data were not reclassified as accepted baselines. Do not publish or retroactively compare any old v3 lane metrics as v4.

Outstanding: held-out lane-centering/steering stability, actual physical crossing/recentering evidence, November 14 +171s high-resolution R-log absence, factory ACC actuator attribution, source-level PAM-to-EPS control ownership, operator-approved parked device resource checks and independent safety review. No steering-delay, torque, throttle, brake, Panda or OEM calibration and no installation are authorized.
