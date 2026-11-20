# November 18 — recorded-stream-start lane-model integrity gate

**Status:** Offline code change and synthetic tests independently verified, followed by private one-shot historical R-log replay. **Research-only.** This report contains no route identifier, private log object key, VIN, location, CAN payload, or exact maneuver timestamp.

## Source and scope

- Draft PR #12, stacked on draft PR #11 `ram1500-lane-postgap-entry-gates-20261118`; original `ram1500-custom-development`, Claude review, steering/PAM PR #1, and pinned comfort PR #2 are not modified.
- Historical installed-build schema and decoder pinned to `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. Recorded `initData` references match this pin; the device's historical **dirty working tree** remains unverified.
- Change `ramtools/lane_phase_quality.py`: when the first valid model observation is already `laneChangeStarting` or `laneChangeFinishing`, the algorithm now records `censoredAtStreamStart`, rather than treating that observation as a verified phase entry. The stream must subsequently show valid `off` or `preLaneChange` before a new model-phase entry can qualify.
- Unknown first states likewise cannot serve as anchors. The handling of invalid records, missing segments, direction reversals, and post-gap phase changes from PR #11 is preserved. This is a counting/inference correction only, not a driving-controller change.
- Synthetic regression inputs that previously began mid-phase were explicitly given a preceding neutral observation where their original purpose was to exercise other quality gates. Four new targeted tests cover first-active left-censor, unknown-first-state, verified neutral re-entry, and invalid initial records.

## Completed validation

- GitHub Actions pinned comfort fixture run [44393170951](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44393170951), **SUCCESS**, on `08d3aa82f52f624e81ebe03397350e76d845acb9`: **120/120 tests passed**. Manifest validation and non-installable delay experiment steps passed.
- Railway one-shot private comfort analysis deployment `private-railway-id`, **SUCCESS**, using the same exact source commit; **81/81 container fixtures passed**. It decoded **316,310 messages** from **3 completed private R-logs**, **0 truncated**, with **3/3 recorded-build commit observations** matching the historical pin.
- No first-active stream censor was observed in these selected three logs (the corresponding report counter is absent/zero). **No change** to the previous actual-route aggregates: **0 qualified model starting phases**, **2 qualified model finishing phases** (one left, one right), and **3 qualifying observed following episodes totaling 40.507 s** with **1.179 s median sample headway**.
- The above numbers are observations on **correlated recorded intervals**. Neither model-state `finishing` nor steering rate proves lane-boundary crossing or a completed physically comfortable recenter. Observed sample headway is not the factory ACC set point or its control target.

## Open gates and interpretation limits

The defect is **demonstrated by synthetic tests**, not by a first-active episode in these selected R-logs. A first-active phase in another recording would now be correctly censored. This fix must not be interpreted as improved vehicle behavior. The November 14 +171-second saturation event still needs matched high-resolution R-log evidence. November 17 controller saturation matches remain phase-ambiguous; neither rate-limited replay alternatives nor same-gear CAN activity identifies an actual control request. The verified 1,460-sample OEM parking EPS status interval does not establish PAM command identity or third-party low-speed control authorization.

Factory ACC throttle/brake commands, real physical lane crossing, parked-device CPU/memory profiling, exact on-device source reconciliation, reproducible Comma 3 build, independent hardware/safety review and controlled validation remain outstanding. No active truck code, CAN transmission, actuator control, Panda limits, ECU permissions, or installation URL was involved.
