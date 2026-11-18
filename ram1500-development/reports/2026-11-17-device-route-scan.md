# On-device route scan — 2026-11-17 (Claude, read-only)

Run on the parked, offroad Comma at `nice 19`, reading the qlogs still on the device with the device's own cereal
(`sunnypilot@1f7233cb`). Only aggregates left the device: no GPS, no raw CAN, no identifiers. Scripts were piped over
SSH, nothing was installed. Times are seconds from the first message of each route.

## Routes on the device
The Nov 14 routes (`000003cc`, `000003cb`) have rotated off. Four routes remain, each with **full rlogs for every
segment** (useful for forensics that qlogs cannot support: 100 Hz signals, `modelV2`, full CAN).

| Route | Segments | Minutes | km | Max speed | Lateral active | `steerSaturated` | Torque output at cap | End-of-route faults |
|---|---|---|---|---|---|---|---|---|
| `000003d8` (Nov 17 drive) | 36 | 44.1 | 44.8 | 35.8 m/s | 68 % of carState samples | none | 24 samples (~2.4 s) | `canError` + `controlsMismatch` in the last 5 s |
| `000003d9` (parked) | 30 | 29.9 | 0 | 0 | 0 | none | 0 | same, last second |
| `000003da` (parked, 1 min) | 1 | 0.8 | 0 | 0 | 0 | none | 0 | `controlsMismatch` 1.0 s at startup (6.4 s) and at end |
| `000003db` (parked, G3 session) | 29 | 28.3 | 0 | 0 | 0 | none | 0 | same, last second |

Every route ends with `canError` + `controlsMismatch` in its final seconds, with the truck stopped. This is the
ignition-off signature already described for Nov 14 (`REVIEW_LOG` C-07), now observed on four of four routes.

## Low-speed steering on `000003d8`
Samples (10 Hz qlog) with lateral control active, `carOutput.actuatorsOutput.torqueOutputCan` |> 50|, driver column torque
|≤ 60| (hands off), counted as "EPS follows" when `carState.steeringTorqueEps` exceeds 50 in the command's direction:

| Speed band | Hands-off commanded samples | EPS follows |
|---|---|---|
| ≥ 14.5 m/s | 7,584 | 6,629 (87 %) |
| 10–14.5 m/s | 98 | 89 (91 %) |
| 5–10 m/s | 60 | 48 (80 %) |
| 0–5 m/s | 60 | 23 (38 %) |

Reading: on this truck (EPS `68466110AB`), after engaging above 14.5 m/s, the EPS delivers openpilot's torque down to
~5 m/s at a rate comparable to highway speed; below 5 m/s the response drops. Limits: one drive, small low-speed counts,
10 Hz samples joined within 150 ms, a crude direction criterion, rate-limit lag not modelled. Engagement itself still
requires ≥ 14.5 m/s (`minEnableSpeed`); nothing here changes that. Confirm with the rlog at 100 Hz before relying on it.

## Torque-cap runs without an alert on `000003d8`
`controlsState.lateralControlState.torqueState.output` at ±1.0 (the 350-unit ceiling): runs at 569.8 s (0.1 s),
1778.5 s (0.1 s) and a cluster at **1801.4–1806.0 s** (1.0 + 0.3 + 0.1 + 0.8 s). No `steerSaturated` fired, so another
alert gate held (driver press within 2 s, no undershoot, desired < 1 m/s², or the car-side limiter differing from the
command). This cluster is the best next forensic case: same mechanism family as Nov 14 at 171 s, but with rlogs on the
device (segment 30). Analysing it means reading that rlog on the device or copying it privately, which needs Mark's OK.
