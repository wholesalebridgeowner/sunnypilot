# 2026-11-20 — source-qualified observational factory-ACC acceleration and sampled jerk

**Status: offline analysis instrument, NOT a real-route result, software calibration, or vehicle release.**

## Frozen provenance and scope
- Exact reported installed-source commit: 1f7233cb983f3c13fdbba47a86f8fbc796ad9211, Sunnypilot 0.10.1. Recorded build had a dirty working tree; byte-for-byte installed executable equivalence is unproven.
- Baseline: read-only automated Q-log reviewer, PR #63 head 6645063cfbe89d5b2d551f595f2d1ba195bf8877 (not the original ram1500-custom-development branch).
- Source-pinned Chrysler factory cruise behavior from PR #18: installed RAM carstate.py derives cruiseState.enabled from bus-2 camera DAS_3.ACC_ACTIVE, with raw CAN ID 0x99. The *current automated Q-log analysis does NOT independently match this raw CAN bit*; only the separate private R-log audit performs that cross-check.
- Earlier validated private R-logs did show **10,861 morning cruise-enabled carState observations**, while a different 54-segment afternoon drive showed zero. This does not establish any longitudinal actuation source beyond the recorded cruise state.

## Concrete analysis gap and correction

The previously exposed comfort.p90AbsJerkMps3 summarizes **all driving** and could easily be mistaken for factory adaptive-cruise jerk, including intervals when the driver used pedals or cruise was off. It remains available for backward compatibility, but its caution now explicitly says that it is *not* ACC-specific.

New factoryAccObservedMotion is **a separate source-qualified observational statistic**. Only a carState with ALL the following participates:
1. carParams.openpilotLongitudinalControl is exactly false AND pcmCruise is exactly true, with no contradictory CarParams across the recording.
2. Parsed cruiseState.enabled is exactly true.
3. Both driver gasPressed and brakePressed are exactly false (a missing boolean does **not** pass).
4. Finite vEgo > 2 m/s and finite aEgo.

Qualified individual acceleration samples contribute signed p10/median/p90 observed acceleration. Consecutive qualified samples contribute absolute acceleration-difference jerk only when their **log-monotonic interval is 30–300 ms**; cruise-off, pedal presses, invalid state, tied/reversed time and missing telemetry break continuity. Jerk calculations never bridge such gaps. Observed duration is summed over eligible adjacent pairs rather than assuming a fixed Q-log rate or treating separate windows as continuous exposure.

**Important limitation:** none of this proves that the vehicle's engine, brakes, or throttle were commanded by the OEM ACC controller. CarParams establish the supported authority architecture, parsed cruise flags imply active cruise as reported, but true actuation attribution requires independent source traces. Q-log acceleration differentiation is noisy and may miss short transients. No set following distance or ACC algorithm gain is inferred, and no steering/coupling tune is proposed.

## Regression and privacy design

Six fixture tests cover: qualified positive exposure, driver-pedal and cruise-off separation, invalid speed/unknown fields, non-stock longitudinal configurations, contradictory CarParams invalidation, nonmonotonic/duplicate timing and privacy-safe output. Existing Q-log reviewer tests remain the integration regression. The output contains no timestamps, route names, raw CAN, VINs, identifying location, or individual acceleration traces. Fields actuatorAttributionVerified=false and calibrationEligible=false remain false unconditionally.

## Validation and next evidence gate

Run GitHub Actions **Ram automated review offline tests** and **Ram offline telemetry analyzer tests** on this draft PR. This branch must not trigger a Railway redeploy because the existing scheduled service is pinned to its separate reviewed branch. Follow-on meaningful validation requires **private** reprocessing of at least two independent, source-pinned cruise-active R-log routes with independent bus-2 DAS_3 agreement, identical temporal gating, actual lead provenance and full latency/missing-data coverage. Compare conditional acceleration/jerk *within* each qualified episode (not unmatched route averages). Independently reviewed longitudinal attribution and supervised safety validation remain required before any vehicle release.

No Pi or Comma login was attempted. No CAN transmission, Panda/OEM safety changes, throttle/brake/steering adjustment, installer, public raw log disclosure, or Railway service changes.
