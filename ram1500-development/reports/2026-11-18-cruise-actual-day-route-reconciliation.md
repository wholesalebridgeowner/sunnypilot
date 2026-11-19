# November 18 Ram ACC / Sunnypilot reconciliation — actual pinned R-log results

**Result:** The earlier suggestion that the Ram had not used cruise *all day* was incorrect. The morning recording **did contain active factory ACC and active Comma lateral control**. A different, later afternoon recording did not. These are distinct routes. No vehicle control software was changed.

## Complete workflow and evidence basis

1. Verified the exact recorded Sunnypilot source `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` and firmware `68466110AB`. Installed `opendbc_repo/opendbc/car/chrysler/carstate.py` reads `DAS_3.ACC_ACTIVE` on **camera CAN bus 2** to populate `carState.cruiseState.enabled`. The recorded RAM-DT DBC maps `DAS_3` to ID decimal 153 (`0x99`), with `ACC_ACTIVE 21|1@0+` and `ACC_AVAILABLE 20|1@0+`. The private analyzer compared this raw input with Comma's already-parsed state using at-or-before observations no older than 150ms. These two interpretations use **the same underlying camera input**, not two independent sensors.
2. Cross-checked authenticated Comma route metadata, a complete paginated private S3 inventory and parked Comma file availability. The later afternoon route had **54/54 complete R-logs** in private S3. The earlier morning route was partially rotated off the Comma; its **20 consecutive late-route R-logs** were still available, requested via standard Comma Wi-Fi upload, and verified stored privately. Its earlier segments were **not available as complete R-logs through this checked path**, so they cannot be invented or described as analyzed.
3. All private analysis was isolated in one-shot Railway containers pinned to the original cereal schema, with fail-closed completeness, raw CAN channel and source-commit verification. The report exposes only counts and anonymized relative windows; no route keys, device ID, GPS, street, VIN, individual frame bytes or source timestamps.

## Later afternoon — COMPLETE recorded route

Isolated Railway deployment `91d045e6-ef27-46cb-91a4-e2612701d3a9` **SUCCESS**; pinned test container ran **89/89** Python fixture tests before the final private analysis.

| Observation across 54 R-logs | Result |
|---|---:|
| Decoded messages | 5,679,283 |
| Segment source pins matching actual build | 54/54 |
| Truncated R-logs | 0 |
| `carState` observations | 322,896 |
| Parsed `cruiseState.enabled=true` | **0** |
| Raw camera-bus `0x99` ACC_ACTIVE=true frames | **0 / 161,543** |
| Raw CAN-versus-parsed `cruiseState.enabled` disagreements | **0 / 322,816 eligible comparisons** |
| Parsed `carControl.latActive=true` | **0** |
| Parsed `selfdriveState.enabled=true` | **0** |
| Reported speed at/above ~32 mph (14.5 m/s) samples | 172,777 |

**Bounded interpretation:** the recorded factory ACC-active flag and Comma lateral-active flag were both off throughout this **specific complete 54-segment recording**. This does **not** prove they were off for every drive that day, nor does it prove a defective decoder. Camera ACC availability was also false on the raw message in this particular recording. Its precise OEM reason remains unverified.

## Earlier morning — PARTIALLY retained recorded route, confirmed ACC and Comma steering

The truck's morning route was earlier and partially deleted from on-device storage, but **20 consecutive completed late-route R-logs** were recovered through the existing authorized Wi-Fi upload interface into private Railway storage. Source-pinned one-shot deployment `ade7574f-ab84-4b23-9087-a839726fef62` **SUCCESS**; all source pins matched and zero files were truncated.

| Observation across 20 preserved consecutive R-logs | Result |
|---|---:|
| Decoded messages | 2,005,758 |
| Segment source pins matching actual build | 20/20 |
| `carState` observations | 114,076 |
| Parsed factory `cruiseState.enabled=true` observations | **10,861** |
| Camera CAN `0x99` raw ACC_ACTIVE=true frames | **5,430 / 57,006** |
| Raw CAN vs parsed factory cruise disagreements | **0 / 114,011 matched comparisons** |
| `carControl.latActive=true` observations | **10,764** |
| `selfdriveState.enabled=true` observations | **10,540** |
| `cruiseState.available=true` observations | 114,076 |
| `latActive=true` while factory cruise `enabled=false` | **399** (requires separate MADS/state transition attribution; not automatically a defect) |
| Speed at/above 14.5 m/s samples | 71,128 |

The remaining retained late morning segments outside the active interval showed factory cruise and Comma steering **off**. They are not evidence of the original unavailable earlier segment state.

**Additional source-pinned private comfort replay:** one four-segment window within this morning drive, Railway deployment `b4a77307-35d9-43df-b151-b44a1a8dfec0`, passed the original schema/source checks and decoded **421,759 messages** from **4 complete R-logs**, reporting **six** sustained observational following episodes totaling **86.444 seconds**, with **2.259 s** median observed sample headway (and 1.543 s p10). **This is not a decoded factory ACC distance setting.** The visible lead source was model flagged in those episodes; that does not identify OEM radar/ACC actuation or selected gap.

## Important response correction

Our earlier 16-segment sample of the later afternoon route found no confirmed cruise. That result was correct **for those samples**, but extrapolating it to “the drive today” would have been wrong. The source-reconciled morning log proves that cruise and Sunnypilot lateral control were engaged on a different drive. This report explicitly distinguishes recorded-route identity, duration and availability.

## Ongoing parameter study, with no unsafe writes

- `steerActuatorDelay` configured at 0.20 seconds versus learned observation around 0.32 seconds remains a **hypothesis grid**, not an optimum. The owner's concern that shorter delay may increase lane-centering oscillation is a required stability acceptance check.
- A new source-gated steering-wheel reversal proxy is under private replay validation. It is not physical cross-track error or proof of real lane ping-pong. Rejects driver steering, CAN faults, unknown curvature, stale control, and near-limit torque.
- No new `latAccelFactor`, friction, delay, steering torque limit, lane-change aggression or OEM ACC setpoint is authorized for the truck. The factory owns throttle and brake. Parking-mode EPS authorization is separate from third-party LKAS.

**Release gating:** multi-route held-out model validation, independently verified correct controller timing, true lane tracking performance, clean installed build, parked Comma resource tests, independent Claude/source review, supervised controlled-area safety checks and explicit owner approval are still outstanding. Do not present an installable calibration or authorize CAN injection from these observations.
