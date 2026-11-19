# November 18 — post-drive private R-log intake and candidate-calibration evidence gate

**Status:** completed read-only analysis of currently available private recordings. The owner said the truck had returned home. This document neither proves the entire return trip was logged nor recommends installing an updated steering calibration.

## Route discovery and provenance

An authenticated Comma route listing and a full private Railway S3 inventory were compared after the driver's return-home message. The newest **fully available** afternoon recording ran approximately 54 minutes and contained **54 complete R-logs and 54 Q-logs**, indexed segments 0–53. Its published route metadata ended before the owner's reported return-home time. No subsequently recorded return-home route was confirmed by that inventory. The owner/device identifiers, exact route token, geographic data and raw messages remain private.

The existing one-shot Railway `ram-private-comfort-once` source-pinned analyzer was executed privately on **four disjoint, four-minute windows** of that latest 54-segment route. Its exact installed-device schema pin was `sunnypilot/sunnypilot@1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. It does not transmit CAN, interact with a moving device, or change vehicle software.

| Window (anonymous route segment indices) | Messages decoded | Source pins matched | Truncated files | Confirmed cruise-state carState rows | Qualified factory following or lane model phases |
|---|---:|---:|---:|---:|---|
| 8–11 | 421,864 | 4/4 | 0 | 0 of 23,999 | 0 |
| 24–27 | 421,957 | 4/4 | 0 | 0 of 23,999 | 0 |
| 40–43 | 421,714 | 4/4 | 0 | 0 of 24,000 | 0 |
| 48–51 | 422,119 | 4/4 | 0 | 0 of 23,999 | 0 |
| **Analyzed total** | **1,687,654** | **16/16** | **0** | **0 of 95,997** | **0** |

Each observed carState sample in these selected four windows was rejected from the factory-ACC-motion evidence because `cruiseState.enabled` was not confirmed. The model lane-phase analyzer had zero qualified `laneChangeStarting` and zero qualified `laneChangeFinishing` windows. **This is insufficient calibration evidence, not evidence that the truck was stationary or that steering did not work.** We did not examine every segment in the 54-minute recording and do not assert that cruise remained off outside these windows.

Provenance of private jobs: the successful one-shot deployment identifiers are `26e60294-7efe-4c6d-ac18-4b8fc236cbd0`, `60d482ae-a4de-4e4e-9737-532226b1fea0`, `f686ee0a-2fdc-4c05-8bb1-c902a0e47e1c`, `fc6c0edc-2130-4e09-91db-dd4c0223da30`. The selected source is the existing review-pinned Railway comfort Docker build, not a new on-device build.

## Consequences for owner-requested tuning

1. There is **no new qualified ACC-headway or lane-change comfort score** from this afternoon's inspected windows. Do not replace previously observed aggregate ACC headway figures with zero or interpret missing data as ideal performance.
2. The observed `steerActuatorDelay` configuration of 0.20 s and learned 0.32 s remain **offline sensitivity hypotheses**, not an optimum. Follow the owner's warning that apparent sluggishness may trade off against steering oscillation/ping-pong: better response latency alone is not a release criterion.
3. The selected analyzer does not derive physical lane-centering oscillation, cross-track error or verified steering phase dynamics from these windows. Claiming a new steering factor, friction or delay would be unjustified.
4. The current private inventory is missing a confirmed completed route extending to the owner's return-home time. Check Comma route history and cloud upload status *later* before claiming the full drive is available; a closed device, connectivity, upload delay or logger coverage could explain the discrepancy, but none is established.
5. No further onroad instrumentation is justified until diagnostic requirements are reconciled. A parked on-device investigation of missing route timing or message continuity would require explicit approval and care with scarce msgq subscriber slots.

**Release:** no calibration value approved; no vehicle controls, Panda firmware, EPS programming, CAN messages or installed files changed. Preserve the current baseline and independent safety gates. Keep future periodic checks limited to authorized private cloud inventory and completed logs.
