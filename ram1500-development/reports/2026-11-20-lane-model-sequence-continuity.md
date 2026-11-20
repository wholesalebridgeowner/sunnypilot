# November 20 — paired lane-change model phase continuity audit

**Status:** New privacy-minimized, read-only offline sequence observer, **synthetic fixtures only**. This is stage 5/6 lane-change evidence preparation, not a physical crossing/centering measure, controller replay or tuning approval.

## Source and integration

- Based on draft PR #32 branch `ram1500-lane-lateral-ownership-20261119` at frozen parent `fff4a110777dd688e06e7e9ecdf4066e91118749`, retaining the previously reviewed fail-closed per-phase comfort observer.
- Exact historical installed driving source `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; pending independent device working-tree verification.
- New companion `ramtools/lane_maneuver_sequences.py` links three sequential *model states* in one segment: observed neutral, `laneChangeStarting` (entry/crossing proxy), `laneChangeFinishing` (recenter proxy), then an observed `off` exit.
- Previously, individual qualified starting and finishing windows could be counted without proving they belonged to one uninterrupted model-reported maneuver. The companion addresses this specific observation gap without assigning undocumented physical lane-crossing meaning.
- Requires ordered continuous model timestamps, consistent direction, at least two model samples in each phase, and fresh past-only, same-segment `carControl.latActive=true` across entry, transition, exit and intervening controls. Gaps, conflicting equal-clock records, uncertain control ownership, changing direction and recording boundaries disqualify.
- Emits only aggregate qualified counts, direction distribution, median paired phase durations and rejection reasons. No timestamps, segment IDs, raw CAN, VIN or private route keys; no vehicle write path.

## Validation and limits

Run `cd ram1500-development && python -m pytest tests/test_lane_maneuver_sequences.py tests/test_lane_phase_quality.py -q` plus new dedicated GitHub workflow. Real private source-pinned multi-route replay and physical lane tracking are **not yet complete**. The model's `laneChangeStarting` is not a verified crossing, `laneChangeFinishing` is not proved recentering relative to physical lane lines. No lateral accel/jerk, overshoot, steering gain, vehicle improvement or safety acceptability is inferred. Pair results with the existing per-phase steering-rate/derivative observer only after independent route selection and provenance verification.

Separate existing blockers: historical Nov 14 steering event, held-out physical lane center/oscillation, factory ACC actuator authority, parked logger CPU/private RSS, OEM PAM↔EPS command authorization, independent safety review and supervised vehicle acceptance. No Pi/Comma installation or control change authorized.
