# November 18, 2026 — causal replay of the November 17 steering torque-cap cluster

**Status:** real-drive observational diagnostics only, not a validated steering-control change or Comma installer.

## Private-data provenance

- Railway private one-shot analysis service: `ram-private-rlog-probe`.
- Deployment: `809e0807-bec2-4a5d-bec9-1858ff1624eb`, **SUCCESS**, pinned source commit `cf3226a30fc9d71565b4dd9d1c82ece22a91eed3`.
- Pinned installed-device Cap'n Proto schema: `sunnypilot/sunnypilot@1f7233cb983f3c13fdbba47a86f8fbc796ad9211`.
- Historical Chrysler model used for **hypothetical** actuation replay: `sunnypilot/opendbc@df807f8be394231b2366a1ecd9c9265ab51d2400`, `opendbc/car/chrysler/carcontroller.py`, `values.py` and `opendbc/car/lateral.py`. This revision is NOT proven identical to the installed runtime or its private working tree.
- Private November 17 route, four R-log segments (0, 29, 30, 31); 421,253 decoded messages in selected segments. Only aggregate, anonymized statistics were emitted.
- Focus: route-relative 1800–1807 s, a **7-second** interval. Do not generalize this window to the full drive.

## From independent streams to aligned, candidate command replay

The independent per-stream audit found 219 of 700 controller/request records at abs(normalized torque) ≥ 0.995, while native CAN-output command peaked at 280 rather than the software-source reference 350; no event or controller `saturated` flag was recorded in this interval.

The new `torque_command_replay.py` takes the 100-Hz `carOutput` stream and **only scores changes** in its recorded native command to avoid counting holds as fresh 50-Hz steering-controller updates. It aligns each changed output with most recent at-or-before `carControl` and `carState` (at most 80 ms old), and with the previous native output recorded within 40 ms. The model calculates the source's proposed torque (`round(request * 350)`), the EPS-measurement-relative software clip (±80 native units around the observed EPS reading), and the ±6-native-unit per-update rate clip; it then compares its *hypothetical active-LKAS output* to the next observed native command with ±1 tolerance.

### Verified counts for the selected seven seconds

| Observation | Samples |
|---|---:|
| Total native `carOutput` samples | **700** |
| Held (unchanged) native output samples | **416** |
| Changed native output samples | **284** |
| Changed samples with `carControl.latActive=true` | **283** |
| Changed, active samples with usable prior inputs | **283** |
| Historical-source replay matches within 1 native unit | **261 / 283** |
| Not matched (phase/gating/other uncertain) | **22 / 283** |
| Replay model invokes measured-EPS-relative software clipping | **195 / 283** |
| Replay model invokes per-update rate clipping | **221 / 283** |
| Changed active samples with abs(normalized request) ≥ 0.995 | **85** |
| **Those 85 capped changed outputs matched historical replay** | **85 / 85** |
| Capped changed outputs not matched | **0 / 85** |
| Inactive/unknown `latActive` changed outputs | **1** |

Counts of rate and EPS-relative clipping **overlap**. They are not mutually exclusive causal explanations. The 85 capped changed transitions are a subset of the 219 capped source samples from the earlier independent-stream count.

### Interpretation

The recorded maximum native command (280) can be explained **plausibly by the existing source-level software torque limiting path**, rather than assuming the EPS has a physical maximum of 280 or that openpilot issued 350 native units. The modeled historical clip-and-rate behavior matched every tested command *change* occurring with a capped normalized request. The other 22 unmatched transitions have not been explained and may reflect asynchronous field timing, 50-Hz update phase, or LKAS gating. The result supports an engineering hypothesis, **not a proven runtime reconstruction or ECU acceptance measurement**.

The historical model's numerical comparison with `steeringTorqueEps` is part of **the verified source algorithm**, not an assertion that EPS motor feedback equals physical steering wheel/rack torque or that native CAN command equals measured motor torque.

**Unobserved:** controller's internal `self.apply_torque_last` update phase, LKAS control bit/latch, precise sent-message receipt by the EPS, model/radar and lateral safety decisions, steering wheel movement attributable to commanded torque, and full-route exposure. The replay uses preceding samples no older than its age limits; near-simultaneous asynchronous events can still be incorrectly paired. This report does not establish zero-speed actuation or navigation feasibility.

## Tests and safety

The private Docker build passed its 7 route-probe fixtures, 6 torque-limit fixtures and **8 causal replay synthetic tests** (21 tests total), and deployment completed successfully. The latest full GitHub Actions workflow is a separate validation requirement.

The new Python module is read-only and emits only counters, parameters and caveats. No raw route data, VIN, GPS, CAN frames, timestamps or identifiers were published. No steering-control changes, ECU/Panda safety modifications or installation occurred.

## Next controlled research gates

1. Capture device-build-matched `CarController` and `CarControllerParams` code/version without sending control CAN messages. Validate whether the historical `STEER_MAX=350`, `STEER_ERROR_MAX=80`, and `STEER_DELTA_UP/DOWN=6` values are identical to the actual installed runtime.
2. Diagnose the 22 unmatched transitions using a private, read-only timing-phase audit (aggregate only; avoid overfitting timing thresholds).
3. Independently verify low-speed firmware authorization and active-lateral telemetry in full-route data; distinguish OEM park-assist from LKAS.
4. Preserve all actuation gates; require regression, simulation and qualified independent vehicle-hardware validation before any installer URL or steering-controller change.
