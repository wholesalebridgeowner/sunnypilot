# Ram lane-change post-exit wheel-rate observation — 2026-11-20

**Scope:** Read-only, synthetic-tested observational research only; not a controller change, calibration or vehicle installation. Based on draft PR #59 at `bd088c8e1ef1beb56a1fb8b0c0fb7df6c2a20ff0`. Recorded-source reference is Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; historical device working-tree dirtiness remains unresolved.

## Why this is a separate measurement

Prior paired model-phase analysis measured wheel-rate and its derivative during model-reported `laneChangeStarting` and `laneChangeFinishing`. Neither phase proves physical lane crossing, or whether the truck stops correcting its wheel *after* the finishing model phase ends. Treating the model's `off` state as physical lane centering would be incorrect.

This addition measures only **observed steering-wheel-rate residual during a configurable 1.0-second post-exit interval**. It exports the P90 of per-sequence peak absolute rate and median of per-sequence last-quarter median absolute rate, alongside qualification/exclusion counts. The duration is an observation-window definition, **not** a vehicle tuning value. It does not estimate wheel angle bias, lane position, path curvature, driver comfort, OEM EPS authorization or physical recentering.

## Evidence qualification

Post-exit observation requires all pre-existing qualified same-direction, single-segment starting→finishing→off model and Comma-lateral-active checks, plus:

- All model samples through the post-exit window remain valid, **neutral** (off/preLaneChange), same-segment, with bounded gaps and right-edge coverage. A renewed lane-change state or missing model evidence disqualifies the measurement.
- Fresh, past-only, continuously `latActive=true` `carControl` evidence throughout the post-exit interval, including its endpoint. Unknown/contradictory or wrong-segment status fails closed.
- At least four continuous, fresh, same-segment `carState` samples across the window; valid CAN, no reported EPS fault, no steering intervention and known finite wheel rate/speed at or above the observation speed gate. Last-quarter tail requires at least two samples.
- Same-timestamp contradictory source records are quarantined through the existing source coalescer. A rejected interval does not contribute zeros to percentile results.

The post-exit results are explicitly **not** the same as physical settle-out, lateral tracking error, oscillation damping, lane boundary crossing, lane-line reacquisition or roadway position. This is a wheel-motion proxy only. Further route-held-out, independently measured geometry and verified safety evidence are required.

## Tests and data boundary

Synthetic tests exercise a completed healthy post-exit interval, no later model/vehicle coverage, renewed maneuver state, inactive or conflicting Comma control, driver steering intervention, segment mismatch, invalid observation-window configuration, input order invariance and privacy exclusions. Existing `ram-lane-sequence-tests.yml` is reused; no new CI workflow, private collector or running Railway service is modified.

For reproducibility: `cd ram1500-development && python -m pytest tests/test_lane_maneuver_sequences.py tests/test_lane_phase_quality.py -q`. Verify the actual PR-head GitHub CI result rather than assuming a passed local synthetic suite proves real hardware performance.

No private Q-/R-log replay of this newly introduced post-exit statistic is claimed. The private route reviewer was independently inspected: its latest report admitted a newly observed two-segment recording with zero laterally active samples, so that report cannot supply eligible evidence for this post-exit comparison. The collector and reviewer are left untouched.

**Outstanding:** independently sourced multiple real active-driving routes and held-out lane position/overshoot scorecards; OEM ACC actuation ownership; PAM-to-EPS message attribution; approved, parked diagnostic CPU/RSS acceptance; independent code/safety review and supervised testing. No steering gains, torque cap, brake/throttle, firmware, Panda permissions or device files were changed.
