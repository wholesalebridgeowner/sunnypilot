# November 18, 2026 — steering replay phase identifiability on private R-logs

**Status: observational, offline-only. Not a vehicle-control patch, validated EPS authorization, or installable Comma release.**

## Provenance and validation

- GitHub analysis commit: `21aefae514e184b7fe48db797c52c9da3a344bb1`; regression tests: `d49951486422c58605e981acfa538ab7035d21fe`, fixture chronology correction: `baa29f0d487154e99a414c249789b87115315224`.
- Private Railway read-only analysis deployment: `private-railway-id`, status **SUCCESS**, source pinned to `baa29f0d487154e99a414c249789b87115315224`.
- Railway Docker build passed **33 targeted tests**: private route probe 7, torque-limit coverage 6, causal replay 8, phase audit 12.
- The job read 421,253 messages from the selected private November 17 R-log segments 0, 29, 30, 31, using the observed build's `1f7233cb` cereal schema. The measured comparison is limited to route-relative seconds **1800–1807**.
- No raw route keys, VIN, GPS, log records, credentials or individual CAN messages are included here. GitHub's full repository-wide workflow result is **not independently confirmed** by this report.

## Why this check was needed

The previous phase sensitivity audit had found that **22/22** active, changed native steering outputs unmatched by the latest logged `carControl` could also be reproduced from an older, preceding logged control sample. That is **retrospective matching**. A rate-limited controller may produce the *same* native command from several very different steering requests; matching an older request alone therefore does not establish controller scheduling.

The new read-only `controlPhaseIdentifiability` audit evaluates **all eligible changed outputs**, not just mismatches. It uses the same prior native command and the latest at-or-before EPS observation; for each output, it compares the latest `carControl` plus up to three distinct-timestamp older controls, within the original 80 ms causal age bound. It counts how many different control samples fit the observed command within ±1 native unit. No future input is eligible.

## Real-drive measurements

| Metric, seven-second window | Count |
|---|---:|
| Changed native output observations | 284 |
| Eligible active-output changes | **283** |
| Eligible with an older control option | 283 |
| Older control samples tested (up to 3 per output) | 849 |
| Outputs with **multiple** control samples producing a fitting prediction | **277 / 283 (97.9%)** |
| Outputs with **one** fitting control candidate among tested options | **6 / 283 (2.1%)** |
| Outputs where all tested control samples predict exactly the same native command | **233 / 283 (82.3%)** |
| Outputs where a different normalized request nevertheless predicts the exact same native command | **189 / 283 (66.8%)** |
| Outputs with at least two distinct predicted native commands among tested options | 50 / 283 |
| Baseline latest-control matches that also fit at least one older control | **261 / 261** |
| Baseline latest-control misses matched by an older control | **22 / 22** |

The 22 baseline misses decompose into **16 with multiple matching older control candidates and 6 with only one** within the tested control alternatives. The observation is consistent with timestamp phase differences, but it **does not identify** an actual controller update phase.

## Engineering interpretation and gate

The leading finding is a **lack of phase identifiability in this replay window**, not evidence for a particular controller scheduling offset. The high rate of identical native predictions is consistent with rate/measured-torque clipping removing information about the original normalized request. Alternative control observations are correlated, and counts across updates are not independent experiments. A unique fitting sample is not proof it was used: intermediate cycles may be unlogged, the LKAS latch is unobserved, and the historical torque constants remain unverified against the dirty installed runtime.

**Do not optimize or raise steering limits based on the 22/22 retrospective fit.** The next appropriate read-only investigation is exact installed controller-source provenance and a passive check of control-cycle timing, command transmission and EPS feedback/acceptance, with driver intervention accounted for. For low-speed/factory parking and route-guided steering, continue separate authorization and fallback research: none of those functions was proven by this experiment.

No running vehicle software, ECU authorization, steering limit or Panda safety code was touched; no installer address is authorized.
