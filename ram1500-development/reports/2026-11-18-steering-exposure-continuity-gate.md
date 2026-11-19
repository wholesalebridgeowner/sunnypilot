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
