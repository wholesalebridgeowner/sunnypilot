# 2026-11-19 — Private Q-log segment-boundary ownership/comfort isolation (draft PR #40)

**Status:** source-pinned, research-only synthetic regression milestone. Stacked on draft PR #39 without changing the original `ram1500-custom-development` branch, Claude's concurrent steering/PAM work, the live Railway collector/reviewer or any Comma/Panda/OEM/vehicle code. **NOT a road release, tuning approval or real-route validation.**

## Observed research-integrity defect

The scheduled Q-log metrics stream did not retain individual segment boundaries. A `radarState.leadOne` sample at the beginning of the next file could silently borrow a prior file's qualifying `carState` if its monotonic timestamp was no more than 0.35 seconds older. Likewise, observed vehicle `aEgo` derivatives could bridge adjacent files (0.03–0.3-second difference) and a sampled `drivingModelData` lane-change starting phase could terminate as a "completed" window in a different segment. Source-pinned chronological input and a correct past-only timestamp join do **not**, by themselves, prove segment-consistent provenance.

These were demonstrated with **anonymous synthetic fixtures**, not identified as real errors in the owner's recordings. Independent R-log research analyzers use separate segment-consistency gates and their prior measured results are not revised by this patch.

## Fail-closed changes

- Optional `SegmentBoundary` markers carry **no route key, timestamp, file path, VIN, GPS, raw CAN, serialized signal or private data**. The source-pinned scheduled Q-log review and the opt-in bounded *quarantine trial* now emit one before each input segment for metric accumulation. The public streaming function default remains backward-compatible with callers that do not request boundaries.
- On each marker, invalidate the previous vehicle state, lateral-control state, all-driving and factory-ACC acceleration derivative anchors, and any unfinished model-phase lane window. A crossing lane window is rejected as `segment_boundary_incomplete`, not counted as a physical maneuver. Factory CarParams ownership-conflict detection remains route-global.
- The exact installed-build `initData.gitCommit` must still be verified per segment, and the non-monotonic clock rejection is **not** relaxed. A source mismatch or backwards segment timestamp still prevents a saved accepted review. The existing quarantine is never promoted by these resets.
- Only explicitly segmented accumulations advertise comparable qualifications: `factory_acc_pedal_free_can_valid_source_pinned_segment_isolated_qlog_v2` and `model_start_to_finish_fresh_active_healthy_segment_isolated_qlog_v3`. Standalone/unsegmented metrics carry `segment_boundaries_unverified_not_for_comparison`; legacy and new qualifiers are not silently compared.
- `coverage.segmentBoundariesSeen` and `coverage.segmentIsolatedMetrics` permit a safe sanity check without identifying the trip. This affects the provisional Q-log observer, **not** OEM-selected ACC following gap, decoded brake/throttle authority, lane-crossing verification, actuator tuning or physical vehicle safety.

## Validation

- Code/test HEAD: `a4dc0cbb3c3169d88d52be49b22ee05f3eb4cc5f`.
- [Ram reviewer dedicated Actions run #44621846820](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44621846820): **43/43 tests passed**.
- [General Ram analyzer Actions run #44621846779](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44621846779): **189 tests, one skipped, zero failures**. The test scope still includes pinned EPS source verification.
- The first CI attempt at `f510a985ab9d1411aa66d3c4d6d0c27006cb943e` failed one outdated route-comparison fixture after qualification versioning; the fixture was corrected at `5d68f60f9f72ad57eadd931f8ccf5152c8185975`. A further provenance guard was added and retested successfully at the HEAD above.
- New synthetic regressions prove an otherwise false cross-file lead/radar match, a false cross-file observed jerk derivative, a false cross-file lane-model phase, bounded-trial isolation, continued cross-segment clock rejection, and non-comparability without boundaries. No real raw route data was used in fixture tests.

## Independent Railway observation and limits

The existing `ram-scheduled-route-review` remains **live on earlier audited research commit** `2377c055e1254498307b45aa471dcbda7814f763` (deployment `98db7208-c0d1-4529-aeb8-b7d69a66fec9`), not the PR #40 code. Its latest inspected scheduled outcomes were `no_new_eligible_routes` with 16 eligible inventory candidates; earlier multi-segment recordings were quarantined as `segment_boundary_regression`, not accepted or retroactively repaired. A prior isolated single-segment bounded observation carried `releaseAuthorized=false`. **No PR #40 private real-route replay or on-device validation has been executed.**

Remaining blockers include November 14 +171-second event's missing R-log, independent held-out lane tracking/steering reversal and ping-pong acceptance, model-phase versus actual lane crossing, OEM longitudinal actuation attribution, PAM-to-EPS exact authorization, parked operator-approved logger CPU/private-RSS proof, and independent controlled-course safety approval. The 32 mph number is an initial Sunnypilot engage threshold, not a proven sustained steering cutoff. The 14.6-second factory auto-park EPS status is observational OEM behavior and never authorizes a third-party low-speed controller.
