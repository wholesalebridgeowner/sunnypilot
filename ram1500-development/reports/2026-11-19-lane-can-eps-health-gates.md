# November 19, 2026 — recorded-source lane model-phase CAN/EPS health gates

**Status:** Offline source-integrity improvement independently validated in a private one-shot decoder, with synthetic fixture regression passes. This is **not** lane-change calibration, OEM steering authorization or a vehicle release.

## Defect and read-only correction

The strict model-phase observer `ramtools/lane_phase_quality.py` could previously admit a candidate model-reported lane-change starting/finishing interval even if its `carState` records had unknown/invalid CAN health or an EPS temporary/permanent steering fault. CarState message validity, driver-steering status and steering-rate availability do **not** establish healthy vehicle CAN or fault-free EPS status. This could make an observational steering-rate comfort percentile appear supported by faulted input.

For each carState within a model phase the observer now requires exact `canValid is True` and exact `steerFaultTemporary is False`, `steerFaultPermanent is False`. False, unknown, missing, or wrong-typed status is excluded. Fixed aggregate-only rejection counters `invalidOrUnknownCan` and `epsFaultOrUnknown` are exposed through the private reporter's explicit allowlist. A healthy synthetic lane phase remains eligible; synthetic invalid CAN, unknown CAN, temporary/permanent EPS faults and unknown fault statuses fail closed. No raw CAN data, event time or identifier is exported.

## Exact reproducibility and independent validation

- Stack: draft PR #29 on draft PR #28, unchanged parent HEAD `3fa9c9dab89a8b30b2b7f831008165d5082ce9c1`.
- Exact code, tests and dedicated CI workflow commit: `b4cec97b6245951e12bac64bb95e1e659802b0a3`; source originally recorded at `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (on-device dirty tree remains unverified).
- Six GitHub Actions suites on the exact code/test commit concluded **SUCCESS**, including the dedicated lane CAN/EPS quality workflow [44535312555](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44535312555) and the existing source/comfort suites.
- Independently executed isolated private Railway one-shot `ram-private-comfort-once`: deployment `private-railway-id`, **SUCCESS**, exact code/test commit pin. Docker image build passed **173/173 offline pytest tests**. Private runtime decoded **20/20 complete retained morning R-logs**, **2,005,758 messages**, **20/20 exact full recorded-build source matches**, and **zero truncated files**.
- The observed morning subset contained **zero qualified model laneChangeStarting and laneChangeFinishing windows**, with **zero newly rejected real lane windows** by either CAN/EPS gate; the private replay therefore cannot estimate a numerical effect on a naturally occurring lane maneuver. Synthetic positive/negative fixtures validate the rejection path, not real lane-keeping comfort.
- Prior observed factory-ACC following aggregates remained 6 qualified episodes / 86.444 seconds / 2.259 seconds median sampled observed gap, not the OEM setting; no OEM throttle/brake command was decoded. These were the same retained morning segments, **not held-out independent routes**.

## Safety and remaining gates

Model phase does not establish physical lane-line crossing, lane-centering accuracy, steering latency or stable recentering. Independent route-held-out lane-change evidence, the Nov 14 +171-second high-resolution saturation, Nov 17 steering controller ambiguity, actual OEM ACC actuation, PAM-to-EPS parking authorization, approved parked Comma CPU/RSS retest and independent OEM/Panda safety/vehicle acceptance remain unresolved. Do not derive or install numerical steering calibration or low-speed permissions from this work.

Only a source-pinned Railway isolated analysis job was re-run using previously private retained R-logs. No raw Q-/R-log, CAN payload, VIN, GPS, device identifier, route key, absolute driving timestamp, credential or private sensor record was published; no active Comma software, CAN command, steering/brake/throttle, OEM/Panda safety, production log collector, original development branch or concurrent Claude branch was changed.
