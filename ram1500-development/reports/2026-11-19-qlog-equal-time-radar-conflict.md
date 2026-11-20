# November 19 — factory ACC Q-log equal-timestamp radar evidence gate

**Scope:** Read-only, source-pinned telemetry analysis for the recorded Ram 1500 build. Draft PR #44 is stacked exclusively on draft PR #43; the baseline branch and active truck are untouched.

## Identified attribution defect

The Q-log summary's latest-radar-per-`carState` observer (PR #43) retained one lead-distance estimate per vehicle-state sample. Its selection condition allowed `radarState` messages with timestamps **equal** to the current newest timestamp to overwrite the pending candidate. If two same-time messages disagreed in `leadOne.status` or usable `leadOne.dRel`, whichever arrived last would win. Such a sample has no defensible temporal ordering. A duplicate log message with identical timestamp and evidence is not a conflict.

## Correction and measurement boundaries

- Retain only the newest time-eligible radar sample relative to the current vehicle state (same-segment, bounded 0–350 ms offset, factory cruise enabled, valid CAN, no pedal intervention, verified factory ACC ownership).
- An *equal-time disagreement* in lead status or distance permanently excludes the current carState headway candidate, even if more radar records arrive before the next carState. Record only an anonymous count of rejected carState samples. Equal-time identical observations do not double-count or trigger a rejection.
- A new carState or verified segment boundary resets that pairing state. Earlier or stale timestamps never overwrite newer evidence; contradictory late CarParams still disqualifies all route-level factory-ACC statistics.
- The headway quality identifier is changed to `factory_acc_pedal_free_can_valid_source_pinned_segment_isolated_equal_time_radar_conflict_guarded_qlog_v4`. Comparisons against v3 or older headway estimates are suppressed. The separately gated **observed vehicle acceleration derivative** retains its v3 quality identifier; no unrelated jerk comparison reset was imposed.
- Reports expose counts and rounded aggregate statistics only. Radar evidence fingerprints, positions, IDs, exact times and raw logs are not serialized.

The resulting headway is still a **sampled model-flagged lead estimate divided by observed ego speed**. It is neither a confirmed factory radar output nor an OEM selected following-distance setting, throttle/brake command or control calibration. No observed improvement in driving comfort is inferred.

## Validation

Exact implementation and tests are based on draft PR #43 at `625c1a68a28ebffb85ea89a178ce371d210758db`, ultimately pinned to recorded-source identifier `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`.

- Commit `3761886a2279918c242823ab7b4b13b2cb2f2350`: fail-closed equal-timestamp observations, anonymous counter, independent quality marker.
- Commit `535933cf89012cc09a767a7116ee7d8bbea15c44`: six synthetic regressions; initial CI run failed **two** existing comparator fixtures, revealing a missed headway marker update in `compare()`.
- Commit `508622ebf8763a6f2cd8b88d6d1e8065a97ed5b9`: corrected the comparison gate to apply headway v4 and observed-motion v3 independently. [Dedicated Actions run #44655394254](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44655394254) **61/61 passed**; [broader analyzer run #44655394178](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44655394178) **207 tests, one skipped, zero failures**. No failed test was ignored.

Synthetic tests cover disagreeing distances in either arrival order, contradictory equal-time lead presence, identical duplicate observations, disallowing later radar from rehabilitating an ambiguous pairing, isolation across segments with privacy-safe count, and comparison-version independence from observed jerk.

## Private evidence and next gates

The live scheduled Railway research reviewer has **not** been repinned or redeployed for this change. At inspection, its deployment `98db7208-c0d1-4529-aeb8-b7d69a66fec9` still used research source `2377c055e1254498307b45aa471dcbda7814f763` and returned `no_new_eligible_routes` (16 eligible inventory candidates). No new accepted real-route replay was obtained, and no previous quarantined route was promoted. The historical positive morning factory ACC recording and six following episodes (86.444 seconds, median sampled observed headway 2.259 seconds) remain **historical results from a separate higher-resolution observer**, not recomputed by this Q-log correction.

Remaining prerequisites: independent held-out ACC-active and lane-positive drives, factory longitudinal actuation attribution, detailed Nov 14 +171s R-log steering observation if it becomes verifiably available, PAM-to-EPS CAN-C command ownership from verified OEM sources, approved *parked* logger CPU/memory validation, and independent supervised safety acceptance. Do not write new steering/longitudinal parameters or install on device from these fixture tests.

No raw GPS, VIN, exact drive time, device ID, private CAN bytes, credentials or source log files were published. No live vehicle software, Comma logger, controller, Panda/OEM protections or CAN messages were changed.
