# November 18, 2026 — private R-log torque-cap separation

**Status: observational analysis only; not an installation or control-change approval.**

## Provenance and privacy
- Private Railway read-only probe, container build/deployment: Railway service `ram-private-rlog-probe`, deployment `55caf448-1760-4237-a1d7-27d0dda2cf8e`.
- Analysis code commit: `71d6dbefbfb1efe9679f08722c6d6d2430699cb0`.
- The Dockerfile pins the installed-device cereal commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, with pinned source-hash verification for its vendored car schema.
- Source data: existing November 17, 2026 driving R-logs, four privately selected segments; raw logs, route key, absolute times, GPS, VIN, and CAN frames are not published.
- Probe scanned **421,253 messages** in selected segments, decoded **40,458 rows** in the focused window, and reported **8,000 controller samples** therein. It reported no `steerSaturated` alert or `lateralSaturated` controller sample in the selected focus window.

## Reconciled measurements: route-relative seconds 1800–1807

| Independent recorded stream | Count | Saturation/ceiling proxy | Count at proxy | Maximum absolute value |
|---|---:|---|---:|---:|
| `controlsState.lateralControlState.torqueState.output` | 700 | abs(normalized) ≥ 0.995 | **219** | 1.0 |
| `carControl.actuators.torque` | 700 | abs(normalized) ≥ 0.995 | **219** | 1.0 |
| `carOutput.actuatorsOutput.torqueOutputCan` | 700 | abs(native) ≥ 332.5 (95% of source reference 350) | **0** | **280** |
| `controlsState.lateralControlState.torqueState.saturated` | 700 | recorded boolean true | **0** | n/a |

Additional observed information: `carControl.latActive` was true in **657/700** request samples. No false or unknown message-validity flags were counted for these independent streams. The maximum observed native steering CAN command was **80%** of the configured 350-native-unit reference. The native and normalized streams have different semantics and sample timing; these rows **are not causal timestamp-matched pairs**.

### Interpretation / limits

This is positive evidence of **normalized-controller output reaching ±1.0** for 219 observed samples in the torque-cap cluster. It is **not evidence** of the native LKAS CAN command reaching the 350-unit source reference; the highest observed native command was 280. The controller's `saturated` flag and the `steerSaturated` alert both remained absent in this selected interval. Their different definitions and gating must be investigated instead of describing all three concepts as a single "steering saturation."

The analysis does **not** determine whether the 280-native-unit ceiling comes from driver torque, steering rate limiting, dynamic vehicle-speed or lateral-acceleration logic, a separate EPS limit, or another source. `steeringTorqueEps` is not directly interchangeable with LKAS CAN units. No ECU authorization or physical wheel movement has been measured.

Only selected R-log segments and a focused interval were decoded. The November 14 event is not present in the current private storage inventory. This run supplies no evidence of active lateral control at actual standstill, zero-speed EPS authorization, or navigated maneuvers.

## Regression and deployment checks

The Railway decoder Docker build ran **7 private-probe tests and 6 torque-cap-audit tests**, all passing. The deployed container then produced the aggregate observation above. The separate GitHub offline-analysis workflow status must be checked independently; a successful Railway build is not full product validation.

## Next engineering questions

1. Attribute the observed normalized-vs-native difference using **past-only aligned** `carControl`, `carOutput`, EPS feedback and driver-input streams, and inspect the pinned vehicle-controller code for dynamic limiting paths. Do not treat cross-unit values as identical.
2. Locate active low-speed and disengagement transitions in complete private R-logs with independently checked 0–44 segment coverage; absence of active low-speed samples in this focus window is not proof of EPS inoperability.
3. Confirm firmware-specific factory ParkSense/park-assist actuator path separately from LKAS and preserve ECU safety protections.
4. Keep any software release blocked until deterministic replay, source-level review, and independent on-hardware validations are complete.
