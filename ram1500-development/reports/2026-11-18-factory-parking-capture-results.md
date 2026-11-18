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
- The block ended at 215.6 s while column torque was about 500–600 and manual steering continued afterwards. That fits
  either a driver takeover or the system finishing; the owner can say which.

## 3. CAN comparison (`park_assist_phase_windows.py`, park 201.0–215.6 s vs manual 216.0–230.6 s at the same spot)
- **No CAN ID appeared only during park assist.** With at least 10 frames in either window, bus 0 carried 129 IDs and
  bus 2 carried 6; only `0x3BC` (18 frames) was seen in the manual window and not in the park window. Whatever the OEM system
  uses is already on the bus during ordinary driving.
- Most large differences are IDs changing *less* during park (for example `0x14E`, `0x8B` ESP_6, `0x151`, `0xB5` ECM_TRQ).
  That is consistent with the lower speed (median 0.48 vs 1.68 m/s), not evidence of a park-specific message.
- IDs that changed *more* or changed rate during park: `0x1EB` (2.06 → 2.47 Hz, change fraction 0.03 → 0.20), `0x127`,
  `0x265`, `0x230`, and `0x332` (4.25 → 1.78 Hz). **None is defined in the public DBC.** These are candidates for
  later passive study only: a correlation is not a meaning, a source ECU or an authorization.
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
- Whether the end at 215.6 s was a driver takeover or a completed maneuver (owner).
- A second self-park, only if the owner chooses, to check that the block and candidate IDs repeat. Not needed for
  anything above.
