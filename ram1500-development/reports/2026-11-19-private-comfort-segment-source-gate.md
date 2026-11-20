# 2026-11-19 — exact per-segment source integrity for private factory ACC comfort replay

**Scope:** Read-only, research-only source/provenance correction. No vehicle code, controller, ACC, Panda or OEM actuation changed. This draft branch is stacked on `ram1500-asof-ambiguity-gate-20261119` (PR #23) and is separate from Claude's steering / parking branches. The `ram1500-custom-development` baseline is unchanged.

## Why this correction matters

Before this change, `private_comfort_rlog_probe.py:pinned_recorded_build()` counted an 8-character prefix of the recorded `initData.gitCommit` as source verified and returned a status field without blocking comfort output when metadata did not match. An otherwise valid header from one file could also mask a missing header in another selected segment. Thus the headway and acceleration proxy report did not itself prove that **every** processed R-log carried the full installed-source identifier.

The new guard requires **the exact, case-sensitive 40-character installed source commit** in at least one `initData` event **inside each selected R-log segment**, without extra unscoped headers, duplicate/invalid selected segment labels, truncated files or disagreement among multiple headers. A failure stops the private one-shot process before it emits comfort aggregates. Privacy-safe output includes only counts of verified files and headers; raw logs, route keys, identifiers, location, per-frame data and token values are not exported.

The guard reuses `ramtools.recorded_source_integrity.require_exact_recorded_commits`, already used by the independent full-route cruise audit; it does **not** assert that the historical dirty on-device tree was identical to the public commit.

## Source-pinned implementation and regression results

- Source change: `0a10d6efbdaeef0da919c4efff619d64473127ad`.
- Synthetic test cases: `3ed9e3f873a3cb4794e2b9c586755ab6edc32648`. Initial isolated container build at `8489c6e7bd59b190a0021a402186ca9375ca0832` found **one test-fixture error**: the privacy assertion for an empty invalid string incorrectly checked that an empty string was absent from a nonempty exception. It passed 153 tests and failed 1; source analysis was not executed from that failed build.
- Fixture correction: `f78bf78f6c4195c1b0ebaa1c2a4e1d36baa39479`.
- Independent source-pinned Railway deployment: `private-railway-id`, **SUCCESS**, GitHub source commit exactly `f78bf78f6c4195c1b0ebaa1c2a4e1d36baa39479`; Docker executed **154/154 passing pytest tests**.
- Dedicated offline GitHub Actions workflow source: `8489c6e7bd59b190a0021a402186ca9375ca0832`; **GitHub Actions pass has not been verified**.
- The Docker build includes original source/schema hash validation and the existing full-route, steering, ACC and lane regression suites. No private credentials entered any build argument or public test logs.

## Replayed private morning ACC comfort evidence

The existing isolated Railway research service `ram-private-comfort-once` (not the production collector) decoded the same already-stored four consecutive morning R-logs using the fixed entry point. This is **not a new held-out driving dataset**.

| Private replay metric | Verified |
|---|---:|
| Complete, decoded R-logs | 4/4 |
| Full recorded-source headers accepted | 4/4 |
| Truncated/failed files | 0 |
| Decoded messages | 421,759 |
| Accepted cruise/lead observations | 1,735 |
| Qualifying following episodes | 6 |
| Total qualified following exposure | 86.444 seconds |
| Median observed sampled headway | 2.259 seconds |
| 10th-percentile observed sampled headway | 1.543 seconds |
| ACC-conditional qualified observational motion episodes | 6 |

**Attribution:** These are samples of a recorded factory ACC-active route, not proof of the driver's OEM **selected following setting**, OEM acceleration request, throttle/brake command, pedal positioning or direct controller smoothness. Correlated samples and the same four segments do not support an independent safety or population estimate. The source-pinned decoder's agreement with bus-2 `DAS_3` is decoder consistency, not an independent measurement of OEM actuation. The truck's initial Sunnypilot 32 mph engagement threshold is not a steering-disable threshold after engagement.

## Unclosed gates

1. Complete high-resolution November 14 +171s saturation R-log is still absent from the checked private archive; no causal diagnosis.
2. November 17 segment-30 steering cap/controller phase and OEM parking command ownership require passive, source-documented analysis; DBC EPS `AUTO_PARK_HAS_CONTROL_2` confirms OEM control status for ~14.6 seconds but is not third-party permission.
3. More independently held-out ACC-active and high-quality lane-tracking drives, physical lane crossing, steer-control stability and quantitative OEM throttle/brake source semantics remain necessary before any calibration.
4. Original parked logger resource measurement was ~21.6% of one CPU core vs the 15% criterion. No new on-device test was run; operator-approved parked validation remains mandatory.
5. Independent source and safety review, reproducible installed binary, permitted Panda/OEM envelopes, fail-safe checks, supervised controlled-area validation and explicit owner approval remain unfulfilled. **No installer, calibration, CAN actuation or road-release candidate is authorized.**

All private R-logs remained private. The original development branch, production Railway collector, vehicle firmware/software, safety model and EPS permissions were untouched.
