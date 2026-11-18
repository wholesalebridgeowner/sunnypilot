# November 18 — factory parallel-parking evidence from private 100 Hz Ram R-logs

**Status: real recorded-drive measurements. Read-only analysis only; OEM activation time is owner-reported, not decoded as an independent parking-mode bit.**

## Provenance
- Owner reported activating factory automatic parallel parking at approximately **05:32 Central** on November 18, 2026. The owner's vehicle has functional factory self-steering parallel parking.
- Exact vehicle's recorded Sunnypilot build: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (v0.10.1). The matching pinned cereal schema was validated and used.
- Completed adjacent R-log segments 11 and 12 were requested through the existing authorized Comma cloud-uploader connection and downloaded into the existing **private** Railway S3 bucket. The two compressed private objects were 9,906,687 and 10,247,145 bytes.
- One-shot isolated Railway private analyzer service `ram-factory-parking-private-analysis`, final successful deployment `250e3a06-9079-45d6-bd76-077019d795e2`, source commit `5526975353a9ea3ebe41ba0d3ee4349cac5de8fb`. No vehicle communication or CAN transmission from this process.
- On each R-log segment, timing is anchored to **its first CAN event**, excluding a replayed `initData` header whose old timestamp would otherwise double the apparent route time. A fixture regression test catches that mistake.
- The report contains no GPS, exact location, VIN, dongle ID, raw CAN payload or full route key.

## Confirmed vehicle behavior

The strongest activity falls in the **route-relative seconds 685–705**, with repeated Drive/Reverse selection, slow movement, large steering angle changes, and substantial EPS motor feedback. This is broadly consistent with the user's approximately 05:32 factory park-assist observation; the precise activation instant remains unverified.

| Route-relative interval | Observed speed range | Near-stop steering angle motion* | Comma lateral control | Comma native steering command |
|---|---|---|---|---|
| 680–685 s | 0.000–0.383 m/s | 5.6° | inactive at 500/500 samples | 0 |
| **685–690 s** | 0.385–0.754 m/s (Reverse) | no ≤0.3 m/s samples; overall steering angle range **557.6°** | inactive at 500/500 | 0 |
| **690–695 s** | −0.039–1.146 m/s (Reverse / Drive) | **354.1°** among 200 near-stop samples | inactive at 500/500 | 0 |
| **700–705 s** | −0.036–1.821 m/s (primarily Reverse) | **327.1°** among 77 near-stop samples | inactive at 500/500 | 0 |

*Near-stop = observed `carState.vEgo <= 0.3 m/s`; the reported range is calculated **only from steering angle samples occurring at those speeds**, not from all samples in the five-second bin. The sensor may report small negative speed noise at standstill. The sample groups do not prove a single continuous interval of literal zero speed.

Additional observations:
- The EPS motor feedback reached approximately **±1024 native reporting units** during several turns. Native values are not motor force in newton-meters and should not be treated as actuator-capacity calibration.
- `carState.steeringPressed` was often true during the most aggressive wheel rotations; this is a steering torque threshold, **not independent proof of deliberate human steering input**. The OEM system can potentially produce torque reactions. Attribution requires the owner observation and/or an independently decoded ParkSense activity state.
- No recorded `canError`, `controlsMismatch` or `steerSaturated` event was observed in the selected segments.
- Comma continued sending nominal zero-torque LKAS CAN traffic, but `carControl.latActive` was false and `carOutput.actuatorsOutput.torqueOutputCan` was zero. **The large steering maneuvers were not commanded by Sunnypilot's LKAS controller.**

## CAN differences: candidates, not identified parking control

An initial broader candidate window mistakenly mixed parking with later higher-speed driving. We corrected it to a low-speed OEM candidate **685–705 s**, compared with the preceding **660–680 s** baseline. This matters: an apparent parking-only `0x3BC` in the wider interval **does not appear as a parking-only candidate** in the corrected interval and is not evidence of a parking steering command.

Selected existing CAN messages showing byte-level activity changes between these windows:

| Bus / ID | Baseline frames and rate | Candidate frames and rate | Fraction of neighboring frame payloads changed, baseline → candidate |
|---|---|---|---|
| Bus 0, `0x81` | 1,000 at 50 Hz | 1,000 at 50 Hz | 0 → 0.153 |
| Bus 0, `0x1F6` | 40 at 2 Hz | 44 at ~2.2 Hz | 0 → 0.093 |
| Bus 0, `0x26F` | 23 at ~1.1 Hz | 26 at ~1.3 Hz | 0.136 → 0.240 |
| `sendcan`, `0xA6` | 1,000 at 50 Hz | 1,000 at 50 Hz | frequent changes in both phases; **Comma commanded steering torque = 0** |

`src=130` duplicates of some bus-0 packets reflect Panda forwarding, not independent ECU proof. These CAN IDs are **not decoded as factory steering commands**. Payload variability can be caused by counters, checksums, changes in gear/speed, sensors or unrelated vehicle activity.

## Vehicle service documentation supports a separate factory architecture

A service-manual reproduction for the 2021 Ram 1500 HFE identifies the **Park Assist Module (PAM)** as accepting inputs including parking mode, steering angle, gear and vehicle speed, and lists a **steering-control output to the EPS**. It also describes module communication over CAN. This is a strong architectural lead that **factory ParkSense self-steering has an OEM control path distinct from Comma's standard LKAS command**. It is not a confirmed frame ID or an authorization protocol.

Source: [2021 Ram service manual, Park Assist Module](https://www.shinevfn.com/Dodge%20and%20Ram/2021/1500%20HFE/Repair%20and%20Diagnosis%20%28Single%20Page%29/Accessories%20%26%20Equipment/Drivers%20Assistance%20Systems%20-%20ADAS/Chime%2FBuzzer%2FDriver%20Assist%20%28Service%20Information%29%20-%201500/Description/Park%20Assist%20System/Park%20Assist%20Module%20%28PAM%29/).

The [2021 Ram 1500 official owner's manual](https://vehicleinfo.mopar.com/assets/publications/en-us/Ram/2021/1500_DT/5633293_21_DT_OM_EN_USC_DIGITAL_E4.pdf) describes steering guidance during ParkSense maneuvering at parking speeds and a nominal 5 mph limit while active. This motivated excluding the later, faster post-parking samples from the candidate interval.

## Critical distinction: LKAS engagement vs low-speed EPS motor capability

The truck's installed Ram software requires **initial Sunnypilot engagement around 14.5 m/s (~32 mph)**, but can continue actively steering once engaged at speeds below that. The user has personally confirmed this. Separately, the **factory parking system steers at near-zero speeds**. Therefore the 32 mph figure is not an EPS physical steering floor; it is a current LKAS engagement/software authorization condition, not proof the OEM park system uses LKAS `0xA6` or grants third-party software the same privileges.

## Next evidence, no active vehicle modification
1. Independently locate factory ParkSense active-state or PAM status messages and their originating CAN bus using licensed service information or safely collected diagnostics. The bytes of `0x81`, `0x1F6`, `0x26F` should not be guessed.
2. Cross-correlate verified parking state with steering angle, EPS motor feedback and OEM-authorized modules; distinguish owner supervision, intervention or possible cancellation from normal operation.
3. Validate whether the OEM PAM-to-EPS command path is observable on the Comma harness. A distinct OEM bus may not be represented by its camera-side CAN view; absence of a message is not proof it doesn't exist.
4. Keep any future analysis passive. Do not replay OEM commands, spoof firmware/gear/mode, modify Panda safety or attempt third-party zero-speed steering from these findings alone.

**Conclusion:** strong recorded proof of EPS steering movement at near-stopped speeds **without Sunnypilot steering actuation**. Together with the owner's factory automatic-parking observation and service documentation, the evidence supports investigating the OEM PAM/EPS control architecture rather than seeking another EPS firmware flash. Exact parking-mode status, command address, ECU ownership and authorization remain unresolved.
