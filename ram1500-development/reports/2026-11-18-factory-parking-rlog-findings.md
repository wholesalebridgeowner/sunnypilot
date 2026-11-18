# November 18 — factory parallel-parking evidence from private 100 Hz Ram R-logs

**Status: real recorded-drive measurements with the factory automatic-parking EPS control flag independently decoded from the pinned original Ram DBC. Read-only; no vehicle modification.**

## Provenance
- Owner reported activating factory automatic parallel parking at the end of a short drive on November 18, 2026. The owner's vehicle has functional factory self-steering parallel parking.
- Exact vehicle's recorded Sunnypilot build: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (v0.10.1). The matching pinned cereal schema was validated and used.
- Completed adjacent R-log segments 11 and 12 were requested through the existing authorized Comma cloud-uploader connection and downloaded into the existing **private** Railway S3 bucket. The two compressed private objects were 9,906,687 and 10,247,145 bytes.
- One-shot isolated Railway private analyzer service `ram-factory-parking-private-analysis`, final successful deployment `250e3a06-9079-45d6-bd76-077019d795e2`, source commit `5526975353a9ea3ebe41ba0d3ee4349cac5de8fb`. No vehicle communication or CAN transmission from this process.
- On each R-log segment, timing is anchored to **its first CAN event**, excluding a replayed `initData` header whose old timestamp would otherwise double the apparent route time. A fixture regression test catches that mistake.
- The report contains no GPS, exact location, VIN, dongle ID, raw CAN payload or full route key.

## Confirmed OEM EPS parking-control status — major result

The **exact installed driving build** includes a Chrysler RAM DT DBC that defines `EPS_2.AUTO_PARK_HAS_CONTROL_2`. Its signal is **one bit at DBC start bit 51** in the source Stellantis `EPS_2` frame (ID 544). The RAM DT DBC generator maps this EPS status frame to **CAN ID 49 (`0x31`) on bus 0**. For this 1-bit Motorola field, the status value is payload byte 6, bit 3. The read-only private analyzer now decodes this single known bit from every recorded bus-0 EPS status frame.

**Source pinned to the actual installed commit `1f7233cb`:**
- `opendbc_repo/opendbc/dbc/generator/chrysler/_stellantis_common.dbc`: `BO_ 544 EPS_2`, `SG_ AUTO_PARK_HAS_CONTROL_2 : 51|1@0+`
- `opendbc_repo/opendbc/dbc/generator/chrysler/_stellantis_common_ram.py`: address mapping `544: 49`

Private reanalysis deployment `e214cb58-0252-4cc0-b1d6-de7c23445d70` **SUCCESS**, source commit `0186f8ea5c900c4df9e9b4bc69991939d9b59ea6`; GitHub CI passed all diagnostics fixtures including the bit-position/transition test.

| Route-relative interval | EPS `AUTO_PARK_HAS_CONTROL_2` true / observed | State transition |
|---|---:|---|
| 660–680 s | 0 / ~2,001 | Factory parking control not reported |
| **680–685 s** | **400 / 500** | **Factory auto-park control enabled** |
| **685–690 s** | **500 / 500** | Remained enabled |
| **690–695 s** | **500 / 500** | Remained enabled, including near-stopped steering |
| **695–700 s** | **60 / 500** | **Factory auto-park control disabled** |
| 700–780 s | 0 / subsequent ~8,000 | Not under factory parking control |

The EPS reported factory auto-park control active for **approximately 14.6 seconds**, during the owner-identified low-speed maneuver. **Comma lateral control remained inactive and Comma native LKAS torque was zero throughout.** In the 690–695 s interval, 200 samples with reported speed ≤ 0.3 m/s spanned **354.1° of steering wheel angle**, while the factory control bit was active for that entire five-second bin.

**Additional independent EPS status cross-check:** a follow-up private analyzer deployment `b350eb55-3179-44f2-816a-9bf1feb44f48` decoded two further status fields from that same installed-build DBC `EPS_2`: `LKAS_STATE : 23|4@0+` (byte 2, bits 7–4) and `TORQUE_OVERLAY_STATUS : 6|4@0+` (byte 0, bits 6–3). The historical schema provides ordinal values but no verified enumeration for these modes.

| Status field | Factory auto-park flag true (1,460 EPS frames) | Factory auto-park flag false (10,539 EPS frames) |
|---|---|---|
| `LKAS_STATE` | **value 1: 1,460 / 1,460** | value 0: 10,537; value 1: 2 |
| `TORQUE_OVERLAY_STATUS` | **value 1: 1,460 / 1,460** | value 1: 3,518; value 3: 7,021 |

The `LKAS_STATE` shift is another clearly measured EPS mode/status correlation during factory parking. Do not assign a human-readable meaning or third-party command recipe to these **ordinal** values without OEM definitions. The two residual inactive-flag samples with `LKAS_STATE=1` can represent near-edge timing; the audit did not infer causality from them. The counts agree with **approximately 14.6 seconds of the auto-park control bit enabled** at 100 Hz.

**Important attribution correction:** later large steering changes at 700–705 s occurred **after the EPS auto-park control bit had switched off**; they must *not* be attributed to active factory automated steering. They may reflect driver-controlled maneuver completion or other steering activity. Similarly, high `steeringPressed` torque flags cannot by themselves establish hand contact or program cancellation.

This independently confirms the OEM EPS was in its automatic parking control mode, not merely that its wheels turned while Comma was inactive. It **does not** identify the originating PAM-to-EPS command message, authorize a third-party steering controller, or establish that Comma's 0xA6 LKAS command can activate that mode.

## Confirmed vehicle behavior

The strongest activity falls in the **route-relative seconds 685–705**, with repeated Drive/Reverse selection, slow movement, large steering angle changes, and substantial EPS motor feedback. This is broadly consistent with the owner's report of a factory park-assist maneuver at the end of the drive; the precise activation instant remains unverified.

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

2021 Ram 1500 HFE service information identifies the **Park Assist Module (PAM)** as accepting inputs including parking mode, steering angle, gear and vehicle speed, and lists a **steering-control output to the EPS**. It also describes module communication over CAN. This is a strong architectural lead that **factory ParkSense self-steering has an OEM control path distinct from Comma's standard LKAS command**. It is not a confirmed frame ID or an authorization protocol.

Source: 2021 Ram 1500 service information, Park Assist System → Park Assist Module (PAM) description. The official copy is Stellantis TechAuthority (subscription); unofficial third-party reproductions are deliberately not linked.

The [2021 Ram 1500 official owner's manual](https://vehicleinfo.mopar.com/assets/publications/en-us/Ram/2021/1500_DT/5633293_21_DT_OM_EN_USC_DIGITAL_E4.pdf) describes steering guidance during ParkSense maneuvering at parking speeds and a nominal 5 mph limit while active. This motivated excluding the later, faster post-parking samples from the candidate interval.

## Critical distinction: LKAS engagement vs low-speed EPS motor capability

The truck's installed Ram software requires **initial Sunnypilot engagement around 14.5 m/s (~32 mph)**, but can continue actively steering once engaged at speeds below that. The user has personally confirmed this. Separately, the **factory parking system steers at near-zero speeds**. Therefore the 32 mph figure is not an EPS physical steering floor; it is a current LKAS engagement/software authorization condition, not proof the OEM park system uses LKAS `0xA6` or grants third-party software the same privileges.

## High-resolution factory-control episode reconstruction (November 18 follow-up)

A later independent one-shot replay (private Railway deployment `0f519093-3379-4fad-b99b-003255eea8c9`, **SUCCESS**, GitHub source commit `40bb763f941a6d3dc8c8b8464f64bb56b5d8379d`) reconstructed each contiguous `EPS_2.AUTO_PARK_HAS_CONTROL_2` control episode from 100-Hz records. It synchronized `carState`, `carControl`, and `carOutput` with **past-only as-of joins capped at 150 ms**.

An initial attempt discarded some observations because different R-log services are interleaved out of timestamp order. That issue was identified through the production replay and corrected by sorting **only private, scalar signal observations** before constructing events. A synthetic regression test now reproduces out-of-order logger messages.

| High-resolution measurement | Result |
|---|---:|
| Distinct factory auto-park control episodes in selected R-log segments | **1** |
| Duration between decoded rising and falling EPS status edges | **14.6 s** |
| `AUTO_PARK_HAS_CONTROL_2=true` EPS frames | **1,460** |
| Matched `carState` samples inside this OEM-control episode | **1,460** |
| Within-episode `vEgo <= 0.3 m/s` observations | **557** |
| Steering-wheel angle range over those near-stop samples | **440.3°** |
| Fresh Comma `carControl.latActive=false` samples | **1,460 of 1,460** |
| Fresh Comma-native steering-output samples | **1,460 of 1,460** |
| Nonzero Comma-native steering command samples | **0 of 1,460** |
| Near-stop `steeringPressed` status samples | **102 of 557** |
| Out-of-order observations after sorting | **0** |

The near-stop range pools the near-stop points within the full 14.6-second factory-control episode; it is **not** proof that the truck made one continuous 440.3° steering movement while completely stationary. The `steeringPressed` status is driver-column torque detection, not a reliable measure of human hand contact.

**This result is a stronger independent demonstration of actual factory parking control than any inferred CAN candidate.** It still does not identify which ECU produced factory-authorized EPS requests, whether that path is on a Comma-accessible bus, or whether a third-party controller could use it. No active steering software or Panda safety was modified.

## Next evidence, no active vehicle modification
1. Independently cross-check the decoded `EPS_2.AUTO_PARK_HAS_CONTROL_2` flag against OEM ParkSense diagnostic state and identify the factory PAM steering-control message/bus through licensed service data, without transmitting test commands. The bytes of `0x81`, `0x1F6`, `0x26F` should not be guessed.
2. Cross-correlate verified parking state with steering angle, EPS motor feedback and OEM-authorized modules; distinguish owner supervision, intervention or possible cancellation from normal operation.
3. Validate whether the OEM PAM-to-EPS command path is observable on the Comma harness. A distinct OEM bus may not be represented by its camera-side CAN view; absence of a message is not proof it doesn't exist.
4. Keep any future analysis passive. Do not replay OEM commands, spoof firmware/gear/mode, modify Panda safety or attempt third-party zero-speed steering from these findings alone.

**Conclusion:** direct recorded confirmation that the EPS reported `AUTO_PARK_HAS_CONTROL_2` during near-stopped automated steering **without Sunnypilot LKAS actuation**. Together with the owner's experience and service information, this establishes OEM parking-control capability on the truck. The precise factory PAM steering-command address, authorization protocol and supported third-party access remain unresolved. No EPS firmware flash or safety-gate change is justified by these data.


## Next passive experiment: matched-speed, matched-gear CAN activity

A new private, source-pinned one-shot analysis (Railway deployment `332b56e5-e9c2-4686-aca4-565ecbd49488`, **SUCCESS**; GitHub commit `f82fa82359bdc0a95c1eda394e0ef30349fe7a99`, offline CI **passed**) matched each observed vehicle CAN frame to the **latest preceding EPS park-control status and carState**, both no more than 150 ms old. The comparison is limited to comparable reported speeds −0.1 to 0.8 m/s and **the same reported gear** (Drive or Reverse). It excludes forwarded harness duplicates, the defining EPS status message `0x31`, and two trailing payload bytes likely to contain counters/checksums. Raw CAN bytes are used only transiently inside the private analysis job; the output contains frame counts and aggregate rate-of-change statistics, not packets or signal bitmaps.

This was specifically designed to reduce the false evidence from the earlier arbitrary before/during-window comparison, which mixed speed changes and gear shifts. It is still **observational**, and does not control for steering demand, load, ECU transitions or full PAM network coverage.

Selected *bus 0* signals, compared **within the same gear and speed band**:

| Bus-0 CAN ID | Gear | Inactive frames | OEM auto-park active frames | Non-tail payload changed between consecutive frames, inactive → active |
|---|---|---:|---:|---|
| `0xA9` | Reverse | 118 | 602 | **0.009 → 0.744** |
| `0xA9` | Drive | 533 | 86 | **0.000 → 0.988** |
| `0xB5` | Reverse | 118 | 602 | 0.845 → 0.349 |
| `0x23` | Drive | 1,066 | 172 | 0.201 → 0.982 |
| `0x81` | Reverse | 118 | 602 | **0.259 → 0.000** |

The `0x81` reversal is especially instructive: its apparent activity increase in the *unmatched*, earlier comparison **did not persist** when speed and gear were held within narrow observational bands. Do **not** identify `0x81`, `0xA9`, `0x23`, or `0xB5` as factory steering-command messages on these statistics alone.

The exact installed-build Chrysler Ram DBC source does **not** label `0xA9` (0xA9 = decimal 169) in its Chrysler-RAM-DT-specific definitions or common generated signal list. Its meaning and originating ECU remain **unverified**. It may reflect motor feedback, powertrain/gear states, parking status, environmental data or an unrelated counter/change. There is no support yet for a PAM-command or actuator authorization conclusion.

The study discarded 61,094 speed-out-of-band CAN observations, 2,681 EPS status frames to prevent circular attribution, 1,499 short payloads, 548 unsupported-gear frames and harness-forwarded duplicates; 76 candidate bus/gear strata had enough source-matched samples to calculate comparable change ratios. These are **filter counts**, not failure events. Input processing was read-only.

### Next validation gate

Ask Claude to cross-check `0xA9`'s message origin and purpose against OEM service/DBC documentation, while separately verifying that the installed Comma harness sees the relevant PAM-to-EPS control network. If the OEM message is not visible to the harness, no amount of analysis of Comma-captured bus 0 will establish it. Do not transmit unknown CAN messages, infer undocumented bits or change Panda/EPS safety to test a hypothesis.



## Independent factory network-topology check

The 2021 Ram 1500 HFE service-manual description of the **Park Assist Module (PAM)** explicitly states that it communicates on the **high-speed Controller Area Network – Chassis (CAN-C)**. It also describes PAM broadcasting electronic messages to enable other factory Park Assist functions. This is authoritative network-family context, **not** evidence that any particular unlabelled frame on the Comma-visible bus is a steering request.

- 2021 Ram 1500 service information, Electronic Control Modules → Module, Park Assist (PAM), Description and Operation (official copy: Stellantis TechAuthority; unofficial reproductions not linked)
- [Ram 1500 DT body-builder cybersecurity/network overview](https://www.ramtrucks.com/BodyBuilder/service/Image?imageId=MtQrP%2FFqLY5r%2Fest8MtGjGgHzAHGUTU0WB3rWuqSY7YmQ2vEhuBWBNLjy4xHgojZ%0A), which documents a vehicle Security Gateway isolating outside connections from vehicle networks.

The next passive engineering problem is therefore **bus observability and source attribution**: whether the Comma harness's observed vehicle streams include the EPS-relevant CAN-C PAM messages, and whether the apparent differential activity of `0xA9` survives matched controls for speed, gear, steering load and repeat maneuvers. No gateway bypass, module authentication modification, steering-command injection, or full-auto steering install is proposed.

