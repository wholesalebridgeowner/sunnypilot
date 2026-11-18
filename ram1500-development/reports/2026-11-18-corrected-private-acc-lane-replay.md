# November 18 — corrected source-pinned private ACC and lane-model replay

**Status: completed private R-log replay, read-only observational analysis. Not a control change, safety validation, or installation candidate.**

## Verifiable provenance

- Historical recorded Sunnypilot/schema source: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (exact schema verified at container build). The three decoded `initData.gitCommit` observations matched this pin. This analysis does not independently verify every historical device working-tree byte.
- Replay source commit: [`ea4ed4e76c5773cbe3d57640021df7288a33c8e8`](https://github.com/wholesalebridgeowner/sunnypilot/commit/ea4ed4e76c5773cbe3d57640021df7288a33c8e8) on research-only draft PR #7, stacked on lane-segment integrity PR #6 and prior ACC radar-provenance PR #5.
- Isolated Railway private one-shot service `ram-private-comfort-once`; deployment **`c3858b13-3ede-4b4d-81ba-badfe1787e9a` SUCCESS**, source pinned to the above commit. No new data went into GitHub: the process emitted only bounded, fixed-shape numeric aggregates.
- Docker container build passed **60 synthetic tests**, including read-only data-output privacy, chronological model/car state segment gates, radar/lead context gates, and the exact installed-build `onroadEventsSP` nested event-envelope decoder. The preceding build at `1263c0b` failed closed because that branch lacked the envelope fixture; the replay source added both the test and installed-schema decoder correction before this successful run.
- GitHub's pinned comfort fixture workflow passed at the replay commit; [GitHub Actions PR #7](https://github.com/wholesalebridgeowner/sunnypilot/pull/7) contains the independent CI history. The separate docs workflow has a known unrelated dependency failure; full repository CI is not a release gate pass.

## Complete private decoder quality

| Quality metric | Verified value |
|---|---:|
| Completed R-log files selected | 3 |
| Decoded messages | 316,310 |
| Truncated or unreadable selected files | 0 |
| `initData` build-commit matches | 3 / 3 |
| `carState` rows | 18,003 |
| `radarState` rows | 3,601 |
| Radar rows with unknown segment provenance | 0 |

The subset is not a statistically representative sample of all highway driving. Source-file selection and timing were controlled privately. No VIN, GPS, route token, raw CAN, or per-event time is published.

## Corrected observational following-distance and acceleration results

| Metric | Post-fix private replay |
|---|---:|
| Accepted cruise/lead observations | 870 |
| Qualified following episodes | 3 |
| Total following duration | 40.507 s |
| Median accepted sample headway | 1.179 s |
| 10th-percentile accepted sample headway | 0.849 s |
| Accepted sample fraction below 1.5 s | 0.900 |
| Model-flagged-lead motion episodes | 3 (40.618 s) |
| Model-flagged-lead p90 absolute windowed acceleration derivative | 0.476 m/s³ |
| No-confirmed-lead motion episodes | 3 (46.200 s) |
| No-confirmed-lead p90 absolute windowed acceleration derivative | 0.198 m/s³ |

Prior, **pre-segment-gate** real-data replay reported no-confirmed-lead **46.240 s** and **0.218 m/s³**; the combined provenance/continuity fixes recalculated these to **46.200 s** and **0.198 m/s³**. Model-flagged-lead duration and proxy percentile, and the qualified following total, did not change at displayed precision. These differences must not be assigned to any single signal or software effect without matched before/after event inspection **inside private storage**.

The headway data is a small set of correlated *observed* radar/model-lead intervals. The 1.179-second sample median is **not** the factory ACC selected following-distance target; neither acceleration derivative is measured throttle/brake or the OEM ECU's acceleration command. The proxy is sensitive to sample selection and windowing, not an independently calibrated comfort score.

## Corrected lane model-phase integrity

| Metric | Private replay |
|---|---:|
| Starting phases censored on log-segment transition or unverified provenance | 1 |
| Starting phases rejected by `steeringPressed` or unknown intervention status | 3 |
| Qualified starting phases | **0** |
| Qualified finishing/recentering *model* phases | 2 |
| Finishing-phase median duration | 1.000 s |
| Finishing-phase p90 peak absolute steering-wheel rate | 2.500 degrees/s |
| Finishing-phase p90 steering-rate derivative | 236.588 degrees/s² |

A valid model state in the adjacent recording segment is **not** accepted as the completion of a prior model phase. `steeringPressed` is an ECU threshold, not independent proof of hand contact. Model finishing phases are not evidence of a physical lane-line crossing or stable lane-center recapture. Steering-rate derivative is not chassis lateral jerk.

## Unresolved validation gates

1. Confirm the November 14 +171 s steering-saturation episode with the exact recorded R-log and source code; the available private storage inventory previously did not contain this high-resolution route. Do not infer causal controller timing from rate-limited replay alternatives.
2. Source-decode factory ACC commanded gap, acceleration and throttle/brake semantics through documented, safe read-only methods before making claims about control smoothness or ACC strategy.
3. Build a larger set of independent, quality-controlled lane-change recordings before drawing comfort conclusions; physical boundary crossing and recentering require separate observable signals.
4. Continue passive PAM–EPS CAN-C bus observability and EPS low-speed mode research. The verified 14.6 s factory auto-parking status does not authorize third-party 0 mph LKAS or document OEM message semantics.
5. Parked Comma/Panda sidecar still requires verified CPU/RSS remediation and operator-approved revalidation; device software remains untouched.
6. Reproducibly build and independently validate a full Comma 3 vehicle-control candidate (fault tests, safety checks, hardware review) before any install guidance.

**No CAN injection, actuator change, EPS firmware modification, Panda restriction bypass, active vehicle install or public raw-log export occurred.**
