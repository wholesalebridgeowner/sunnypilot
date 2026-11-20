# Post-exit lane wheel-rate attribution integrity — November 20, 2026

**Scope:** read-only source-pinned offline observation only. Parent draft PR #61 at `c6c407c6f2c4bc127753aaafd78ad34047d011bb`; observed source reference `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. Historical device working-tree dirtiness is not independently resolved. Not a physical lane position or line-crossing measurement, controller calibration, or release.

## Gap and correction

Earlier post-exit analysis could qualify a nominally healthy one-second wheel-rate tail even if **the immediately preceding starting/finishing maneuver was unqualified** because of driver steering intervention, EPS fault, invalid CAN, missing wheel telemetry or an unverified sampling gap. Later healthy evidence cannot restore lost attribution for the full maneuver. The corrected post-exit gate requires a successfully qualified, healthy, same-segment two-phase steering-wheel envelope before collecting any post-exit score. For unqualified preceding maneuvers, a separate aggregate `postExitManeuverKinematicsUnqualified` exclusion is counted, and both post-exit peak/tail outputs remain absent.

The old post-exit filter also admitted `preLaneChange` as a neutral model state; the car may already be preparing another lane change. The corrected observational window requires uninterrupted `off` samples, not merely a state from the broader neutral-anchor tuple. It does not infer that `off` means the vehicle is centered or that no chassis motion is happening.

Post-exit measurement definition is **`healthy_maneuver_off_only_v2`**. Post-exit aggregate results produced before this filter change are **not directly comparable** without rerunning on the identical private input bytes, windows and sampling gates. The phase-transition wheel derivative definition (`phase_boundary_inclusive_v2`) is unchanged.

## Reproducible offline checks

Run `cd ram1500-development && python -m pytest tests/test_lane_maneuver_sequences.py tests/test_lane_phase_quality.py -q`. New synthetic anonymous tests reject an otherwise healthy tail after intervention, CAN failure, EPS failure and missing in-phase observations, enforce input-order invariance, reject `preLaneChange` during tail observation, and retain qualification for a complete, continuously healthy off-only sequence. Output contains no route, segment index, date/time of drive, location, raw CAN, VIN or operator identity. This correction neither reads the private Railway bucket nor writes to a device.

## Remaining release gates

This change guards an observational proxy, **not an observed steering improvement**. The new qualification needs private real-route replay using exact source and held-out commitments, independent route-level stability and physical lane-position validation, additional complete lane crossings, manufacturer factory ACC attribution, a separately approved parked diagnostic CPU/memory acceptance run, independent safety review and supervised validation before any vehicle controller change.

**Safety:** no vehicle commands, no installer link, no source-tree update to the installed Comma, no Panda/CAN actuation, no changes to OEM steering/brake/throttle authorization, no new Railway service or log publication. PR remains draft.
