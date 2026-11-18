# November 18 — fail-closed ACC following-data validity gate

**Research-only; no vehicle-control or installer changes.**

## Scope and provenance

- Recorded-drive decoder remains pinned to Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (installed dirty working tree remains unverified).
- Stacked on the independently tested lane-quality [draft PR #8](https://github.com/wholesalebridgeowner/sunnypilot/pull/8); does not alter `ram1500-custom-development`, steering/PAM [draft PR #1](https://github.com/wholesalebridgeowner/sunnypilot/pull/1), Claude's review branch, vehicle software, or Railway.
- Narrowly changes the offline `ramtools.comfort_episodes.evaluate` following-distance stream. The separate longitudinal acceleration and lane model-phase analyzers remain unchanged.

## Identified error and correction

The following episode index previously discarded explicitly invalid carState messages before its causal as-of lookup. A newer invalid vehicle state could therefore be invisible and an older valid state reused within the 150-ms tolerance, manufacturing continuous following evidence. In addition, it did not require `carState.canValid=True` and accepted a radar record whose envelope validity was unknown (`None`).

Now **all** timestamp-valid carState messages remain visible to the as-of lookup; only explicitly valid carState **and** explicitly valid CAN status can qualify, and any invalid or unknown radar envelope is rejected. Every rejected radar/carState pairing increments the existing episode-continuity barrier, so a neighboring valid sample cannot bridge the poisoned observation.

Synthetic fixtures test four carState/CAN invalid or unknown variants and an unknown radar envelope, each between two adjacent sub-threshold periods. They also ensure the previous valid state is not incorrectly reused. The fixture helper now marks ordinary synthetic carState CAN status explicitly true.

## Interpretation and test gates

This is a fail-closed observational data-quality change, **not** a hypothesis that the actual truck's ACC control changed. The prior private replay's three following episodes (40.507 s; median observed sample headway 1.179 s) were computed with earlier logic and **must not be represented as revalidated** under this gate. Re-run the exact tested source in an isolated private R-log analyzer before updating numeric findings. The observed headway is not the OEM selected gap, a safety target, nor evidence of factory throttle/brake commands.

No raw telemetry, VIN, route ID, location, CAN payload, or event timestamps are committed. The November 14 +171-second saturation, the actual PAM→EPS command identity, and independently validated Comma 3 installation build remain unresolved.
