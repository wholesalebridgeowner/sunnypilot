# November 18, 2026 — parked sidecar liveness and decoder integrity guard

**Scope:** Offline inspection of anonymized health-summary rows only. No truck/device execution, CAN injection, raw R-log processing, or controller changes. This draft is stacked on PR #16 (which is stacked on PR #15), isolated from the original development branch and Claude's steering/PAM and ACC branches.

## Defect discovered in prior resource-only assessor

A diagnostic sidecar that stops decoding messages can consume very little CPU and pass the old CPU/RSS/reader-count checks. The existing offline assessment on PR #16 did not inspect the **cumulative `rows` counters** that the current source emits in every periodic and terminal `sidecar_health` record. Source reviewed: `ram1500-development/device_telemetry.py` on draft PR #1, at commit `8c795c0d250af45b00a7c56788768998ee366dc5`: `Capture.handle` increments counts for successfully processed message types, increments `decode_errors` when decoding fails, and `health_row` copies those counters. Device software is not modified by this PR.

An apparent 8%-CPU resource pass with frozen CAN/carState counters or accumulating decode errors does **not** establish a functioning representative telemetry workload. This is an **offline evidence-quality defect**, not a verified current failure on the truck.

## Narrow change to the existing private-data-safe assessor

- Require every `sidecar_health` row to include a dictionary of cumulative nonnegative integer counts limited to the known source message types plus `decode_errors`. Invalid, missing, boolean, negative, or unknown entries fail closed.
- Reject counter regression between any adjacent health observations.
- Require `can` and `carState` counters to be positive in the first 10-second health window and to increase between every later **periodic** health record. This is a strict *representative parked data-flow sufficiency* check; a legitimately asleep or CAN-silent device can fail it without implying hardware malfunction.
- Reject **any** accumulated decoder error; report separately whether the counter evidence is decoder-error-free and continuously progressing. The final unwindowed health record is not required to increase its counters relative to the last periodic record, because the last two may share an identical rounded timestamp.
- Keep no route, VIN, GPS, CAN payload, message bytes, file paths, timestamps of private drives, or raw counter key/value series in GitHub or assessor output. Results include only aggregate booleans and generic reason codes.
- The result still only says `metrics_pass_other_gates_pending` when all offline checks pass; it is never device fitness, safety approval, or installation authorization.

## Reproduction and validation

`python -m unittest discover -s ram1500-development -p 'test_sidecar_health_gate.py' -v`

Seven new fully synthetic regressions cover fully idle sidecar, a partial freeze, decode errors despite low measured CPU, missing/malformed/unknown counters, rollback, legitimate repeated terminal counters, and rejection without echoing sensitive extra input. There are **26 expected tests** including the 19 inherited tests. Record actual GitHub CI outcome separately; test code alone does not establish success.

## Relationship to real evidence and blockers

The parked-device G3 measurement from November 17 still showed **21.6% of one CPU core** against the 15% criterion, with combined RSS increasing from 29.6 MiB to 53.9 MiB. This validation patch does not cure or reinterpret those measurements. The revised private/shared memory instrumentation and later polling changes still need a fresh **operator-approved parked** diagnostic test to verify both resource overhead and real source streaming, process stability and log completeness.

The November 14 +171-second high-resolution R-log is absent from both the listed private Railway bucket and present Comma route-file metadata; 51 Q-logs remain previously decoded. The November 17 segment 30 R-log exists, but steering-rate-cap inference remains observational rather than controller-phase proof. The November 18 EPS factory auto-parking status bit was true for 1,460 observed 100 Hz EPS frames (~14.6 s), while Comma lateral control was inactive; PAM→EPS command ownership and third-party authorization remain unverified. The vehicle's 32 mph condition is **initial Sunnypilot engagement**, not proof of an ongoing steering cutoff below 32 mph.

**Release:** research-only, no merged branch, no device changes, no novel car-control code and no installer link.
