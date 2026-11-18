# November 18 — fail-closed interrupted model lane-change phases

**Status:** Offline research change; no vehicle actuation, Panda safety, OEM permissions, device installation or remote log movement.

## Scope and recorded-source provenance

- The analysis is downstream of pinned installed-recording schema commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. The on-device dirty working tree remains unverified.
- Change is limited to `ramtools/lane_phase_quality.py`, its synthetic tests and this report, stacked on the independently verified following-validity PR #9.
- The maintained `ram1500-custom-development` branch, Claude's review, PR #1 parking investigation and PR #2 are not modified.

## Identified false-positive path

When a `drivingModelData` phase was open, an invalid observation, model-message gap, or segment transition correctly incremented a censor counter, **but then cleared the suppressed phase state**. If a later record still said `laneChangeStarting` or `laneChangeFinishing`, the analyzer could treat the *tail of the interrupted original phase* as a fresh phase entry, and—given enough valid `carState` samples—publish a qualified completed maneuver. That is not independently supported by the missing model chronology.

**Correction:** retain the interrupted state's suppression through further bad records; continue censoring that same state until a later valid observation shows a different state. An observed exit allows subsequent genuinely new phases to be measured. A later segment's matching model phase is not assumed to be independent of the interrupted phase.

Synthetic regression coverage:

1. A same-state model gap followed by a well-sampled truncated tail.
2. An invalid/unknown-validity model observation followed by the same state.
3. A segment boundary during a finishing phase followed by a completed-looking tail.
4. A distinct new phase after an observed state exit remains countable.

## Interpretation and remaining validation

This change has no effect on the steering or longitudinal controller, and it does **not** prove physical lane crossing or recentering. ## Verified fixture and private-R-log results

- **Offline GitHub Actions:** [run 44372497662](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44372497662), SUCCESS, **112/112** pinned comfort fixtures; parameter-manifest verification and synthetic parameter sensitivity generation also passed. Tests ran on source/report commit `076c7d87c64d814ab3461da410b5ae4984e4e1b6`.
- **Private Railway one-shot replay:** deployment `51b30761-550f-4de0-be4a-9ac7ea0f6555`, SUCCESS, source pinned to **that same exact commit**; Docker build ran **73/73** private-targeted tests, including the four new lane-phase regressions. Completed three R-logs, **316,310** decoded messages, **zero** truncated files and **3/3** recorded `initData.gitCommit` observations matching the historical `1f7233cb` pin. The device's dirty installed working tree remains unverified.
- **Measured difference under the correction:** counted `laneChangeStarting` **completedTransitions decreased 3 → 2**, and `driverOverrideOrUnknown` rejections decreased **3 → 2**. One interrupted tail is no longer classified as another completed transition. The existing one segment-boundary censor remains. Qualified entry windows remain **0**. Completed and qualified `laneChangeFinishing` windows remain **2**, one left and one right.
- The independent following-distance aggregates are unchanged under this narrow model-phase fix: **three** qualifying episodes totaling **40.507 s**, median sampled observed headway **1.179 s**. This is not the factory ACC selected gap.

Only fixed whitelisted aggregate counters were extracted from the private Railway job. No raw log contents, route keys, CAN payloads, GPS, VIN or exact event timestamps are included here. These data support a correction to the **offline phase classification**, not any causal change in steering behavior or validated physical lane crossing.

The private Nov 14 +171-second saturation event, Nov 17 segment-30 steering cap causal attribution, decoded factory ACC actuation and OEM PAM→EPS command identity remain unresolved. Resource profiling and a reproducibly validated Comma 3 release are still outstanding.
