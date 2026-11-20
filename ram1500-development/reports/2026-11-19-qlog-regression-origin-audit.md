# November 19 — independent Q-log regression-origin audit (offline research only)

**Scope:** Draft PR #37, stacked exclusively on draft PR #36. Original development branch `ram1500-custom-development`, production Comma log collector, active vehicle software and Panda/OEM security are unchanged. No steering, braking, throttle, CAN injection or on-device logger work is authorized by this change.

## Evidence motivating the change

The source-pinned private Railway research reviewer previously returned `bounded_trial_rejected / event_clock_regression` for one already-quarantined, 54-segment Q-log recording, under deployment `private-railway-id` at exact tested source `d0e517252dd8b02be5e6b74c7242e159e0f9b390`. That combined category did **not** distinguish a cross-segment clock overlap from an already-emitted in-segment event-time ordering violation. It did **not** establish a logger fault or safe steering baseline.

## Minimal, fail-closed change

In `auto_review_bounded_order.py`, each segment now tracks whether it has emitted any ordered observations. A time regression at the first emission of a new segment is rejected as `cross-segment-clock-regression`; a regression after an emission within that same segment is rejected as `emitted-watermark-regression`. Neither condition is silently repaired by reordering across segment boundaries. These fixed internal tokens map in `auto_review_job.py` to **`segment_boundary_regression`** and **`emitted_order_regression`**, respectively; the previous combined token remains recognized for historical results.

The scheduled read-only Railway research reviewer now performs at most one additional, exact-source-pinned diagnosis of an **existing** combined `event_clock_regression` trial. It writes only a new immutable `regression-origins/` object in the existing private bucket, retaining the previous `bounded-trials/`, quarantine tombstone and `latest.json` without modification. It does not calculate or promote driving metrics. The result contains a coarse category, nonreversible input fingerprint, schema pin, count of selected segments and `releaseAuthorized=false`, never original route keys, timestamps, CAN payloads, VIN or locations. Unknown decoder exceptions map to a constant generic label, not potentially sensitive exception text.

An explicit `previous_rejection_not_reproduced` outcome means *diagnostic disagreement only*, never a passing route review or authorized calibration.

## Independent fixture and source verification

- Tested source **`2377c055e1254498307b45aa471dcbda7814f763`** (four narrowly scoped implementation/test commits).
- [Dedicated review workflow #44590533235](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44590533235): **26/26 synthetic tests passed**, covering intra-segment bounded reordering, sub-second cross-segment regression, immutable legacy diagnosis, reader-exception privacy and lack of baseline promotion.
- [General analyzer workflow #44590533188](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44590533188): **172 tests run, one skipped, no failures**. The CI step independently verified the installed `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` Chrysler EPS DBC signal layout and Ram address mapping.
- Private Railway research-only reviewer pinned to the exact tested commit: deployment `private-railway-id`, Docker build/deploy **SUCCESS**. Runtime evidence is separate: Docker build success alone does not establish that a real input was reclassified.

## Independently observed private Q-log outcome

The exact-source-pinned Railway deployment `private-railway-id` reached **SUCCESS**, and its real scheduled execution logged `regression_origin_saved / segment_boundary_regression` for the **54-segment** previously quarantined Q-log recording. The private diagnostic was written under the new immutable namespace; the old `event_clock_regression` trial and original quarantine remain unchanged. The source/test commit was `2377c055e1254498307b45aa471dcbda7814f763`, verified by both GitHub workflows above.

**Interpretation:** The bounded trial encountered a newer segment whose earliest emitted observation was older than the preceding segment's final emitted observation. A previous first-order clock audit on these same 54 segments found one within-segment inversion >0.5 seconds and zero cross-segment adjacent inversions >0.5 seconds. These observations can coexist because the new boundary check rejects *any* overlap, including overlaps too small to meet the prior 0.5-second reporting threshold. No exact gap or segment-specific timestamps are published. This narrows the software rejection **category**, not its physical root cause; asynchronous logger segmentation/interleaving is a plausible alternative to clock reset. The recording is still quarantined, not an independently held-out route, calibration input or accepted driving benchmark.

## Acceptance boundary

Only a successful private diagnostic execution under the pinned deployment can narrow the previous 54-segment failure. If the recording cannot be re-read, remains ambiguous or produces another rejection, quarantine is preserved. No held-out driving scorecard, November 14 full-resolution steering event, OEM factory ACC actuation ownership, PAM-to-EPS command authority, approved parked CPU/memory check or independent vehicle safety acceptance is supplied by these tests. This is *not* a new controller, steering calibration, or vehicle release.
