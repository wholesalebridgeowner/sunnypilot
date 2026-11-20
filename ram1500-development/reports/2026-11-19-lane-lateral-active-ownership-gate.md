# November 19, 2026 — require source-verified Comma lateral authority for lane model-phase comfort

**Status:** Validated read-only, offline integrity gate. **Not** steering calibration, physical lane-crossing confirmation, safety acceptance or an installable release.

## Why this gate exists

Previously the model lane-change observer used model starting/finishing states, same-segment vehicle steering-rate observations, CAN health and EPS steering-fault checks but **did not require Comma's actual lateral actuation to be active**. Model state is not sufficient to attribute wheel movement to Comma: driver- or OEM-owned steering may coincide with model transitions. Counting those windows as *qualified Comma-controlled comfort* could be misleading.

This correction makes actual recorded `carControl.latActive` evidence an explicit requirement in the private and command-line research entrypoints, without equating it with EPS acceptance or a physical lane crossing. Factory ACC cruise status is **not** required, since this platform can separate longitudinal OEM cruise from Comma lateral activity.

## Evidence-quality rules

- `carControl` observations are source-pinned through the same recorded-build decoder, with full exact source headers required for every private R-log segment.
- A completed model-phase candidate must have past-only `carControl` observations at its entry and exit and at **every** vehicle steering-rate sample, each no more than **150 ms** old. Each supporting control state must have `valid is True`, `latActive is True`, and the same segment as the model phase.
- Every intervening observed control update must also be valid, lateral-active and same-segment. An observed gap exceeding the configured bound, a simultaneous contradictory control state, inactive control, unknown status or cross-segment match **rejects** the phase. Neither future samples nor an older healthy observation can fill an invalid same-time barrier.
- A fixed anonymous count `lateralInactiveOrUnverified` indicates exclusions. No per-sample motion, route identifiers, VIN, GPS, clock times, raw CAN, private credentials or untrusted enum strings are serialized.
- Existing synthetic/legacy `analyze()` calls continue to expose the older observational contract; `private_comfort_rlog_probe.py` and `investigate_comfort.py` explicitly opt in to strict lateral-ownership evidence.

## Exact validation

- **Unchanged main and concurrent development branches.** PR #32 stacks only on draft PR #30, whose inspected parent was `9b50fb344670f83dd989a4055431f1b85ca0f57d`.
- **Exact source/tests/entrypoint commit:** `72ab211f237b24c288a7eb5cb276bb2247189499`. Changes restricted to the lane-phase observer, synthetic fixture tests, local research CLI and private Railway comfort entrypoint, plus private summary regression tests.
- GitHub Actions **six existing Ram workflows SUCCESS** on that exact code commit, including [pinned comfort #44558306064](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44558306064), with **203 passed**; no new workflow file.
- Source-pinned isolated private Railway service `ram-private-comfort-once`: deployment `private-railway-id`, **SUCCESS**, exact source commit `72ab211f237b24c288a7eb5cb276bb2247189499`, Docker **185/185 tests passed**.
- Private replay: **20/20 complete retained partial-morning R-logs**, **20/20 exact full recorded-source headers**, **2,005,758 decoded messages**, **zero truncated files**. There were **22,814 model observations**, but **zero completed model starting/finishing phases** and therefore **zero natural qualified windows** and **zero ownership-based exclusions**. This is the same previously analyzed partial-morning recording, **not** a new held-out drive. Synthetic fixtures establish fail-closed behavior, not measured lane-change improvement.
- Longitudinal observational findings were unchanged: **six following episodes / 86.444 s / median sampled observed headway 2.259 s**; six motion episodes / **86.741 s**. None of these identify the OEM selected following gap, actual throttle/brake commands or radar ECU actuation.

## Remaining limitations and gates

`latActive` is a software controller-activity flag, **not** proof that EPS accepted a command, that the truck physically changed lanes or that an automated lane change is comfortable. Coverage is bounded by logged sample timing and does not prove the absence of unobserved short transitions; invalid and missing observations are rejected, not interpreted as healthy. The exact on-device dirty working tree and executed controller remain unverified.

The November 17 steering-cap command phase is still non-identifiable under rate-limited retrospective alternatives; the November 14 high-resolution saturation R-log remains unavailable (only scoped Q-log evidence). No calibration value is justified without independent held-out lane tracking and ping-pong analysis. Factory ACC actuator authority, verified PAM-to-EPS control message, parked operator-approved device CPU/RSS acceptance, peer safety review and supervised closed-course acceptance remain unresolved.

**No Comma/device software, OEM ECU, Panda safeguards, production collector, steering limits, throttle/brake control, CAN traffic, installer or private data was modified or published.** The complete eleven-step vehicle release remains blocked.
