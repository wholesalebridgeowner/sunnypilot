# 2026-11-19 — bounded retained 20-segment morning factory ACC and lane-model audit

**Status: VERIFIED OFFLINE OBSERVATION ONLY. NOT A CONTROL CALIBRATION OR VEHICLE RELEASE.** This is a partial, retained earlier-morning route, not a complete day or the separate 54-segment later-afternoon cruise-inactive route. Its earlier segments 0..26 were not available and are not inferred.

## Why this was needed

The source-pinned ACC comfort reader had a hard four-segment maximum and previously audited only the first four of the *20 retained* morning R-logs. That restriction left open whether the six existing following episodes and zero qualified lane-change phase windows were merely a four-segment sampling artifact. The new bounded selection accepts at most **20** explicit, nonduplicate, nonnegative segment indices (all <=1000), requires every selected object to be present from one unambiguous private route, and independently verifies a complete exact 40-character recorded-source header in each decoded segment before releasing fixed aggregate summaries. Source/output remains private; the GitHub change contains no raw frames, device IDs, VIN, GPS, credentials, route prefix or per-sample times.

No additional early R-logs were retrieved, no active truck software or device diagnostics run, and no vehicle actuator, Panda or OEM safety behavior was changed. The existing isolated private Railway research service was repinned to an exact GitHub commit and its selection limited to the already retained twenty R-logs; production collection was not changed.

## Reproducible source and validation

- New 20-segment bounded selector / source gate, first source commit `f2f5704bbbdf43f592dbe826e455cdb03ac91920`.
- Four synthetic test functions for twenty-item selection/order, 21-item and oversize failure, missing selected private object, and absence of one of twenty source headers: `3d4c4a36603649a16cdf859a74c789e7b7cd3447`.
- Standalone offline CI workflow: `303dd3a3ac502cea5a1af3a4e127a0149eae2836`.
- First private Railway Docker build at `303dd3a3` **FAILED**: 160 passed / 1 failed because an obsolete four-item rejection fixture still expected five selected segments to be invalid. The fixture was corrected, **without weakening bounds**, in `1883af2413902b943e89f85ba37cdc3a0077c4b1`.
- Successful isolated private Railway deployment **`c5fc0c4e-2d60-4838-b6f7-e0b84facb355`**, pinned to exact source/test commit **`1883af2413902b943e89f85ba37cdc3a0077c4b1`**, **SUCCESS**; Docker build **161/161 pytest tests passed** and subsequent private runtime emitted a complete quality-gated aggregate. A completed GitHub Actions result was not independently confirmed; the private Docker pass must not be called a GitHub CI pass.

## Private source-pinned results

| Quality or observational measurement | Retained 20-segment run |
|---|---:|
| Selected, fully decoded retained R-logs | **20 / 20** |
| Complete exact recorded-source headers | **20 / 20** |
| Decoded messages / truncated R-logs | **2,005,758 / 0** |
| Vehicle-state observations processed by motion observer | **114,076** |
| Radar-state observations / model samples | **22,815 / 22,814** |
| Qualified factory-ACC-conditional following episodes | **6** |
| Qualified following exposure | **86.444 seconds** |
| Valid cruise + lead following observations | **1,735** |
| Sampled *observed* median / P10 time gap | **2.259 s / 1.543 s** |
| Median episode-median gap | **2.397 s** |
| Same-timestamp conflicting radar rejection count | **0** |
| Invalid radar messages rejected in following observer | **7** |
| Qualified acceleration-motion episodes / samples | **6 / 8,680** |
| Motion exposure (different eligibility from gap observer) | **86.741 seconds** |
| Median observed carState.aEgo | **+0.100 m/s²** |
| P90 absolute observed acceleration | **1.163 m/s²** |
| P90 half-second-windowed acceleration-rate proxy | **0.563 m/s³** |
| Qualified model phase starting / finishing windows | **0 / 0** |

**Comparison:** The original first-four-segment source-pinned replay produced the *same* six qualifying following episodes, 86.444 seconds and 2.259 s sampled median observed gap. The additional sixteen segments therefore supplied **no additional episodes passing this particular conservative following filter**; that is **not** evidence of zero ACC use, zero lead vehicles or zero lane changes during the remainder of the morning. The full run also found no qualified lane-model starting or finishing windows among 22,814 model records. This is absent qualified phase evidence, **not evidence that the truck never crossed a lane boundary**. In particular, lane-centering corrections while maintaining a lane are not counted as lane changes. All six qualified acceleration-motion episodes were labeled `model_flagged_lead`, with **zero** `radar_flagged_lead` episodes; these are recorded source labels, **not proof of what the stock OEM ACC ECU tracked**.

The two different time totals (86.444 s following versus 86.741 s motion) arise from separate eligibility, rates and episode integrations; they must not be conflated. Relative-distance / ego-speed observed headway does **not** reveal the selected OEM ACC gap target. `carState.aEgo` is estimated motion, **not decoded factory throttle, brake pressure or commanded acceleration**. The jerk-like window derivative is not a validated occupant comfort or collision-risk measurement. These thousands of samples are correlated within **six** episodes on one partial route, not thousands of independent trials. The original drive reported a dirty installed working tree, so full Git-commit header matches do not prove executable/runtime equality.

## Staged-plan implications and remaining blockers

This completes a bounded *partial route* source-pinned check of ACC / lane-model sample coverage, not staged-plan steps 6, 7 or 8's held-out acceptance. New independent ACC-active and lane-change-positive recordings with physically verified crossing, speed/gear/driver state and source quality are needed. The November 14 +171 s saturation event has no verified high-resolution R-log; Nov 17 segment 30 actuator-limit matches are observational and controller phase ambiguous. PAM-to-EPS steering command identity/authorization remain unresolved; Nov 18 EPS OEM auto-park remains a measured ~14.6 s status episode only. The prior owner-approved parked diagnostic CPU result ~21.6% of one core exceeds the 15% gate; no new parked hardware run is implied.

No public raw logs, no installation or calibration candidate, no CAN injection, no OEM/Panda authorization changes, no changes to the original development/Claude branches or production Railway collector. **Research-only; independent held-out testing, peer safety review and explicit parked/controlled-area operator approval are still required.**
