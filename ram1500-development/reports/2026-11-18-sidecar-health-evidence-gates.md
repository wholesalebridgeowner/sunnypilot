# November 18, 2026 — independent Comma 3 parked-sidecar resource evidence gate

**Status: read-only, synthetic-tested source only. Not rerun on Comma, not installed, not a vehicle-control candidate.**

## Evidence and immediate blocker

- The original parked, operator-approved G3 capture on November 17 completed for 120 seconds without reported controls faults or logger process restarts, but **failed its 15% single-core CPU threshold**: observed steady CPU was **21.6% of one core**. The watchdog monitored the timeout wrapper rather than the Python subprocess, so the sidecar's own rows provided the actual measurement.
- Reported process RSS increased **29.6 to 53.9 MB over two minutes**. That original figure combined shared-memory pages and private anonymous RSS. The trend **cannot establish a private Python memory leak**.
- Subsequent changes on PR #1 separated `rssAnonKiB` from `rssShmemKiB`, reduced polling and parsing overhead, and added CPU/private-memory self-limits. Off-device benchmarks suggested lower overhead, but **no comparable parked hardware rerun has validated those fixes**.
- Source: [PR #1 DEVICE_DIAGNOSTICS.md](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/DEVICE_DIAGNOSTICS.md), [device_telemetry.py](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/device_telemetry.py). A later device run must use that current, independently reviewed source under operator approval; the present tool does not run on the device.

## New independent, privacy-preserving offline gate

`sidecar_health_gate.py` accepts **only** privately extracted `sidecar_start`, `sidecar_health` and `sidecar_stop` JSONL rows from a single parked diagnostic session. It does **not** open raw R-logs, parse CAN, connect to the truck, transmit CAN, copy telemetry, or return any input identifiers/paths. Any CAN or unrecognized data record causes rejection. Only non-identifying scalar metrics and fixed reason codes are emitted.

It checks: a normal completion; approximately 120 s elapsed; health-clock order and sampling gaps; enough 10 s CPU windows; **all observed** CPU windows at or under the 15% one-core pilot threshold; private anonymous memory at or under 150 MiB; and before/after reader counts increasing by exactly one without reaching the historical 15-reader rollover in the precheck. It rejects moving / all-CAN capture modes and unreviewed sampling rates. `RssShmem` growth is displayed separately and **never treated as anonymous Python heap growth**.

A successful numerical gate is reported only as `metrics_pass_other_gates_pending` — not a complete pilot pass. The output specifically excludes independent verification of Park/ignition state, CAN validity/alerts, `pandad`/`card`/`controlsd`/`loggerd` PID stability, and device/build/safety authorization.

## Reproduction

With **only the three health record types already extracted on the user's private workstation**, never a raw CAN capture:

```bash
python ram1500-development/sidecar_health_gate.py /private/offrepo/parked-health-only.jsonl --expected-seconds 120
python -m unittest discover -s ram1500-development -p 'test_sidecar_health_gate.py' -v
```

No private health or CAN records belong in GitHub. Fourteen synthetic fixtures cover CPU spikes, private/shared memory distinction, missing metrics, interrupted sessions, subscriber eviction, clock errors, multiple-session contamination, raw-frame rejection and malformed thresholds.

## Why this advances the release investigation

It gives a separate, repeatable validation gate for the known **failed real-device resource criterion** without launching any process on the Comma. The next hardware evidence still requires an operator-approved, safely parked **single run after reboot** with active watchdog observation; `carState` had one free subscriber slot and a second run without reboot could evict critical subscribers. No unattended run is justified.

Other release blockers are unchanged: November 14 +171 s R-log not present in the checked private bucket or current Comma route-file metadata; November 17 steering-cap phase remains observational; OEM factory auto-park mode was recorded for one ~14.6 s episode without Comma lateral command, but PAM→EPS command identity/third-party authorization remains unestablished; factory ACC throttle/brake actuation, actual lane crossing, source-specific build reproduction and independent road-safety review remain unresolved.

**No modifications to `ram1500-custom-development`, PR #1/2, Railway collector, Panda, EPS, running software, or public raw-log disclosure.**
