# November 19, 2026 — lane model-phase equal-time source integrity

**Scope:** Read-only, non-installable, fail-closed improvement to observational lane-change model-phase comfort analytics. Does not establish physical lane crossing, recentering accuracy, steering authority, actuator delay, calibrated gains or real-road safety.

## Confirmed defect and remedy

The model-phase observer previously sorted model and carState messages by timestamp but treated duplicates as separate samples. An **identical** model duplicate created an invalid zero-delta model gap, spuriously censoring a candidate maneuver; identical carState duplicates created an invalid zero-time vehicle-state gap. Worse, **contradictory** model records at one time were processed in input order, so a false transition could be accepted differently depending on replay ordering. Contradictory same-time car observations also lacked an explicit deterministic quarantine contract.

The new `_coalesce_equal_time` normalizer compares only fixed, relevant model/vehicle evidence fields. Identical same-time observations count once; contradictory same-time records are replaced by a minimal invalid sentinel. Conflicting model evidence censors a candidate and requires the existing neutral-state re-anchor. Conflicting carState evidence cannot qualify a comfort window or influence its percentile. This avoids exposing source values or driver/private metadata in the sentinel. Boolean timestamps are excluded as clocks, while legitimate signed *route-relative fixture clocks* are retained. No absolute timestamp, route key, private CAN, VIN, GPS, device identifier or raw sensor sequence is written to this repository.

## Exact code and offline verification

- Base: unchanged draft PR #29 HEAD `4706d099f2fe61de231df593b42e4ec8cf1517b6`; separate stacked draft PR #30.
- Initial code commit: `0787fb9b25bce5d5526a559e71746b39deb9414f`.
- New synthetic regression cases: `039835e389f4b409d54e07ab7a788df5069db957`: identical duplicate model/car records, contradictory equal-time model state and segment, conflicting vehicle steering/CAN/EPS health and segment, permutation-invariant fail-closed result, privacy markers, and boolean timestamp.
- Corrected test-compatible final code/test commit: `73a0480f583a6e77f23d9c19ea363d2efa085510`. Initial GitHub CI found three failing existing tests because the new clock filter excluded negative **synthetic relative** fixture timestamps. The filter was narrowed to reject boolean values while preserving signed integer relative clocks. Do **not** treat the initial failed run as passing.
- **GitHub Actions verified PASS at the corrected commit:** [Ram pinned comfort tests #44545938584](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44545938584) **196 passed**; [existing lane health quality workflow #44545938727](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44545938727) **82 passed**. No additional workflow was created.
- **Independent isolated Railway Docker build**: `ram-private-comfort-once`, deployment `d881452f-25c7-4589-8a11-6c8d6e0f124c`, exactly pinned to `73a0480f583a6e77f23d9c19ea363d2efa085510`; **178/178 offline pytest tests passed**. Private R-log execution must be independently confirmed from runtime output; build success alone is not a completed private replay.

## Evidence and release boundaries

The previously retained 20 partial-morning R-logs are historical non-held-out data. Prior qualified lane phase windows numbered zero. Synthetic coverage shows deterministic handling of ambiguous simultaneous evidence; it does **not** show actual lane-change comfort improvement, verify a physical lane boundary crossing, or justify numeric steering calibration. Factory ACC authority remains OEM-controlled; observed headway and acceleration are not decoded brake/throttle commands. Remaining evidence gates include held-out steering/lane tracking, historical Nov 14 high-resolution saturation, PAM-to-EPS command ownership, approved parked-sidecar CPU/RSS acceptance, OEM/Panda safety, independent review and supervised vehicle validation.

The main `ram1500-custom-development` branch, concurrent Claude branches, production Railway log collector, active truck software, firmware, Panda/OEM protections and CAN controls are untouched. No vehicle command, steering/brake/throttle adjustment, public installer or private raw log publication was made.
