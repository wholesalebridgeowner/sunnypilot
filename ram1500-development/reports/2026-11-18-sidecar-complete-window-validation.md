# November 18, 2026 — full-window integrity for parked diagnostic resource evidence

**Status: source review and synthetic offline tests only. No parked-device rerun, CAN access, or installation.**

## Audited source and what was wrong

This independent patch is stacked on draft PR #15's read-only `sidecar_health_gate.py` (not on PR #1's on-device logger). The exact current diagnostic source is `ram1500-development/device_telemetry.py` on `ram1500-steering-forensics-20261117` (draft PR #1), which defines `HEALTH_INTERVAL_S = 10.0`. It produces periodic `sidecar_health` records with one 10-second `windowCpuPercentOneCore` each. In `finally`, it emits a **terminal unwindowed health record** (`windowCpuPercentOneCore=None`) immediately before `sidecar_stop`. `wallElapsedS` is rounded to hundredths of a second, so the terminal and preceding periodic timestamps can be equal without implying a lost sample.

The earlier independent assessment accepted a sample count and maximum intersample interval but did not require an **early first health sample**. Eleven or twelve records compressed into the last 20 seconds of a nominal 120-second session could falsely appear sufficiently sampled. It also allowed a missing *interior* CPU observation if enough other windows remained. These gaps could conceal CPU spikes precisely where the Nov 17 parked hardware pilot had exceeded the 15%-of-one-core criterion. Neither bug establishes actual hardware instability: they are deficiencies in assessing the completeness of the evidence.

## Narrow offline correction

- A first periodic health timestamp must be **strictly positive and no later than 20 seconds** into the session. This conservative allowance equals two nominal reporting intervals.
- Every periodic health row must have a finite, nonnegative windowed CPU measurement. An absent, `NaN` or invalid sample fails the metric gate **even when enough other samples exist**. The final health row is required to be explicitly unwindowed (None), matching the inspected source contract.
- Periodic health timestamps must strictly increase. The sole permitted equality is a terminal unwindowed record sharing its rounded timestamp with the last periodic row; clock reversal remains invalid.
- Existing overall duration, >20-second periodic gaps, single-session, CPU threshold, split resident memory, reader-count, and normal-stop requirements remain in force. The output remains only `metrics_pass_other_gates_pending`, never a vehicle safety approval.
- Five additional synthetic regression tests cover late clustered observations, one missing interior CPU window, present-but-populated or missing terminal CPU fields, and equal terminal rounded timestamps. No driving signals, raw CAN, identifiers or positions are used.

## Provenance / execution

- Existing PR #15 baseline: `ram1500-sidecar-health-independent-gate-20261118`, with 14 existing synthetic tests passing before this patch.
- Proposed isolated follow-up: `ram1500-sidecar-health-coverage-20261118`, based on PR #15; **do not merge either PR into the active driving branch without review**.
- Code commit: `489228720db18109a511b3d5224879e75e01e484`.
- Regression commits: `0be21dd3def6a59f20481b5375ca59dc3f9827ff`, `3e36cb775a67dcf82797f1ef4b154a4382dfa6d5`.
- Final explicit terminal-field schema correction: `df4e19d251370a5bbb748a1520512df92b5496c2`.
- Isolated CI workflow commit: `21adc850a7e79a70b7b2e6627ffe38d63af5f5fc`.
- Test command: `python -m unittest discover -s ram1500-development -p 'test_sidecar_health_gate.py' -v`. Report the GitHub Actions outcome separately; a committed script does not prove it passed.

## Hardware blocker and boundaries

The original Nov 17 **parked-device** pilot measured ~21.6% CPU on one core against the configured 15% criterion; RSS 29.6→53.9 MiB combined private and shared pages. The separate private-vs-shared memory instrumentation was added afterward but **has not been measured on the Comma**. This patch neither changes that historical failure nor establishes memory stability. A new stationary, operator-authorized test with logger/panda/controls process checks is still required. No activation of the diagnostic tool or vehicle software is authorized by this evidence check.

Nov 14 +171 s high-resolution R-log remains absent from the checked Railway bucket and current Comma metadata; Nov 17 native torque cap explanation and 14.6-second factory EPS auto-park status remain observational. Factory ACC actuation attribution, true lane boundary crossing, PAM message identity, EPS firmware-specific permission, full build reproduction and independent release safety gates remain unresolved. The vehicle's ~32 mph restriction is for **initial Sunnypilot engagement**, not a claim that steering cannot continue below that speed after engaging.

**Privacy and safety:** No raw logs, VIN, locations, CAN frames, device changes, active vehicle controls, OEM authorization, Panda settings or installer links are involved.
