# Ram 1500 — automated Q-log comparison provenance (2026-11-20)

**Research only. No steering, ACC, Panda, EPS, installer or on-device changes.**

## Independently inspected source and new private aggregate evidence

- The scheduled private reviewer is pinned to Sunnypilot GitHub source `8d86107587cb3bd0d8bb5f0679d62bd5b85df01e`. Its recorded Cereal schema requires exact installed-build commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; the historical dirty on-device working tree remains a separate caveat.
- The latest reviewer produced a source-checked observational report for **69 contiguous Q-log segments**, with **16,384 Comma `carControl.latActive=true` samples**, **11 model lane-change starting windows** and **zero observed steer-saturation event episodes**. The report was marked `assistedSteeringReviewEligible=true`, `comparisonBaseline=true`, `becameLatest=true`. These are **sample/window counts**, not maneuver counts, validated route completeness, absence of real-world saturation, or measured physical lane-keeping improvement.
- Read the actual pinned `auto_review_job.py` and `auto_review.py`: the legacy `comparisonBaseline` flag means only that a **different route in private `latest.json` had nonzero lateral-active samples** and was eligible for an unmatched route-to-route trend. It did **not** assert matched weather/road geometry, same-input baseline-versus-controller-candidate replay, physical lane position, exact controller phase or independent held-out acceptance.
- The reviewer accepts routes contiguous from segment zero after a private upload quiet period; it explicitly says **final route segment count unverified**. A previous different-route comparator is not a calibration baseline.

## Offline-only correction on this draft branch

1. Verify that a previous active-route report carries a 20-character hashed route identity distinct from the current route, a complete 24-character input fingerprint, the exact schema pin, valid positive verified-segment count, the assisted-steering eligibility flag and a strictly positive **integer** `latActive` count. Missing, malformed, cross-source or same-route evidence is refused rather than quietly accepted.
2. Preserve legacy `comparisonBaseline` for existing consumers, but append `comparisonKind: unmatched_prior_active_route|none` and `calibrationEligible: false`. Private report version 2 adds the same explicit evidence classification plus four independent **false** qualification flags (matched conditions, paired baseline/candidate replay, physical lane tracking and calibration).
3. Synthetic unit tests cover valid and invalid comparator provenance, anomalous nested metrics, a newly reviewed active route, private output hygiene and the boundary between observational delta and vehicle release. This check is a metadata gate, **not** a calibration proposal or a replay against the new real 69-segment route.

## Interpretation and remaining 11-step release gates

The new independently recorded active route provides a useful additional **candidate** for stages 5–8, and can potentially be compared against other independent recordings after private source-complete R-log acquisition and qualified held-out scoring. Its Q-log aggregate cannot establish wheel oscillation/physical lane centering, genuine full lane-change entry/crossing/recentering, OEM cruise throttle/brake output or longitudinal calibration. The legacy `11 model starting windows` are **not** completed physically crossed lanes.

Still required: source-pinned private high-resolution route replay, physical lateral position/overshoot evidence, independent disjoint route cohorts, measured factory ACC comfort without direct OEM actuation assumptions, actual approved parked CPU/RSS diagnostic acceptance, source-exact control candidate review, and supervised closed-course validation. OEM factory parking status proves a separate low-speed steering authority only; it does not authorize Comma control below the engage threshold.

Keep the production reviewer and collector unchanged. Do not install code on the Comma, re-create deleted network probes, alter Pi/Tailscale/SSH configuration, publish private route identities, invent numerical delay/torque gains or infer a road-release gate from these findings.
