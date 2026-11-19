# 2026-11-19 — independent ACC-motion radar timestamp integrity, private retained-morning replay

**VERIFIED OFFLINE / RESEARCH ONLY. NOT A CALIBRATION OR RELEASE.**

## Defect and scope

The existing source-pinned observed longitudinal acceleration/jerk analyzer used a past-only same-segment radar as-of join, but equal-time `radarState` observations were previously selected in input order. Contradictory lead status, source flag, relative distance, relative speed, or validity at one timestamp could therefore produce a non-reproducible lead-context label and episode continuity. The separate following-distance observer already had a timestamp-conflict gate; this patch extends the principle to **the distinct observed-motion observer**, not to stock OEM ACC control.

Each segment now coalesces exact duplicate radar records, counts repeated rows, and preserves a **fail-closed unavailable timestamp** for conflicting simultaneous records. An ambiguous newer timestamp cannot silently fall back to a previously accepted lead. Unknown source flags remain explicitly unknown. Zero-order insertion and input-order replays are covered by anonymous synthetic tests.

## Source-pinned code, tests and independently executed validation

- Parent: draft PR #26 `ram1500-comfort-retained20-20261119`, exact parent HEAD `fb83bd36fa9db36b7175c9b99de167a62119852f`.
- Source fix: `57ac8266537fbf90d9a0f0935bbaae3a928cbb9b` in `ramtools/acc_motion_integrity.py`.
- Synthetic tests: `c5df792810e760ba44f6feb85eae2b643dc05366`; four new cases (exact duplicate, order-reversed source conflict, validity conflict, relative-velocity conflict).
- Dedicated CI workflow and exact **private validation source/test** commit: `4b6993c7970f93ebb74b4bb03fd77205a4118293`.
- GitHub Actions run **44516562159**: `Ram ACC motion equal-time radar integrity`, **completed SUCCESS**. Existing pinned comfort workflow run **44516562089**: **completed SUCCESS** on the same exact commit. These are fixture checks, not physical or replay safety tests.
- Existing isolated Railway research service `ram-private-comfort-once` repinned to that exact commit; deployment **`df05dd8d-f508-4a20-91fa-70e49497c043` SUCCESS**. Docker build passed **165/165 pytest tests** (formerly 161 on parent). No source, image, or data from the truck was modified.
- Private runtime redecoded **20/20 full retained R-logs**, **20/20 exact recorded-build commit headers**, **2,005,758** messages, **0** truncated files. This establishes recorded **Git source header** equality, not equality to a potentially dirty on-device executable.

## Observed effect in the retained recording

| Aggregate only | New guarded analyzer | Previously verified parent |
|---|---:|---:|
| Radar observations read | 22,815 | 22,815 |
| Repeated equal-time radar rows | **0** | Not counted |
| Ambiguous equal-time radar timestamp groups | **0** | Not counted |
| Qualified observed-motion episodes | **6** | 6 |
| Qualified motion exposure | **86.741 s** | 86.741 s |
| Acceleration observations, model-flagged lead | **8,680** | 8,680 |
| P90 absolute observed acceleration | **1.163 m/s²** | 1.163 m/s² |
| P90 half-second-windowed derivative proxy | **0.563 m/s³** | 0.563 m/s³ |
| Qualifying following-distance episodes | **6** | 6 |
| Qualified following exposure | **86.444 s** | 86.444 s |
| Median sampled observed following gap | **2.259 s** | 2.259 s |

The original retained subset happens not to contain any same-time radar collision under this detector, so no numerical aggregate changed. The regression tests prove that **future** contradictory same-time data are no longer silently treated as valid lead context. This change does **not** establish factory ACC's programmed gap, radar ECU authority, throttle/brake commands or a vehicle comfort rating.

## Remaining 11-step gates and precautions

Missing high-resolution November 14 +171s steering saturation evidence, phase-ambiguous November 17 segment 30 torque cap, non-independent held-out lane tracking/stability, lane-change-positive physically verified crossing, actual OEM factory ACC actuation, confirmed PAM-to-EPS authorization, and separately approved **parked** logger CPU/RSS retest still prevent calibration or installation release. The earlier parked CPU result was 21.6% of a core against a 15% gate. The factory Nov 18 auto-park EPS-status episode (about 14.6s) is read-only OEM behavior, not a third-party steering permission.

No private raw logs, vehicle identifiers, keys, per-sample timestamps, CAN payloads, installed software, ECU/Panda/OEM permissions or production collection changes were made. This change is confined to an **offline** research branch, fixture CI and the isolated one-shot Railway research replay. Original `ram1500-custom-development` and the Claude branches are untouched.
