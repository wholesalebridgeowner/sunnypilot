# 2026-11-18 — parked sidecar representative-workload and CPU-window QA

**Status: offline source inspection and synthetic regressions only. Not hardware validation, installation approval, or evidence of a road-safe control stack.**

## Scope and source pin

This study builds narrowly on draft PR #17 (head `db9ba44f8c72d01bc0169b868528819e6f493c5f`), stacked on PR #16 and PR #15, and stays isolated from `ram1500-custom-development`, Claude's steering/PAM work on PR #1, and the pinned ACC/comfort work on PR #2. It reads only anonymous `sidecar_start`, `sidecar_health` and `sidecar_stop` summaries; it does not capture or export CAN.

Reviewed passive logger source: `ram1500-steering-forensics-20261117`, commit `8c795c0d250af45b00a7c56788768998ee366dc5`, `ram1500-development/device_telemetry.py`, especially `Capture.handle` (sample-rate decimation; increment successfully emitted message counters), `health_row` (per-window process CPU and cumulative counts) and `run` (10-second scheduled health with `next_health += HEALTH_INTERVAL_S`). Recorded driving baseline remains independently pinned to Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, with dirty-tree caveat. The logger is not installed or certified on the truck.

## Two remaining evidence-quality gaps identified

1. The previous resource QA already rejected *missing* timed CPU observations and large time gaps. However, an overdue health emission followed by a catch-up emission only one second later could both be counted as complete, independent CPU evidence windows. The logger's fixed 10-second scheduler can generate such closely spaced observations when work stalls. A one-second CPU average cannot substitute for a normal full measurement interval.
2. The earlier liveness gate required cumulative `can` and `carState` to increase on each periodic observation. It would still pass **one** carState row every 10 seconds when `sampleHz=2` requests approximately 20 rows per 10 seconds. That extremely underloaded capture could make CPU use appear artificially low and should not establish representative overhead.

## Narrow change

- Require each *periodic* health CPU window, including the first from capture start, to span **at least 5 seconds**. Keep existing 20-second maximum gap, 10-second intended cadence and separate unwindowed terminal health exception. A catch-up burst now fails `cpu_window_duration_too_short` rather than inflating window coverage.
- Require that each period contain at least **50% of the requested carState 1–5 Hz sample rate**, computed against the actual elapsed window duration from anonymized cumulative rows. This is intentionally a *sufficiency gate for representative CPU data*, not an assertion of expected CAN traffic, car functionality, or device failure. A quiet, sleeping truck can fail legitimately.
- Require `sampleHz` to be an actual integer 1–5; Python boolean `True` no longer masquerades as integer one.
- Add aggregate `requestedRateEvidenceConfirmed` flag and generic `carstate_rate_not_representative` reason. No private timestamps, raw counters, payloads, route keys, vehicle identifiers or network details enter GitHub or the aggregate output. No control logic or device software was changed.

## Reproducibility

Command: `python -m unittest discover -s ram1500-development -p 'test_sidecar_health_gate.py' -v`.

Six new synthetic test cases exercise nominal 2 Hz stream, one-row-per-window false pass, a single underloaded interval, compressed CPU window, boolean sample rate, and a legitimate 1 Hz workload. Existing 26 regressions remain present (32 tests expected). Run the dedicated GitHub Action `Ram parked sidecar representative-load evidence tests` on this branch, and record actual pass/fail separately: **a committed test is not a verified passing test**.

## Limits and release gates

The old operator-approved parked G3 measurement **21.6% one-core CPU** exceeded the 15% acceptance threshold; this code does **not** improve it or verify the later CPU optimization. Re-test only while parked and operator-approved, with CPU, private/shared RSS, msgq reader stability, actual decode counts, and driver-control daemon stability observed together. Never use an assessor's `metrics_pass_other_gates_pending` to authorize on-road deployment.

Nov 14 +171 s steering saturation still has no matching high-resolution R-log in the currently inventoried private Railway bucket or Comma metadata. Nov 17 segment 30 cap investigation and factory ACC smoothness remain observational. November 18 `EPS_2.AUTO_PARK_HAS_CONTROL_2` remained true for 1,460 samples (~14.6 s) under factory parking, while Comma lateral commands were zero; no PAM command ID, third-party steering permission, low-speed LKAS authorization, or navigation release has been established. The 32 mph threshold is only initial Sunnypilot engagement. Keep the vehicle, installed software, Panda and OEM safety conditions unchanged.
