# Ram 8:19 PM steering warning — cap-exposure attribution gate (Nov 19 local / Nov 20 UTC)

**Status: source-pinned offline code + synthetic regression validation only. Draft PR #50 is stacked on draft incident PR #48; not a vehicle release or calibration.**

## Incident evidence motivating this change

The owner-reported 8:19 PM CST steering-torque warning was independently matched to a real `steerSaturated` onroad warning at about 8:19:09 PM, with the recorded installed source `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. Existing source-verified private incident analysis (issue #46, PR #48) successfully read five contiguous R-log segments around the warning and reported, within a ±5-second window, 1,000 carState/carControl/carOutput/controlsState samples each; Comma `latActive=true` in 1,000/1,000; controller `saturated=true` in 74/1,000; native `carOutput.actuatorsOutput.torqueOutputCan` absolute p90/max 350, and observed `steeringPressed=true` in 0/1,000. These are recorded *sample* counts, not independently verified CAN transmissions or physical EPS torque delivery.

These are existing findings, **not a new replay**. The newly added exposure observer has not yet been run on those private R-log objects, so no new at-cap duration, root-cause, control-cycle phase, or safety claim is made.

## Independently verified exact original source

Fetched the three source files at the vehicle's recorded Sunnypilot commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, then pinned their Git blob SHA in the dedicated CI verification:

| Installed-build file | Verified blob SHA | Source-level facts |
|---|---|---|
| `opendbc_repo/opendbc/car/chrysler/values.py` | `4a9b6a93cd2563abb4a12d921994e64ece77c72a` | RAM DT `STEER_MAX=350`, `STEER_DELTA_UP=6`, `STEER_DELTA_DOWN=6`, `STEER_ERROR_MAX=80`, `STEER_STEP=2` |
| `opendbc_repo/opendbc/car/chrysler/carcontroller.py` | `0ac0b7e8b4ff5f339e1f9b59ad7957f412a01215` | At a 50 Hz steering update, normalized `CC.actuators.torque` is scaled and passed through measured-torque/rate limiting, and `torqueOutputCan` records the last native applied output. LKAS gating may force zero |
| `opendbc_repo/opendbc/car/lateral.py` | `9d120e308b7f56c49ece55b4a5037ab002717235` | Measured motor-torque/error and delta-up/down rate-limiting function is called by Chrysler controller |

These are historical software reference values, **not proposed tuning changes** and not proof the observed EPS accepted the commanded torque. A dirty running tree remains an independent source-verification caveat.

## Narrow experimental implementation

`incident_cap_exposure.NativeCapExposure` is integrated into the existing read-only `incident_rlog_probe.read_rlog` as a **separate aggregate**, preserving the original warning statistics. The new gate:

- Requires explicit source-verified R-log segment boundaries; clears car/controls/command context on every boundary and rejects clock regressions instead of reordering observations.
- At each source-reported native command sample, examines only independently fresh *past* carState, carControl and controlsState at age 0–30 ms. Requires exact `latActive=true`, `canValid=true`, both EPS faults false, `steeringPressed=false`, and explicitly known controller saturation state. Missing, stale and contradictory states remain unqualified.
- Counts observations at the documented 350 native-unit ceiling, eligible/unqualified observations, controller saturation overlaps, and contiguous *sampled* ceiling runs. A source-reported output above that ceiling rejects the qualified report.
- Emits only anonymized counts and one aggregate longest sampled run span; no VIN, route key, absolute timestamp, camera frame, GPS, ECU payload, or credentials.
- Differentiates repeated high-rate `carOutput` observations from actual 50 Hz CAN send opportunities. A 100 Hz observed output sequence cannot establish independent CAN transmissions, EPS acceptance, physical force, or controller causality.

## Executed tests and remaining gates

- Six new synthetic regression cases cover qualified native-cap observation, inactive/stale/driver-override evidence, segment resets, monotonic-clock inversion, unexpected above-ceiling output and unknown controller state. One additional integration fixture proves the incident R-log summary includes the new gated aggregate.
- Dedicated incident workflow: **19 tests** (six new plus incident probe/inventory fixtures, including integration) and source-blob verification are expected on the final commit. Broader analyzer workflow also exercises existing telemetry safeguards and pinned EPS DBC provenance. Refer to GitHub Actions on final HEAD for exact pass/fail status; do not infer success from this prose.
- The original development branch, concurrent Claude branches, production Comma collector, Railway scheduled reviewer, active truck software and OEM/Panda protections are unchanged.
- **Blocked:** private real-route replay of this new observer, independently verified exact runtime/dirty-tree correspondence, controller output phase and 50 Hz transmission alignment, measured-vs-commanded EPS response, independent active-route steering stability, permitted parked resource checks and independent hardware/closed-course safety review. No controller parameter candidate, installer or autonomous release is authorized.

**Interpretation:** The earlier incident established a native command reaching the recorded ceiling. This change makes *duration and conditions of that observation* independently measurable without falsely attributing stale, unassisted or unsafe samples to Comma steering. It does not yet establish how long those conditions persisted on the incident route or why the controller saturated.
