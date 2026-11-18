# Ram 1500 custom-driving development

Status: **development only, not installable or validated for road use**.

## Vehicle and software baseline
- RAM_1500_5TH_GEN, vehicle model identified in Comma telemetry.
- Comma 3 / tici, Sunnypilot staging-tici version 0.10.1, observed source commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (dirty working tree reported).
- Baseline vehicle software is never modified by this branch until validation is completed.
- Current development branch: `ram1500-custom-development`.

## Findings from recorded driving data
- Route `000003cc--27a46b2116`, November 14, 2026, reported 50.7 miles.
- All 51 Q-log segments read and framed using the cereal Cap'n Proto schema at the observed commit.
- 728,719 messages, 51 message kinds, 30,000 `carState` and 30,000 `carControl` records, 30,000 `controlsState` records.
- `steerSaturated` event approximately 171 seconds into the log (~6:32:44 PM Central).
- `canError` events near the end of this recorded route; the event count represents samples, not unique failures.
- These are observational data. No numerical commanded/actual torque comparison or root cause established.

## Engineering deliverables / acceptance gates

1. **Reproducible log decoder:** decode entire Q-logs/R-logs, schema-version pinned; produce time-aligned vehicle, command, radar, camera, model, driver-monitoring, and fault streams. Export summary and machine-readable timelines. Verify units, field definitions, sample timing, and missing data.
2. **Vehicle interface audit:** inspect RAM_1500_5TH_GEN interface/controller, DBC messages, EPS behavior, permitted actuation and stop-to-steer operation. Self-parking proves low-speed steering actuator capacity, not that the openpilot LKAS authorization path operates at zero speed.
3. **Driving behavior replay:** replay captured telemetry into the unmodified baseline, establish lateral error and event distributions. Inspect segment 2 near +171 seconds.
4. **Design proposals and tests:** evaluate control improvements using deterministic replay and unit tests. Keep safety and actuator constraints; do not infer successful behavior from simulation alone.
5. **Zero-speed engagement and destination navigation:** feature exploration separated from baseline. Identify whether supported vehicle integration and reliable maneuver planning are feasible. Do not bypass ECU protections or activate unvalidated behavior.
6. **Reproducible build:** pinned dependencies, CI checks, and release documentation for the exact Comma 3 hardware. Build must be validated before any installer address is issued.
7. **Release acceptance:** independent code review, documented fault handling, regression testing, and controlled testing by qualified operator. Explicitly mark candidate builds *not for public road use* until validated.

## Workflow
ChatGPT and Claude may develop and review patches, but tests and measured telemetry decide whether to retain a change. Prefer a private destination repository for future development; the initial branch is in the existing public repository. Do not publish vehicle identifiers or private logs in this repo.

## Current status
Branch and plan created. No rebuilt control software, signed image, install URL, or validated zero-speed/end-to-end-navigation capability exists yet.

## Most recent development work (November 17, 2026)

**Continuation branch:** `ram1500-steering-forensics-20261117` — see [draft PR #1](https://github.com/wholesalebridgeowner/sunnypilot/pull/1). This contains the latest steering/following-distance/throttle/lane-change analysis scripts, passive Comma/Panda CAN sidecar, CI tests, reports, and the complete Windows Claude SSH handoff.

**Claude after restarting Windows:** open [CLAUDE_RESUME_HANDOFF.md on the continuation branch](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/CLAUDE_RESUME_HANDOFF.md), review [DEVICE_DIAGNOSTICS.md](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/DEVICE_DIAGNOSTICS.md), and check out that branch. The test suite passed on the continuation branch prior to the documentation-only handoff. Its tools are **not** installed on the Comma; remote SSH is not yet configured. No active driving code or safety limits have been changed.

## Verified November 18, 2026 continuation milestone

The maintained **read-only research changes remain on** `ram1500-steering-forensics-20261117`, not the parked Comma or this baseline branch. See [bounded steering replay phase-sensitivity analysis](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/2026-11-18-torque-replay-phase-sensitivity.md) and [draft PR #1](https://github.com/wholesalebridgeowner/sunnypilot/pull/1). The private Railway R-log probe, pinned to code commit `312a209f9dd8608e73b0b2c1b7d7f6457771a03f`, passed 29 targeted container fixtures and produced numerical aggregates: 261/283 changed native steering outputs matched historical-source replay and all 22 baseline mismatches could also match with an older, still causal `carControl` sample under bounded retrospective analysis. This is **not proof** of actual controller update phase or EPS command acceptance. Historical limits are not yet proven identical to the dirty on-device working tree. The project is not installable or hardware validated.
