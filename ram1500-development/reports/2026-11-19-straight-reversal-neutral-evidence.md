# Ram held-out steering research: source-clock ambiguity and neutral-gap reversal continuity

**2026-11-19 (US Central) | Offline observational measurement only | Draft review**

## Provenance and scope

- Stacked on the exact draft PR #18 source tree at `4f65a19a5229dec8938ae60efb36193b30e49bdc`. The original vehicle recording/build remains `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (dirty observed device tree).
- Modifies only `ramtools/lateral_stability_observer.py` and the matching synthetic regression tests. The original working tree, installed truck, live Railway collector/reviewer and parked diagnostic sidecar remain unchanged.
- Relevant 11-stage-plan gates: **5** (active healthy steering evidence), **6** (independently measured reversal/oscillation proxy), **8** (offline test and review). This is not lane-centering or physical crossing ground truth.

## Measurement defect addressed

The prior straight-driving reversal proxy retained the previously confirmed positive steering-rate sign across arbitrarily long periods of qualified **near-zero wheel rate**. A later negative rate could therefore be counted as a sign reversal despite an extended neutral interval separating unrelated maneuvers. That biases an observational proxy toward apparent oscillation and can obscure comparisons of stabilizing response delay.

It also resolved two contradictory `latActive`, desired-curvature, or native-output observations with **the same timestamp** by whichever record happened to appear last. This makes eligibility depend on log ordering, not the vehicle evidence.

## Correction

1. Once qualifying wheel rate remains below the sign threshold for a continuous **250-ms neutral dwell**, reset confirmed reversal sign continuity. Brief neutral interruptions preserve reversal detection. The time is a **measurement segmentation rule**, not an actuator/controller tuning value.
2. Fold exact same-timestamp duplicate source observations only when both value and type agree; otherwise mark that timestamp ambiguous, disqualify as-of use of that source until a genuinely later observation arrives, and never select by input order.
3. Validate source tuple/clock shape and reject booleans as timestamps. Keep existing past-only 120-ms freshness, 100-ms state-gap continuity, active/healthy CAN, driver override, speed/gear, straight-curvature and torque-limit filters.
4. Add `straight_wheel_rate_reversal_neutral_gap_source_conflict_v2` qualification to each per-segment and combined summary; refuse to combine legacy v1 results with revised v2, because historical reversal rates are **not directly comparable**.

## Reproducibility and validation

Synthetic tests exercise long versus short neutral gaps, exact equal-time contradictory controls and native output in either arrival order, benign duplicates, invalid source clocks, and mixed qualification rejection. Existing synthetic controlled-sign, source freshness, driver override, curved-road, high-torque and combine tests also run.

Run from repository root: `cd ram1500-development && python -m pytest tests/test_lateral_stability_observer.py -q`. The pre-existing `ram-pinned-comfort-tests.yml` workflow already covers this test file; no new workflow created.

**Important:** This report documents a source-level testable correction, **not** a new real-route measurement or passing CI claim. Record exact final commits / CI statuses in the draft PR after verification.

## Still blocked

Actual independent source-pinned `latActive=true` R-log replay and lane-boundary truth; matching baseline/candidate replay on independent held-out routes, quantified real overshoot and lane-change recentering, closed-loop controller attribution, factory ACC actual actuator sourcing, approved parked diagnostic CPU/msgq tests and independent safety acceptance. The already observed morning 20 R-log segments contain genuine Comma engagement, but these fixture tests do **not** rerun those private logs. A low reversal count cannot prove absence of ping-pong or safe tuning. No revised delay or steering command is proposed.
