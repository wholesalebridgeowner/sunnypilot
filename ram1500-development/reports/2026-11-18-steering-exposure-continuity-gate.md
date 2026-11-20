# Steering reversal observability must be conditioned on contiguous clean exposure

**Date:** 2026-11-18  
**Status:** OFFLINE EVIDENCE-INTEGRITY CHANGE; research only, not installable.  
**Parent evidence:** draft PR #18, source-pinned original RAM build `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; morning private replay with 20 complete retained R-log segments.

## Why the additional gate matters

The previous steering-wheel-rate proxy reported 1,903 qualifying samples, 18.859 seconds of clean accepted intervals, and zero sustained rate-sign reversals across the selected morning recording. It **did not** report how those seconds were divided into uninterrupted qualifying runs. That omission is important: if short qualifying windows are broken by steering input, faults, requested curvature, torque exposure or telemetry gaps, a reversal requiring sustained positive and negative steering-rate dwell may be physically unobservable. A zero count then says little about lane-centering oscillation.

This patch preserves the pre-existing accepted-time and reversal algorithms and adds explicit **contiguous-exposure auditing** to `ramtools/lateral_stability_observer.py`.

Each invalid sample is a hard episode boundary. Two accepted samples are connected only when their monotonic timestamps differ by more than zero and at most the existing 100 ms continuity threshold. Segment boundaries remain hard boundaries because `analyze_segment` runs independently per file. The analyzer reports the number of eligible runs, runs at least 0.5, 1.0 and 2.0 seconds, longest run duration and seconds contributed by runs at least 2 seconds. A conservative `sustainedReversalObservability` status is `insufficient_contiguous_exposure` when no eligible run reaches two seconds; otherwise it is `limited_proxy_only`, never `lane_centering_validated`.

The two-second threshold is an **exposure-quality reporting guard**, not a proven oscillation period or an acceptable steering limit. Passing it does not establish absence of ping-pong, physical lateral deviation or safety. Sampled steering-wheel direction changes can also reflect legitimate road curvature and driver/vehicle context.

## Synthetic acceptance tests

Six new fixture scenarios cover (1) one continuous 5.99-second clean run, (2) 24 interrupted ~210-ms runs with a substantial total accepted duration but no two-second window, (3) a single invalid driver-input sample splitting an otherwise contiguous run, (4) a timestamp discontinuity despite otherwise valid data, (5) no qualified samples and (6) aggregation across multiple isolated segments without manufacturing a long run. The pre-existing seven observer tests are retained.

Dedicated GitHub workflow `ram-stability-exposure-tests.yml` exercises the relevant tests on this isolated research branch. **Do not report a pass until the run actually completes.**

## Private measurement and interpretation boundaries

The historical 1,903 eligible samples, 18.859 seconds and zero observed stable reversals are inherited from an already verified private PR #18 Docker replay, **not newly measured by this commit**. The number and lengths of the underlying clean episodes **have not yet been recalculated on private original logs using this change**. No claim of sufficient sustained exposure can be made from the older summary.

Next privacy-safe action: run the source-pinned read-only analyzer over the same 20 retained Rlogs within authorized private Railway infrastructure (without moving keys, files or payloads into GitHub); compare old aggregate fields for invariance, capture only anonymous new aggregate exposure fields, and obtain independent review. If no two-second window exists, the current reversal study is structurally uninformative for oscillation absence. Even if some longer runs exist, independent route-held-out replay, actual lateral tracking and torque/driver-override acceptance remain essential before proposing any numerical steering delay, friction or damping value.

## Safety and ownership

No vehicle CAN output, steering/braking/throttle command, Panda or OEM permission, device SSH, installed software, private log, VIN, road location or credentials are changed or published. This research branch is stacked on PR #18; it does not modify the shared `ram1500-custom-development` branch, PR #1 steering/PAM work, PR #2 comfort work or the existing Railway collector.

**Release gate:** unchanged. No production calibration or installer; owner-approved parked checks, independent safety review and controlled-area supervised testing still required.

## Exact-source independent local regression execution

The committed analyzer and test file were independently reconstructed in an isolated local Python test environment and their `git hash-object` digests **matched** the GitHub blob SHAs:

- `ramtools/lateral_stability_observer.py`: `c01e68cbed6dbabee94f11c822d66c7b6ac82670`
- `tests/test_lateral_stability_observer.py`: `4b49fa0ba989ca599ebcfd389c4c09f2add6df9d`

Execution: `PYTHONPATH=/tmp/ramtest python -m pytest -q /tmp/ramtest/tests/test_lateral_stability_observer.py` yielded **13 passed in 0.08 seconds**, including six newly added synthetic fixtures. The first attempt detected a `NameError` in the new shifted-time test fixture; this was corrected in commit `a419d7dbe7a7fdb15589ca2b3c949022d5af58a1`, and the exact corrected GitHub file hash was then tested to completion.

GitHub Actions was configured for the isolated branch, but a read of the GitHub Actions workflow-runs collection returned **zero registered runs** at verification time. Therefore **CI is not reported as passed**. The successful independent local test does not replace cross-platform CI, the private pinned-log rerun, held-out drive qualification or independent safety review.


## Verified private replay of the revised continuity gate (November 18, 2026)

**Execution:** Private, source-pinned Railway study service `ram-full-route-cruise-private-once`, new isolated research deployment `private-railway-id`, **SUCCESS**. The service was explicitly pinned to the code/test/report parent commit `1de0b2653f7c1f7088ad5312cee090877f007f59` from this draft PR. This is a repeat of the **same 20 retained consecutive morning R-log segments**, not an independent route, a new drive, or a full morning route. The normal five-minute `comma-log-sync` collector and the vehicle were not modified.

**Source and completeness:** 20/20 completed R-logs decoded, 2,005,758 messages processed, 20/20 recorded `initData` source pins matching `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, zero truncated/failed files. The container's pinned Docker build executed **108 tests successfully** (`108 passed in 0.25s` in Railway build logs), including the revised steering observer fixtures. GitHub Actions had **zero registered runs** on the analysis commit at the last query; a private container test pass is not mislabeled as a GitHub Actions pass. The dirty historical installed working tree remains independently unverified.

| Steering-wheel-rate observability metric | Private measured result |
|---|---:|
| Eligible clean samples (unchanged) | 1,903 |
| Accepted continuous interval seconds (unchanged) | 18.859 s |
| Qualified contiguous runs | 17 |
| Runs >= 0.5 s | 7 |
| Runs >= 1.0 s | 6 |
| Runs >= 2.0 s | **4** |
| Longest contiguous eligible run | **5.889 s** |
| Exposure within runs >= 2.0 s | **14.549 s** |
| Sustained steering-rate-sign reversals under the existing strict proxy | **0** |
| Observability classification | `limited_proxy_only` |

All prior sample counts, rejection tallies, and the zero-reversal measurement match the earlier 20-segment private audit; the newly calculated exposure metrics are now independently observed rather than synthetic-only. The >2-second runs mean there **were** limited windows in which the chosen stable-rate-reversal proxy could have registered events. This corrects the earlier uncertainty about *whether any* qualifying sustained runs existed. It does **not** establish an absence of lane-centering ping-pong, good lane tracking, acceptable real-world steering dynamics, or any optimum actuator delay: the proxy uses steering-wheel rate without an independent lane-center error reference and excludes substantial portions of the route. It has no validated sensitivity/specificity for lane oscillation. Serially correlated samples and four eligible runs are not independent held-out driving evaluations.

**Unchanged blockers:** No independent held-out active-lateral route meeting equivalent telemetry quality, no physical lane-tracking error or model-to-EPS latency identification, no verified Nov 14 +171 s high-resolution segment, no OEM PAM-to-EPS command-owner identity/authorization, no successful approved parked sidecar resource rerun, no independent OEM/Panda control safety review, no hardware installation or supervised closed-course approval. No steering, brake, throttle, CAN, OEM/Panda safety, or truck-device operation was changed.

**Operational scope:** For this private replay only, the existing *one-shot read-only Railway research service* was repinned from its earlier draft-PR #18 commit to the source-exact draft-PR #20 commit. The service had already been configured for the private stored segment window; no raw logs or private identifiers were moved to GitHub, and no existing collector settings were changed.
