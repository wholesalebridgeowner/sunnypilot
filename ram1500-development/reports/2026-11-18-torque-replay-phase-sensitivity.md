# November 18, 2026 — bounded causal phase sensitivity of Ram steering replay

**Status:** Observational, offline-only. No control change, ECU/Panda safety change, or installable release.

## Verified provenance

- Private Railway service: `ram-private-rlog-probe`
- Successful Railway deployment: `f3c06fac-de19-4edb-bc8d-68757a22c387`
- Tested/deployed source commit: `312a209f9dd8608e73b0b2c1b7d7f6457771a03f`
- Log schema pinned to the observed Comma software revision `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`
- Private November 17 driving data; selected R-log segments 0, 29, 30 and 31 (421,253 messages), focusing on seconds 1800–1807 (7 seconds)
- Aggregate results only; no vehicle identifier, raw GPS, private timestamps, CAN payloads or raw log objects published

## What changed

The read-only `torque_replay_phase_audit.py` tests *why* a source-based hypothetical steering-command replay diverges from recorded native output. It starts from the same changed-output samples used by `torque_command_replay.py`: the prior output must be earlier and within 40 ms, the most recent at-or-before `carControl` and `carState` samples must each be within 80 ms, and the event must have active lateral control with finite, valid request and sensor readings.

For each previously unmatched output change it retains the previous native output and historical source limit formula, but tests up to three *earlier*, distinctly timestamped source observations from each input stream (all at-or-before the observed output and no older than 80 ms). Alternatives are counted in exclusive priority order: older `carControl` only, older `carState` only, both, neither. This is a sensitivity experiment, **not** a reconstruction of the controller's actual scheduling.

## Private-drive numerical result

| Metric | Samples |
|---|---:|
| Recorded native output records, selected window | 700 |
| Held output records | 416 |
| Changed output records | 284 |
| Changed, eligible active-control records | 283 |
| Baseline hypothetical replay matches within ±1 native unit | **261 / 283** |
| Baseline unmatched | **22 / 283** |
| Unmatched explained by older preceding `carControl` alone (bounded retrospective fit) | **22 / 22** |
| Unmatched requiring older `carState` alone or both | **0 / 22** |
| Unmatched not explained by those preceding alternatives | **0 / 22** |
| Baseline unmatched at normalized request ceiling | **0 / 22** |
| Absolute baseline discrepancy >1 to 6 native units | 14 |
| Absolute baseline discrepancy 7 to 20 native units | 8 |
| Largest absolute baseline discrepancy | **12 native units** |

All latest control samples used by the initial replay were recorded less than 10 ms before the selected output changes, including the 22 mismatches. Of those 22 mismatches, the latest EPS/state sample was less than 10 ms old in 19 cases and 10–30 ms old in 3. These ages describe recorded sampling timestamps, not necessarily internal controller execution order.

**Interpretation:** The entire residual mismatch set is consistent with a possible 50 Hz controller/data-publication phase difference in this particular window. Because many earlier requested values can produce the same rate-limited command, these matches could also be coincidences from retrospective searching. They do **not** prove that the installed controller used an older request, that the historical constants match its build, that the EPS accepted a transmitted command, or that software steering capacity should be increased. The earlier 85/85 capped-demand transition matches remain a separate observation.

## Tests and repeatability

- GitHub code: `ram1500-development/torque_replay_phase_audit.py` at `32a0fa1c`.
- Eight synthetic privacy, causality, source-age and replay regression tests: `test_torque_replay_phase_audit.py` at `e418fd2c`.
- Private-probe integration: `6460fef3`.
- Pinned Docker build/test gate: `312a209f`.
- Railway Docker build passed **29 targeted tests**: 7 private-probe + 6 torque-cap + 8 original replay + 8 phase-audit fixtures. Deployment succeeded and produced the above real-drive private aggregates. Full repository-wide GitHub CI is **not independently confirmed** by this report.

## What remains before any installable candidate

1. Compare historical `CarController`, limit constants, call order and effective installed runtime to the exact on-device code before deriving a behavioral fix. The installed working tree previously reported dirty.
2. Verify and model actual steering-controller 50 Hz update phase, LKAS latch, CAN transmit versus logged `carOutput`, and EPS reception with passive, authorized tests.
3. Analyze the full private route for low-speed `latActive` engagement, firmware authorization and OEM parking-assist versus LKAS semantics. Do not bypass EPS or Panda safety rules.
4. Keep planned route-guided driving in research/simulation until steering, braking, planner fallback and safety handling are independently validated.
5. Complete code review, full CI, reproducible Comma 3 build and qualified controlled hardware testing before issuing an installer address.

No steering software, torque limit, ECU/Panda safety configuration, or vehicle software was modified.
