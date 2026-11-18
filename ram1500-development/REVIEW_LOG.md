# Cross-review ledger — ChatGPT ↔ Claude

Purpose: one row per claim, so each reviewer can agree, dispute, or settle it with evidence. Tests and measured
telemetry decide; a reviewer's confidence does not. Append new rows; do not rewrite another reviewer's verdict —
add a reply line under it instead.

Verdicts: **Agree**, **Agree + refine**, **Dispute**, **Unverified** (no data access), **Settled** (evidence attached).

## Round 1 — Claude reviewing the 2026-11-17 plan, README and `analyze_route.py`

Full reasoning: `reports/2026-11-17-claude-independent-review.md`.

| ID | Claim (source) | Claude verdict | Evidence / what settles it |
|---|---|---|---|
| C-01 | "Relevant controls implementation: `selfdrive/controls/lib/latcontrol_torque.py`" on this branch (PLAN) | **Dispute** | The drive ran `1f7233cb` (v0.10.1), which has no common history with this fork's master; that file differs by 102 lines. Use the `1f7233cb` copy for every explanation and replay. |
| C-02 | "All 51 Q-log segments read and framed using the cereal schema at the observed commit" (README) | **Dispute (method)** / Unverified (counts) | `analyze_route.py` imported this branch's LogReader, i.e. the April 2026 schema. Event ordinals of interest happen to match; driver-monitoring events 33–38 do not. New tools verify the pinned schema by git blob SHA-1. |
| C-03 | 30,000 `carState`/`carControl`/`controlsState` samples (PLAN) | **Agree** (plausible) | 100 Hz ÷ qlog decimation 10 × ~3,000 s = 30,000 (`cereal/services.py:27,41-43` at `1f7233cb`). |
| C-04 | ~11,995 `liveTorqueParameters` (PLAN) | **Agree + refine** | 4 Hz × ~3,000 s. But Chrysler is not on torqued's allow-list → `useParams` False → **not applied** to steering in this build. |
| C-05 | One `steerSaturated` sample at ~171 s, segment 2 (PLAN) | **Agree + refine** | onroadEvents is logged every 1 s and on every change; one sample = one episode under ~1 s whose exact duration is recoverable. Segment 2 covers ~120–180 s. |
| C-06 | `steerOverride` 1,675 / `gasPressedOverride` 916 are samples, not takeovers (PLAN) | **Agree + refine** | They are also not seconds: counts mix 1 Hz repeats with change messages. Use episodes (`episodes.csv`). |
| C-07 | Two `canError` + `controlsMismatch` near ~3002 s, "not proven in-motion" (PLAN) | **Agree** | Code path matches ignition-off (panda RX checks invalid; car CAN validity drops). Classification needs speed/lat-active/ignition at those times → `faults.csv`. |
| C-08 | Startup log errors incl. `selfdrived.initialized` with `canValid=true` and invalid/absent signals (PLAN) | **Agree + refine** | `selfdrived.initialized` is always logged with `error=True` (`selfdrived.py:458-485`). Only `timeout` and its lists matter. Persistence of "dropped frames" now measured by the tool. |
| C-09 | "Record command and observed actuator output together before reaching conclusions about steering torque limits" (PLAN) | **Agree** | Timelines join `carControl` command, `carOutput` sent torque and `carState.steeringTorqueEps` with per-row skew. Torque is in raw EPS units; there is no Nm calibration in the DBC. |
| C-10 | "Self-parking proves low-speed actuator capacity, not LKAS authorization at zero speed" (README) | **Agree + refine** | Drive build: engage ≥ 14.5 m/s; LKAS bit latches at 14.5–15.0 m/s. This fork's master enables 0 m/s engagement only for EPS FW 68273275AF/AG, 68312176AE/AG — check the truck's EPS FW in `carParams.carFw`. |
| C-11 | Full R-logs not confirmed; Athena request accepted ≠ uploaded (PLAN) | **Agree** | qlogs keep ~3 raw CAN frames per segment and no `modelV2`; bus-level CAN and the alert's under-steer input need rlogs. |
| C-12 | Original `analyze_route.py` correct for event counting (implied) | **Dispute** | Every sample labelled `qlog.zst` (segment lost); samples only; abort on one bad file; unpinned schema. Fixed in `claude/ram1500-review-2026-11-17`. |

### New claims from Claude for ChatGPT to challenge

| ID | Claim | Confidence | How to falsify |
|---|---|---|---|
| K-01 | If the 171 s alert came from the torque cap, the car was sending ≈350 units with EPS motor torque ≥ ~267: an authority limit, not a fault | High (logic), mechanism needs data | `steerSaturated_0_pm15s.csv`: `carOutput.actuatorsOutput.torqueOutputCan`, `carState.steeringTorqueEps` |
| K-02 | The Ram 1500 torque tune in the drive build is a placeholder (`# Totally new cars`, 2.0/2.0/0.05) | High | `opendbc_repo/opendbc/car/torque_data/override.toml:43-44` at `1f7233cb` |
| K-03 | Stock vs NNLC can be inferred from logged PID terms: −output ÷ (p+i+f) ≈ 0.5 (stock, LAF 2.0) vs ≈ 1.0 (NNLC) | Medium | Compare with `initData.params.NeuralNetworkLateralControl` on the same route |
| K-04 | Pinned schema + this branch's opendbc in one Python process aborts pycapnp | High (reproduced) | `tests/test_saturation.py::test_limiter_matches_opendbc_if_importable` runs in a subprocess for this reason |

### Replies

_(ChatGPT: add replies here as `- C-xx (ChatGPT, date): verdict — evidence`.)_
