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


## November 17 continuation: event attribution and RPM/firmware evidence

- In [steering_saturation_report.py](steering_saturation_report.py), the event snapshot now chooses the **latest controller state at or before the event**, never the closer *future* state. This corrects a potential false attribution for the +171s `steerSaturated` alert. Missing/stale controller samples stay `null`. Added 1s pre-event observed controller sample/saturation/command-limit counts and three deterministic fixture tests. No source torque or safety limits changed.
- [RAM DT zero-speed source audit](reports/ram-dt-zero-speed-evidence-2026-11-17.md) documents actual pinned `df807f8be` integration exceptions for selected EPS firmware strings and `0x4FF` fingerprint state. **32 mph is not a universal physical steering limit**; installed firmware eligibility is still unknown. Self-parking doesn't prove an LKAS permission path.
- **Next empirical step:** using the privately stored Q-logs and schema-matched installed build, produce a +171s numeric steering event report, then obtain sanitized EPS firmware/`CarParams` evidence. Do not distribute raw Q-/R-logs, VIN, credentials, or local paths via this public repository.
- **Validation:** GitHub offline fixture test workflow passed on the event attribution change. This is *synthetic validation only* and neither proves cause nor qualifies a software installer.
