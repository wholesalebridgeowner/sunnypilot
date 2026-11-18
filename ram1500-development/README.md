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

## Offline analysis tools (read-only)
Requires Python 3.11+ with `pycapnp` and `zstandard` (`pip install pycapnp zstandard`). Nothing here talks to a vehicle.
Outputs are derived from private driving data and must stay outside this repository (the tool enforces it).

```
python ram1500-development/investigate_route.py --fetch-schema --schema-dir ~/.cache/ram1500/schema-1f7233cb --out ~/ram-analysis/<route> <log dir>/<route>--*/qlog.zst
```

- `--fetch-schema` downloads the five Cap'n Proto files of the drive build `1f7233cb` and verifies their git blob SHA-1s.
- Output: `summary.json`, `findings.md`, `episodes.csv`, `faults.csv`, `steerSaturated_<n>_pm15s.csv` / `_pm30s.csv` (+ units).
- `analyze_route.py --schema-dir ...` gives the quick counts/episodes summary.
- Tests: `cd ram1500-development && python -m pytest` (set `RAM1500_SCHEMA_DIR` to the schema dir to include the Cap'n Proto end-to-end test).
- Do not load the pinned schema and this branch's openpilot/opendbc in the same Python process; pycapnp aborts on the duplicate schema ids.

## Current status
Branch and plan created. No rebuilt control software, signed image, install URL, or validated zero-speed/end-to-end-navigation capability exists yet.
Claude round-1 review and offline tooling: `reports/2026-11-17-claude-independent-review.md`, `REVIEW_LOG.md`.
