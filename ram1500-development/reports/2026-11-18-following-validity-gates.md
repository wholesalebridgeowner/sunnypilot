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


## Independent validation on the exact analyzed source

- **GitHub Actions:** [Ram pinned comfort fixture tests, run 44362640082](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44362640082) **SUCCESS**, **108/108** targeted tests. This is on source/test commit [`1c90bb3`](https://github.com/wholesalebridgeowner/sunnypilot/commit/1c90bb3091881b011123cc13932d43a88d7b00f5). The separately running full fork CI is **not** claimed passed.
- **Private Railway:** isolated one-shot `ram-private-comfort-once`, deployment `private-railway-id` **SUCCESS** from the exact same source commit. Docker image build passed **69/69** targeted fixtures, and the decoder processed **316,310** messages from three complete private R-logs with **zero** truncated files and **3/3** pinned build commit observations matched.
- New privacy-whitelisted rejection counts: **0** invalid/unknown vehicle-message-or-CAN snapshots and **0** invalid/unknown radar envelopes in the selected following observation stream. Consequently, this narrow data-quality correction did not change the aggregate results in those selected files. This is not a statement about all routes or all CAN health.
- Revalidated observational headway aggregate: **3** qualifying following intervals, **40.507 s** total, median observed sampled gap **1.179 s**. The windowed acceleration-derivative p90 remained **0.476 m/s³** with model-flagged lead and **0.198 m/s³** with no confirmed lead. Model finishing phases **2 qualified**; entry phases **0 qualified**.
- These samples are time-correlated, the source's historical working tree remains unverified, and no factory-selected following-distance setting, actual brake/throttle command, physical lane crossing or roadway control capability is established.

**Outstanding:** matching November 14 +171 s high-resolution R-log, PAM-to-EPS command provenance, independent parked device CPU/RSS validation, reproducible Comma 3 build, and independent safety review.

## Interpretation and test gates

This is a fail-closed observational data-quality change, **not** a hypothesis that the actual truck's ACC control changed. The prior private replay's three following episodes (40.507 s; median observed sample headway 1.179 s) were computed with earlier logic and **must not be represented as revalidated** under this gate. Re-run the exact tested source in an isolated private R-log analyzer before updating numeric findings. The observed headway is not the OEM selected gap, a safety target, nor evidence of factory throttle/brake commands.

No raw telemetry, VIN, route ID, location, CAN payload, or event timestamps are committed. The November 14 +171-second saturation, the actual PAM→EPS command identity, and independently validated Comma 3 installation build remain unresolved.
