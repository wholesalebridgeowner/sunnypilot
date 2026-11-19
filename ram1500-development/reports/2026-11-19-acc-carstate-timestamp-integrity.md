# November 19, 2026 — verified offline vehicle-state timestamp integrity for factory ACC research

**Status: validated source-pinned offline instrumentation; research-only, not control calibration or vehicle-ready.**

## Defect and confined change

The factory ACC *observed-motion* analyzer and separate observed following-distance analyzer could previously accept conflicting `carState` records with identical timestamps. Insertion order at an as-of join could determine whether a synthetic/duplicated observation appeared to have cruise enabled, healthy CAN, no driver pedal, the right segment, or a particular speed/acceleration. It could thereby distort continuity and observational comfort statistics. This is a data-integrity bug, not evidence of a vehicle malfunction.

A shared `ramtools/carstate_timestamp_integrity.py` canonicalizer now coalesces equivalent timestamp rows and, when relevant state fields conflict, produces one synthetic **invalid barrier at the same timestamp**. The barrier prevents older good vehicle state from being reused through an ambiguous observation. Segment disagreements are treated as conflicts. Both affected analyzers report fixed aggregate repeated/ambiguous counters; the private report only allowlists those known numeric fields. The downstream following filter exposes the specific ambiguity rejection count rather than relying on accidental input ordering.

Synthetic tests include identical duplicates and conflicts in cruise state, accelerator input, speed, acceleration, CAN validity and segment identity, with both input orders. The existing Ram offline Actions workflow was expanded instead of introducing a new workflow. No control modules or installed vehicle files were changed.

## Reproducible validation

- Base: draft PR #27 HEAD `ee91b2a20d98c0b2a65d2bd54d80076473d3c3e3`.
- Final source-and-tests validation commit: `3157e9d5d1c52801175e87f5d48c260bbc86ce9f` on draft PR #28, branch `ram1500-acc-carstate-time-integrity-20261119`.
- GitHub Actions exact commit: **five completed successful runs**, IDs `44525961416` (following), `44525961392` (20-segment integration fixtures), `44525961436` (motion), `44525961560` (per-segment source), `44525961561` (pinned comfort).
- Isolated, one-shot private Railway research service `ram-private-comfort-once` pinned to the same source/test commit: deployment `7cf1043f-f2f8-400f-ac2e-17b476e24c4a` **SUCCESS**. Docker build **169/169 pytest passed**. A previous validation attempt at an earlier commit failed because an old strict fixed-key output fixture did not include the new ambiguity counter; the fixture was updated before the successful clean rerun.
- Private runtime: **20/20 completed retained morning R-logs**; **20/20 full recorded-source commit headers** equal the installed-log source `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; **2,005,758 messages**; **0 truncated files**. Full commit header match does **not** prove identity with the dirty working tree or running executable on the Comma.

## Measured effect and interpretation

| Retained morning recording, aggregate only | Post-fix result |
|---|---:|
| `carState` rows | 114,076 |
| Repeated same-timestamp `carState` rows | **0** |
| Ambiguous same-timestamp `carState` groups | **0** |
| Following observations accepted | 1,735 |
| Following episodes of at least 2 seconds | 6 |
| Qualifying following duration | 86.444 seconds |
| Median sampled observed headway | 2.259 seconds |
| ACC-conditional observed acceleration episodes | 6 |
| Qualified acceleration duration | 86.741 seconds |
| Acceleration observations labeled model-flagged lead | 8,680 |
| P90 absolute observed acceleration | 1.163 m/s² |
| P90 half-second-windowed acceleration-derivative proxy | 0.563 m/s³ |
| Qualified lane-change starting/finishing model-phase windows | 0 / 0 |

**No real equal-time carState collisions occurred in this particular retained morning subset.** Accordingly, historical observational aggregates are unchanged. The synthetic tests prove future contradictions are now rejected deterministically. They do **not** validate OEM longitudinal actuator commands, a selected following-gap setting, a radar ECU tracking authority, physical lane change, steering stability or driving comfort.

The following-distance estimator is observational and heavily dependent on model-flagged lead context; correlated samples and episodes are not independent trials. The later-afternoon route's lack of active factory cruise applies only to that recording, **not the full day**. Early morning route segments that rotated off the device remain unverified.

## Outstanding release gates

The November 14 +171-second saturation lacks full high-resolution R-log evidence; November 17 segment 30 torque-rate/controller-phase alternatives remain observational and may be ambiguous; held-out independent lane-tracking, oscillation and comfort acceptance is not complete. OEM factory ACC throttle/brake actuation remains unidentified. The November 18 factory parking EPS status was independently observed during approximately 14.6 seconds, but actual PAM-to-EPS command identity, authorization and third-party low-speed capability remain unverified. A prior parked CPU prototype exceeded its acceptance gate; further device tests require explicit parked-vehicle owner approval. No valid installation candidate, Panda/OEM safety validation, independent peer safety acceptance or supervised vehicle release has been established.

**Privacy/safety:** No raw R-/Q-log, raw CAN payload, VIN, GPS, location, dongle/device identifier, route key, absolute event timestamp, private credentials, on-device commands, vehicle actuation, steering/brake/throttle controls, Panda bypass, OEM permission spoofing or installer address was created or published. The original development and Claude concurrent branches, Railway production collector and truck software remain unchanged.
