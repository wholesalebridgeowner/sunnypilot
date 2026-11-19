# 2026-11-19 — equal-timestamp radar observation ambiguity in factory ACC following replay

**Research-only, offline integrity patch. Not vehicle-installable.** Stacked on draft PR #24 (previous exact per-segment installed-source gate). Neither the unmodified `ram1500-custom-development` branch nor Claude's PAM/steering branches are altered.

## Defect and bounded correction

The pinned private following observer `ramtools/comfort_episodes.py` previously consumed each `radarState` record independently. Duplicate messages from the **same segment and monotonic timestamp** could be counted as two lead observations. If those duplicates disagreed on lead validity, range, relative speed or radar/model-source flag, arbitrary input order could influence continuity and sample-weighted observed headway.

New fail-closed processing groups `radarState` records by the verified segment and integer monotonic time, collapses semantically identical duplicates to one observation, and substitutes an anonymous **continuity barrier** for contradictory duplicates. It excludes malformed/noninteger or negative timestamps from radar observation candidates. No raw frame, route ID, GPS/VIN, timestamp or sensor value is emitted by the error path. Only a whitelisted aggregate `ambiguousRadarTimestamp` rejection count is exposed in the private published summary.

A duplicate *identical* frame is not a second independent experiment. A *contradictory* frame is not evidence that a particular lead object was observed. This prevents artificially extending a valid following episode across an ambiguous observation and protects the sample-weighted median.

## Source and tests

- Source: `ram1500-development/ramtools/comfort_episodes.py`, first isolated code commit `27a6545c7fc8a37be84e846b7752b8e7058169f1`.
- Three deterministic synthetic regression functions added: exact duplicates count once; conflicting range fails independent of row order; validity / source / relative-speed conflicts create a break. Test commit `d1303982cd0210c8190d9bbc1cfb1217fa65d9dc`.
- Privacy-scoped output changed in `private_comfort_rlog_probe.py` (`789dc919f5381f8724f54923c7699b0be67a0da3`) and its output-schema fixture (`a9db10db7bda0183d20acc985f26be4c59836409`).
- Dedicated standalone GitHub Actions workflow: `ram-acc-radar-time-integrity.yml`, commit `280aa39e4d409a81a61e2cf35606caebe3de2c3d`. It runs the existing following-observer and private-output test suites without real telemetry.

**Validation status at report creation:** The new tests are committed but neither a completed GitHub Actions run nor a rerun of the private source-pinned Railway R-logs has yet been verified. Do not label the new patch as CI-passed or claim the earlier six-following-episode or 2.259-second observed-headway aggregates remained identical under this correction. The *preceding*, separately pinned PR #24 Railway Docker replay passed 154/154 tests on commit `f78bf78f6c4195c1b0ebaa1c2a4e1d36baa39479`; that result predates this correction.

## Context and blockers

The earlier morning private four-segment positive-ACC sample yielded six qualifying following episodes, 86.444 seconds, and a **sampled observed** median headway of 2.259 seconds, **not** the selected OEM following target. The later 54-segment cruise-inactive route is a distinct recording. The November 14 +171-second steering R-log still has no verified private high-resolution source, while the November 17 segment-30 cap and controller-phase evidence remains observational. The November 18 EPS auto-park status is confirmed (~14.6 s), not a decoded PAM steering command or a permission grant.

Next gates remain a CI pass on this exact patch, privacy-preserving private four-segment replay to measure whether ambiguities actually occur, independent held-out ACC-active and lane-tracking routes, true OEM throttle/brake attribution, operator-approved **parked** logger CPU/RSS retest (prior CPU 21.6% of one core versus 15% criterion), independent vehicle safety review and controlled-area approval. No controller/acceleration/throttle/steering/bus/Panda changes, Comma device operations, private data export, installer or Railway collector change was performed.
