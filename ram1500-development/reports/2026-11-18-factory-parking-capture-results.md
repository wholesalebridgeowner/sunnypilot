# Factory parallel-park capture — first results (Claude, 2026-11-18)

Follows `2026-11-18-factory-parking-capture-plan.md`. One supervised factory self-park, owner-reported as done.
Passive, offline, aggregate-only. No raw CAN bytes, GPS, VIN, device identity or clock times in this file.

## Data
- Route `000003dc` (sunnypilot build `1f7233cb`, the same drive build as Nov 14 and Nov 17), 14 segments.
  Segments 8–13 (the last ~5.5 min, ending in the park) were copied read-only over SSH after the truck was parked
  and offroad. No segment was still open. SHA-256 of all six `rlog.zst` files matched the device copies.
- Raw logs and per-second derivatives stay private on the owner's PC. Times below are seconds from the first
  valid CAN message of segment 8, the anchor shared by all `park_assist_*` tools.

## 1. The truck's own status signals mark the park-assist window
`park_assist_eps_state.py` (commit `ca536c2`) decodes documented status fields of `EPS_2` (0x31) and `EPS_3` (0xA4)
from the drive build's public DBC.
- **Decoder self-check passed:** decoded `COLUMN_TORQUE` and `EPS_TORQUE_MOTOR` equal `carState.steeringTorque` and
  `steeringTorqueEps` on 32,628 of 32,628 samples.
- **`AUTO_PARK_HAS_CONTROL_2` = 1 in one contiguous block: 201.0–215.6 s (14.6 s)**, matching the owner-reported
  time. It was 0 on every other frame of the six segments.
- `LKAS_STATE` was 1 in the same block, plus 2 frames (20 ms) after it, and 0 everywhere else. That differs from the Pacifica DBC comment
  ("2 when autopark has control, 8 when actuatable by LKAS"). sunnypilot never engaged on this drive (max 13.4 m/s),
  so `LKAS_STATE` while sunnypilot steers is still unobserved on this truck.
- `EPS_3 Activation_Status` stayed 0 throughout, and `Hands_on_Wheel` was 0 throughout the block.
  `TORQUE_OVERLAY_STATUS` was 1 throughout the block but also took that value during manual driving, so it does
  not discriminate.

## 2. What happened inside the block
| Seconds | Gear | Speed (m/s) | Wheel rate max (°/s) | EPS motor feedback | Note |
|---|---|---|---|---|---|
| 200 | drive → reverse | 0 | 2.5 | ~10 | stopped, shift |
| 201–204.6 | reverse | 0–0.38 | ≤ 27.5 | up to 242 | park assist holds torque; wheel moves 5.6° while stopped |
| 205–212 | reverse | 0.4–0.75 | up to 600 | up to 1023 (signal ceiling) | backing in |
| 212.3–214.3 | reverse → drive | ≤ 0.3 | 467.5 | 1024 | **wheel turned 354° while reported stopped** |
| 214.3–215.6 | drive | up to 2.0 | ~300+ | up to 1024 | pulling forward; block ends at 215.6 s |

- **Zero-speed steering is real on this truck's EPS:** 557 of 1,460 samples in the block were at or below 0.3 m/s
  (432 with `carState.standstill`), and steering angle spanned 440° across them.
- **sunnypilot was not involved:** `carControl.latActive` was False on 1,460 of 1,460 samples, and the Comma's native
  steering command (`carOutput.actuatorsOutput.torqueOutputCan`) was 0.0 throughout. The OEM path steered independently.
- **The block ended because the owner took over** (owner-confirmed after this report was first pushed). The EPS
  dropped `AUTO_PARK_HAS_CONTROL_2` at 215.6 s, in Drive at 2.0–2.1 m/s (about 4.6 mph). Over the last 1.1 s before
  the release, driver column torque reversed sign and built steadily. It was above 120 in magnitude (the
  `steeringPressed` threshold) for the final 1.12 s, above 300 for 0.73 s and above 400 for 0.43 s, and was about −600
  at the release. EPS motor feedback eased from the 1024 ceiling to 760–900 during that ramp. Earlier, while the
  system turned the wheel at up to 467°/s at standstill, column torque had the opposite sign (+150 to +267).
- With a single event, a torque-triggered release cannot be told apart from a speed-limit release: torque and speed
  were rising together. (ChatGPT's report cites a nominal 5 mph limit from the owner's manual; not checked here.)
- `EPS_3 Hands_on_Wheel` and `Driver_Override` stayed 0 through the takeover and for 1 s after it, so neither field
  marks this takeover. Across the six segments, `Hands_on_Wheel` was 1 on 4,705 of 15,612 manual-driving EPS_3 frames,
  0 on all 730 park-assist frames, and `Driver_Override` was 1 on only 5 frames.

## 3. CAN comparison (`park_assist_phase_windows.py`, park 201.0–215.6 s vs manual 216.0–230.6 s at the same spot)
- **No CAN ID appeared only during park assist.** With at least 10 frames in either window, bus 0 carried 129 IDs and
  bus 2 carried 6; only `0x3BC` (18 frames) was seen in the manual window and not in the park window. Whatever the OEM system
  uses is already on the bus during ordinary driving.
- Most large differences are IDs changing *less* during park (for example `0x14E`, `0x8B` ESP_6, `0x151`, `0xB5` ECM_TRQ).
  That is consistent with the lower speed (median 0.48 vs 1.68 m/s), not evidence of a park-specific message.
- IDs that changed *more* or changed rate during park: `0x1EB` (2.06 → 2.47 Hz, change fraction 0.03 → 0.20), `0x127`,
  `0x265`, `0x230`, and `0x332` (4.25 → 1.78 Hz). **None is defined in the public DBC.** These are candidates for
  later passive study only: a correlation is not a meaning, a source ECU or an authorization. These five come from
  windows that were not matched for speed, gear or wheel motion. They were not re-tested with the controls in
  section 8, so treat them as weaker than `0xA9`.
- Tool note: rows on bus 128/130 are the panda's echoes of frames it sent or forwarded, and they duplicate the
  bus 0/2 rows. Read them as copies, not separate traffic.

## 4. Manual low-speed steering vs park-assist steering (same 6 segments)
| | Park assist, wheel moving (≥ 10°/s) | Manual, ≤ 3 m/s, wheel moving (≥ 10°/s) |
|---|---|---|
| Samples | 986 | 7,165 |
| Driver column torque, median / p90 | 105 / 261 | 280 / 400 |
| EPS motor feedback, median / p90 | 884 / 1024 | 659 / 1024 |
| Wheel rate, median (°/s) | 105 | 125 |

The EPS supplies more torque with less column torque during park assist. Some column torque during park assist is
expected without hands, from the wheel's inertia at 600°/s. `EPS_3 Hands_on_Wheel` was 0 in both columns.

## 5. Why sunnypilot needs 32 mph first, then steers below it (owner observation, confirmed in code)
Drive build `1f7233cb`:
- `opendbc_repo/opendbc/car/chrysler/interface.py` `_get_params_sp`: Ram 1500 DT `minEnableSpeed = 14.5`,
  `minSteerSpeed = 0.5` (0 only if 0x4FF is on the bus; it is absent on this truck).
- `opendbc_repo/opendbc/sunnypilot/car/chrysler/carcontroller_ext.py` `get_lkas_control_bit`: on Ram DT the EPS
  "LKAS control" bit is **set only while 14.5 ≤ vEgo ≤ 15.0 m/s (32.4–33.6 mph)**. It then **stays latched at any
  lower speed until the gear leaves Drive**. A 2 s guard follows every falling edge.
- `selfdrive/car/car_specific.py`: the Chrysler branch raises `belowSteerSpeed` below 1.0 m/s (clears above 1.5 m/s).

That is exactly the owner's observation. It matches the Nov 17 drive scan: once latched, the EPS followed hands-off
sunnypilot torque 80–91 % of the time down to 5 m/s, and 38 % below 5 m/s. The factory park assist steers at 0 m/s
through its own path (`AUTO_PARK_HAS_CONTROL_2` = 1) while sunnypilot's bit is unlatched (gear changes reset it).
How the EPS authorizes that path is not established. **Nothing here supports replaying or spoofing OEM messages.**

## 6. Tool fixes found on real data
- `park_assist_motion_context.py` and `park_assist_phase_windows.py` rejected any non-increasing `logMonoTime`. Real
  rlogs interleave services in per-socket batches: measured maximum reorder was **0.137 s** on these segments, and the
  committed motion tool stopped with `records must be chronological`. Fixed in this commit: reorder up to 2 s is
  accepted and reported (`maxReorderSeconds`), and anything larger still fails closed. Two regression tests were added.
- `park_assist_eps_state.py` "hands-off wheel motion" is **not specific**: it flagged 13 ordinary manual low-speed
  turns, because power assist keeps driver torque under 120. Use `AUTO_PARK_HAS_CONTROL_2` to locate the window and
  treat the motion intervals as context only.

## 7. Open questions
- `LKAS_STATE`, `AUTO_PARK_HAS_CONTROL_2` and `EPS_3` fields while sunnypilot is actively steering. This needs an
  engaged segment, for example route `000003d8`, read privately with the owner's OK.
- Whether the factory system releases on driver torque, on speed, or on both (section 2). One takeover cannot
  separate these.
- A second self-park, only if the owner chooses, would test whether the flag block and the `0xA9` activity repeat.
  Ideally it would run to completion without a takeover. Nothing above depends on it.
- Which module sends `0xA9` and what it means. This needs OEM documentation, not more payload statistics.

## 8. Cross-check of ChatGPT's parallel analysis of the same event
ChatGPT analysed segments 11–12 of the same route privately on Railway
(`2026-11-18-factory-parking-rlog-findings.md`, `2026-11-18-pam-eps-source-dbc-crosscheck.md`). The two pipelines
used different decoders and different segment sets.

**Agreement.**

| Measure | ChatGPT | This report |
|---|---|---|
| `AUTO_PARK_HAS_CONTROL_2` block | 1 episode, 14.6 s, 1,460 frames | 201.0–215.6 s, 1,460 frames |
| `LKAS_STATE` | 1 on 1,460/1,460 flag frames; 2 stray frames outside | 1 in the block; 2 frames (20 ms) after it |
| Samples at ≤ 0.3 m/s in the block | 557, angle range 440.3° | 557, angle range 440° |
| sunnypilot lateral | `latActive` False 1,460/1,460, command 0 | same |

- I fetched the two pinned DBC source files from GitHub at `1f7233cb`, and their git blob SHA-1s match ChatGPT's
  pins (`df913561…` and `fa408f13…`).

**Correction: `0x23` and `0xB5` are labeled, and `0x23` is the steering-angle message.**
- The pinned Ram DT generator maps `STEERING` (258) to `0x23` (35) and `ECM_TRQ` (280) to `0xB5` (181).
  `STEERING` carries `STEERING_ANGLE` and `STEERING_RATE`.
- `0x23`'s payload changes whenever the wheel turns, in manual driving and in park assist alike: 0.89–0.99 of frames
  with the wheel moving, 0.01–0.05 with it still. ChatGPT's 0.201 → 0.982 contrast for `0x23` in Drive therefore
  reflects wheel motion, not park mode.
- `0xB5` (engine torque limits) changed on 0.33–0.95 of park-assist frames and 0.27–0.86 of manual frames.
  Neither ID is a park-assist candidate.

**`0xA9` holds up when wheel motion is controlled for.** Method:
- Measure the non-tail change between consecutive frames of the same ID, with the last two bytes excluded as in
  ChatGPT's tool.
- Match each frame to the latest `carState` and `EPS_2` that are no more than 150 ms old.
- Classify the wheel as moving at |`steeringRateDeg`| ≥ 10 and still below 2.
- Keep only strata with at least 20 frames.

Results:

| `0xA9` stratum | Frames | Payload changed |
|---|---|---|
| Manual, wheel moving, Drive or Reverse, ≤ 0.8 m/s | 507 / 447 | 0.000 / 0.000 |
| Manual, wheel moving, Drive, 0.8–3 m/s and > 3 m/s | 2,392 / 3,145 | 0.000 / 0.000 |
| Park assist, wheel moving, Reverse / Drive | 365 / 86 | 0.975 / 0.988 |
| Park assist, wheel still, Reverse | 199 | 0.276 |

- `0xA9` is sent at about 50 Hz. Its non-tail payload changed on 574 of 16,283 frames in the six segments. Only 2 of
  those changes fell outside the park-assist block.
- Inside the block, the activity began 20 ms before the EPS flag rose and ended 20 ms before it fell. That is one
  `0xA9` frame period, so this data cannot say which comes first.
- `0xA9` is not defined in the pinned Ram DT DBC sources. **This makes it the strongest passive park-assist candidate
  so far, and it is still only a correlation.** Its sender, bus path and meaning are unknown. So is whether it is a
  request, an acknowledgement or a status. Nothing here supports transmitting it or replaying it.

**`0x81`:** its non-tail payload never changed during park assist in Reverse (0 of 602 frames). In manual Reverse at
≤ 0.8 m/s it changed on 0.344 of frames with the wheel moving and 0.000 of 42 frames with it still. This is
unexplained, and it is recorded as an observation only.
