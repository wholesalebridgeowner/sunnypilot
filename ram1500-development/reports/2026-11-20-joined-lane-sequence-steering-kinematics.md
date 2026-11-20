# November 20 — joined lane model sequence steering-wheel kinematics

**Source:** exact observed installed driving build `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, but dirty device working tree remains unverified. Frozen parent: PR #58 / `0bfe1106366d8b993f7a2c6cab5d4890e313186b`. All tests use synthetic privacy-safe fixtures; no new private R-log replay occurred.

## Purpose and contract

Earlier PR #58 identified same-segment, continuous neutral → starting → finishing → off **model-reported** sequences with fresh past-only `carControl.latActive`. The per-phase quality observer separately computed steering motion, but those phase distributions were not inherently paired within **the same** observed model maneuver. This offline addition measures a joint steering-wheel-rate envelope only for model sequences already lateral-owned and additionally covered by carState measurements on **both** phases. It rejects: sparse phase coverage, stale/mismatched segment or contradictory clock samples, vehicle CAN invalidity, unknown/true EPS fault, unknown/true driver steering override, nonfinite steering rate/speed, or low speed.

Exports only aggregate numbers: qualifying model/owned/kinematic sequence counts, P90 of the **per-sequence peak absolute observed steering-wheel rate**, P90 of the **per-sequence peak observed wheel-rate derivative** (degrees/s²), and rejection categories. Differentiation is within each model phase only, never across the phase boundary. Derivative means **steering-wheel angular acceleration**, not chassis lateral jerk; high or low values are not themselves proof of lane comfort, safety or any steering gain. No route IDs, time stamps, source-record hashes, GPS, VIN, CAN bytes or actual command traces are exported.

The ownership, clock and vehicle-health gates are observational eligibility filters, not causal identification of which actuator generated wheel motion. `laneChangeStarting` does not verify physical lane crossing; `laneChangeFinishing` does not prove recentering or settle-out. No lane error, ground-truth center, overshoot or lane boundary measurement is available.

## Acceptance and next evidence

Run `cd ram1500-development && python -m pytest tests/test_lane_maneuver_sequences.py tests/test_lane_phase_quality.py -q`; validate in dedicated existing PR-triggered GitHub Actions workflow. Next run the exact same source-pinned observer privately on **multiple distinct, confirmed `latActive=true` routes**, isolate held-out evaluation, compare baseline/candidate only on frozen identical eligibility, and add physical lane position before claiming comfort improvement. Cross-check with PR #18 eleven-step roadmap and independent Claude review. No calibration, truck/Panda/CAN changes, Pi/Comma install or road-release authorization.
