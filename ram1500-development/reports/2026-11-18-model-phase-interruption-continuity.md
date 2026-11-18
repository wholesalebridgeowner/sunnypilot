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

This change has no effect on the steering or longitudinal controller, and it does **not** prove physical lane crossing or recentering. Previously reported two qualified model finishing phases and zero starting phases from three private November 17 R-logs must be independently **replayed under this exact commit** before they are described as revalidated after the gate. If aggregate counts change, report corrected numbers with matched source SHA, pinned schema, exact test results and private Railway deployment provenance; publish no raw or identifying records.

The private Nov 14 +171-second saturation event, Nov 17 segment-30 steering cap causal attribution, decoded factory ACC actuation and OEM PAM→EPS command identity remain unresolved. Resource profiling and a reproducibly validated Comma 3 release are still outstanding.
