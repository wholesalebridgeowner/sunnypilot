# Steering observational replay: reject same-timestamp source ambiguity

**2026-11-19 — research-only, offline data-integrity correction. No driving-control change or installation authorization.**

## Defect and correction

The existing straight-driving reversal observer used `bisect_right` on sorted control, curvature and native-output source streams. Two rows with **identical timestamps but contradictory values** were silently resolved by whichever row appeared last. Consequently, the same telemetry multiset could pass or fail steering eligibility depending on enumeration order. The ambiguity could affect exposure duration and reversal counts without any reported rejection.

Read-only `ramtools/lateral_stability_observer.py` now rejects contradictory same-time observations in each of the three source streams with a value-free error. Identical duplicate observations are coalesced. Malformed rows, negative/boolean/nonintegral source timestamps fail closed. Past-only 120-ms joins and all existing steering eligibility rules are preserved. This is a provenance check, **not** a controller or steering-response calibration.

- Stacked from draft PR #22 / `ram1500-steering-neutral-contiguity-20261119` head `e7d233b33dbfc6099d6974cc594efa6be4822d9c`.
- Source-fix commit `6a437ea1e02ae9412f51d1ced578db54bddb4324`.
- New synthetic fixture commit `97839ef7710cd9e5346d8c21d8b25ff8c2bf6205`; cases cover contradictory Boolean and numeric streams, permutation sensitivity, identical duplicate coalescing, bad timestamps/row shapes and privacy-preserving exceptions.
- Dedicated GitHub workflow commit `7e5b6d0d6aaff6486f5eb5e0119d19be4c363a03`.

## Independent pinned regression and private replay

Source-pinned isolated **Railway** service `ram-full-route-cruise-private-once`, deployment **`7dc25327-7a46-4080-bafb-10a8b438d210`**, built commit **`7e5b6d0d6aaff6486f5eb5e0119d19be4c363a03`**. Deployment status **SUCCESS**. The Docker build reported **137/137 tests passed**. This is an observed Railway Docker test pass, *not* a GitHub Actions pass (none registered at this checkpoint).

The completed private replay of the **same 20 previously retained morning R-logs** returned **20 complete segments**, **2,005,758 decoded messages**, **20/20 exact full recorded-build commit matches** against `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, and no truncated files. The observed steering proxy is unchanged: **1,903 qualified samples, 18.859 seconds, 17 continuous intervals, four at least two seconds, longest 5.889 seconds, 14.549 seconds in >=2-second intervals, zero sustained reversals**. This demonstrates no ambiguity failure in these selected recordings under the new guard, **not** that all available historic logs lack duplicates or that there is no ping-pong.

The retained morning evidence still has actual factory cruise and Comma lateral engagement; do not conflate it with the separate afternoon factory-cruise-off recording. No following-distance target, actual factory throttle/brake command, causal actuator phase, lane-center accuracy or numerical steering delay was established by this audit.

## Remaining release gates

No independent held-out lane tracking/oscillation comparison; Nov 14 +171-second high-resolution R-log remains missing; Nov 17 steering-cap cause remains observational; OEM PAM-to-EPS command ID, originating module and authorization are unverified despite 1,460 recorded EPS parking-mode samples; factory ACC acceleration ownership and smoothness remain incomplete; actual parked logger CPU was previously **21.6% of one core**, above the 15% criterion and requires fresh explicitly approved *parked* retest; dirty-tree executable, Panda/OEM safeguards, independent review, reproducible build and supervised closed-course validation remain gates.

The original `ram1500-custom-development` branch and Claude's PR #1, PR #2 and review branches were not modified; production `comma-log-sync`, private bucket, truck software and ECU/CAN/Panda settings were not changed. Only an isolated Railway research service was repinned to the read-only analysis commit; private logs remain private. **Do not merge into vehicle-control software or produce installer links on this result.**
