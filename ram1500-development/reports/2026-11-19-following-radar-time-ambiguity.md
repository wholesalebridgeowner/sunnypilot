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

**Independent validation, November 19:** The isolated private Railway comfort service was repinned to **exact code/test/workflow commit `c4e32de281ebacc935a1209354ffa70e4c0fd706`**, deployment `7f1bdf9a-6eb8-4931-87e4-97352b3130d7` (**SUCCESS**). Its Docker image build completed **157/157 offline fixture tests passed**, including the three new regression functions. The following runtime private replay of the *same exact source* completed **4/4 retained morning R-logs**, **421,759 decoded messages**, **4/4 matching complete 40-character recorded-build headers**, and **zero truncated files**. The new `ambiguousRadarTimestamp` rejection was **zero** for those four segments; the new correction therefore left the observational aggregates unchanged **for this recording**: **six qualified following episodes, 86.444 seconds, 1,735 eligible lead observations and 2.259 seconds sampled median headway**. Those episode samples remain correlated; an unchanged result here is not an independent held-out validation. The previous PR #24 Docker 154/154 pass was separately pinned to `f78bf78f6c4195c1b0ebaa1c2a4e1d36baa39479`. **No completed GitHub Actions run was registered for code commit `c4e32de`**, so this is a verified Railway Docker/runtime pass, *not* a GitHub Actions CI pass.

## Context and blockers

The earlier morning private four-segment positive-ACC sample yielded six qualifying following episodes, 86.444 seconds, and a **sampled observed** median headway of 2.259 seconds, **not** the selected OEM following target. The later 54-segment cruise-inactive route is a distinct recording. The November 14 +171-second steering R-log still has no verified private high-resolution source, while the November 17 segment-30 cap and controller-phase evidence remains observational. The November 18 EPS auto-park status is confirmed (~14.6 s), not a decoded PAM steering command or a permission grant.

Next gates remain an independently registered GitHub Actions CI pass, independent held-out ACC-active and lane-tracking routes, true OEM throttle/brake attribution, operator-approved **parked** logger CPU/RSS retest (prior CPU 21.6% of one core versus 15% criterion), independent vehicle safety review and controlled-area approval. No controller/acceleration/throttle/steering/bus/Panda changes, Comma device operations, private data export, installer or Railway collector change was performed.

## Independently retrieved historical private ACC *motion*, not throttle, measurements

Separately from the **now-validated same-source duplicate-radar patch**, I re-read the fixed, privacy-safe aggregate from the **already successful** private Railway comfort deployment `ff47fd9e-ff0b-4a29-a44f-3b407d98437d` and independently checked Railway's deployment metadata: source commit exactly `f78bf78f6c4195c1b0ebaa1c2a4e1d36baa39479`, status SUCCESS, created November 19 at 08:43 UTC. That *previous* build had 154/154 Docker fixtures passing, four selected complete R-logs with four exact recorded-build headers, 421,759 decoded messages and zero truncation.

The historical observer's **estimated observed car acceleration** `carState.aEgo`, stratified by lead-source label, was:

| Source-pinned aggregate, **prior source** | Value |
|---|---:|
| ACC-conditional motion episodes labeled `model_flagged_lead` | 6 |
| Total continuity-qualified motion exposure | 86.741 s |
| Eligible acceleration samples | 8,680 |
| Median observed longitudinal acceleration | +0.100 m/s² |
| P90 absolute acceleration | 1.163 m/s² |
| P90 absolute raw finite-difference acceleration-rate (jerk-like) | 2.397 m/s³ (8,674 pairs) |
| P90 absolute half-second-windowed derivative proxy | 0.563 m/s³ (170 consecutive-window pairs) |
| Qualified motion episodes with `radar_flagged_lead` | **0** |

The distinct following-distance estimator reported 1,735 eligible cruise/lead observations, six >=2s following episodes over **86.444s**, sampled median observed gap **2.259s**, episode-median gap **2.397s**, sampled P10 gap 1.543s, and 6.4% of accepted sampled gaps <1.5s. These estimators have **different eligibility and integration rules**, so their durations should not be made identical or interpreted as the same independent trials. Model-source fraction median was 1.0 across the following episodes. The private four-segment lane-model observer produced **zero qualified** lane-change starting and finishing windows; this means unavailable evidence for those windows, **not** proof that no lane changes happened on other portions of the drive.

**Hard attribution limits:** `carState.aEgo` is estimated vehicle motion, not an OEM throttle opening, requested acceleration, direct brake pressure or a causal measure of ACC controller smoothness. Sample-to-sample derivatives are strongly noise-sensitive; the windowed proxy depends on 0.5-second bins and actual sampling coverage. `radarState.leadOne.radar=False` in these six qualified motion episodes is the **recorded/model lead-source flag**, not proof of which object the factory ACC ECU tracked. The 2.259s measured headway is **not** the selected OEM gap setting or proof that the OEM ACC radar commanded the same target. The 8,680 samples are serially correlated within **six** episodes, not 8,680 independent events. No comfort or safety threshold is inferred.

The preceding historical numbers advance observational staged-plan step 7. They are also numerically consistent with the newly verified same-source `c4e32de` replay, but **neither run clears independent held-out ACC actuation, longitudinal comfort, or vehicle safety acceptance gates**.
