# 2026-11-19 — Q-log factory ACC observational-data qualification (draft PR #39)

**Scope:** Read-only, offline analysis only, stacked on draft PR #38. Original `ram1500-custom-development`, Claude's concurrent branches, the five-minute Railway collector/reviewer, active Comma software, ECU, OEM/Panda safety and CAN actuator paths remain unchanged.

## Engineering defect

The scheduled Q-log reviewer's legacy `leadHeadway` admitted a model-flagged radar lead when cruise was enabled and driver pedals were merely **not literally True**. Missing/unknown pedal values therefore counted as no intervention. It also admitted invalid CAN status and did not independently confirm the observed CarParams ownership (`openpilotLongitudinalControl=false`, `pcmCruise=true`) before labeling factory-ACC-conditional samples. The legacy `comfort.p90AbsJerkMps3` included all driving, without cruise or driver-intervention eligibility. These are research attribution defects; there is no evidence they altered the previously validated private 20-R-log ACC figures because that analyzer is a separate implementation.

## Narrow fail-closed correction

- Require the observed exact stock factory-ACC CarParams flags, `carState.cruiseState.enabled is True`, `canValid is True`, and both `gasPressed is False` and `brakePressed is False`. Unknown is not unpressed. Require a finite moving speed and positive finite lead distance, with the pre-existing **past-only** bounded carState-to-model-lead join. This does not establish that the **OEM radar** supplied the lead, nor that the OEM selected a target following gap.
- Contradictory CarParams observations permanently invalidate the entire recording's qualified headway and factory-ACC-motion aggregates, including earlier observations; a later matching snapshot cannot silently rehabilitate those samples. Prior legacy all-driving counters remain separate.
- Add `factoryAccObservedMotion` for absolute derivatives of the **observed vehicle** `aEgo` only over consecutively eligible sampled carState values (0.03–0.3 s intervals). Reset derivative history across cruise-off, pedal activity, unknown pedal state, invalid CAN, unsupported source, nonfinite acceleration or excessive gaps. **No OEM throttle, brake, commanded acceleration or longitudinal controller signal is inferred.** This finite-difference jerk is noisy, unfiltered, Q-log sampled and not a comfort acceptance criterion.
- Version the `leadHeadway` and `factoryAccObservedMotion` qualification as `factory_acc_pedal_free_can_valid_source_pinned_qlog_v1`. Never compare those values against older unqualified route reviews or against observations with missing source-consistency evidence. The existing legacy `comfort` metric remains clearly unconditioned; it must not be relabeled factory ACC.
- Remaining caveat: the streaming Q-log metric class does not currently carry explicit segment identifiers for individual lead and vehicle records. Cross-boundary joins within its sampling-age limit are not independently excluded. Therefore the new aggregates remain **provisional Q-log observations** and cannot replace the separate segment-consistent private R-log analyzer or satisfy route-held-out release gates.

## Verification and exact commits

- Production-independent **code implementation**: `0ec8af724c75dd504d06b60ecdd73d0925312360`.
- Synthetic fixtures: `f9409e078f0c8916223a65b3331f1ed34b2dd06d`, chronological fixture fix and tested code HEAD **`316d4ebe85e8a7bf761da1a6e1cb4f5b6e6b2838`**.
- [GitHub Actions dedicated offline reviewer #44611219221](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44611219221): **37/37 synthetic tests passed** on the tested code HEAD.
- [GitHub Actions broader analyzer #44611219232](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44611219232): **183 tests run, one skipped, zero failures** on the same commit; installed Chrysler EPS DBC source-provenance fixture also passed.
- New synthetic cases cover valid model-flagged headway, an observed acceleration derivative, ambiguous/missing/pressed pedals, invalid or missing CAN, cruise off, absent/non-factory source, mid-episode pedal interruption, later contradictory CarParams, and rejection of legacy comparisons. No route keys, VIN, raw CAN, S3 credentials or location data are committed.
- **No private R-log/Q-log replay under this patch** and no independent held-out route comparison have been completed. The live scheduled reviewer remains pinned to prior commit `2377c055e1254498307b45aa471dcbda7814f763`. Do not claim real-world driving improvement.
- Separate latest private reviewer logs (not run under this patch) continue to quarantine many multi-segment Q-log candidates as `segment_boundary_regression`; one single-segment bounded trial reached `bounded_trial_saved` with `releaseAuthorized=false`, not accepted baseline or release. This new source change is **not** a fix for those time-ordering rejections.

## Remaining safety/release blockers

High-resolution November 14 +171 s steering saturation evidence; independent, source-exact multiple-drive and held-out lateral stability and lane tracking, including steering-reversal/ping-pong and sampled true lane crossing; actual OEM ACC brake/throttle attribution; PAM-to-EPS control authorization and exact truck topology; owner-approved parked Comma CPU/private-memory validation; and independent control-code, safety and supervised vehicle acceptance. The observed 32 mph constraint concerns *initial* Sunnypilot engagement, not post-engagement minimum steering. Factory parking status does not authorize third-party low-speed steering. **No tuning candidate or vehicle installation authorized.**
