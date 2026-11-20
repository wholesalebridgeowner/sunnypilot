# November 19 — Q-log lane model-phase attribution is not vehicle lane-change proof

**Scope:** Offline research-only draft PR #38, stacked on PR #37. Original `ram1500-custom-development`, concurrent Claude branches, private log collector, active Comma software, Panda/OEM protections and CAN actuator paths are untouched. No vehicle calibration or installation is authorized.

## Defect and correction

The original private scheduled Q-log reviewer treated `drivingModelData.meta.laneChangeState=laneChangeStarting` followed by **any** other model state as a completed starting window, provided a steering-rate sample occurred. It did not test whether Comma was actually steering. As a result a model-only preparatory/aborted transition with `carControl.latActive=false` could be misreported as lane-change comfort, and compared across routes.

The revised analyzer counts a sampled model start-to-finish window only when **all** of these are true:
- Explicit `laneChangeStarting` followed by `laneChangeFinishing`, with consistent known left/right direction and observed steering-rate evidence.
- Past-only `carControl.latActive is True` observations within 150 ms at model entry, throughout subsequent sampled vehicle/control states, and at finishing; any dropout, missing/stale evidence or excessive control-sample gap irreversibly disqualifies the window.
- Fresh, past-only `carState` health: `canValid is True`, `steerFaultTemporary is False`, `steerFaultPermanent is False`, and `steeringPressed is False` at observed points. Missing values fail closed. A model phase that leaves Starting without Finishing is rejected.
- Comparison with an older route is permitted only if **both** metrics carry the new explicit qualification marker `model_start_to_finish_fresh_active_healthy_qlog_v2`. The previous unqualified `medianStartingSeconds` or steering-rate series cannot be silently interpreted as an equivalent baseline.

Aggregate rejection reasons contain only constant, anonymous categories, not timestamps, route keys, CAN payloads, VINs, GPS or device identifiers. The existing published field `completeStartingWindows` now describes only *qualified sampled model-phase windows*, **not verified physical crossing or trajectory recentering**. Q-logs are sampled and may miss short faults/driver intervention; full-resolution R-logs with route/segment-held-out evidence and explicit lane position are still necessary.

## Independently verified offline regression

- Exact implementation/test source commit: **`049ea2476261322653aa4e8a913c9c5c89a24209`** (implementation `f1eb567f1c4c246a7f4a656d2197f1e7af34a45e`; synthetic tests `64ecf284e2bf8f9dcd0504620deabef7b5a74d18`; fixture correction `049ea247...`).
- [Dedicated automated-review GitHub workflow #44601108957](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44601108957): **32/32 tests passed**.
- [General telemetry analyzer GitHub workflow #44601108875](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44601108875): **178 tests run, one skipped, zero failures**. Earlier first-run tests failed one obsolete unqualified comparison fixture; it was updated for the explicit qualification marker and the complete rerun passed.
- Synthetic fixtures cover valid active and healthy model finish, no lateral ownership, intervening lateral disengagement, stale lateral/vehicle-state evidence, CAN invalidity, temporary/permanent EPS faults, driver steering override, aborted model phase and incomparable historical lane metrics.

**Real-data boundary:** The existing Railway `ram-scheduled-route-review` research service was deliberately **not repinned** to this PR. It remains deployed at `2377c055e1254498307b45aa471dcbda7814f763`; no real private route was replayed under this patch in this run. Previous 20-R-log morning lane-phase results were zero qualifying windows under the separate high-resolution analyzer; do not present this patch as a new measured driving improvement. No steering, acceleration, OEM ACC gap or actual lane crossing was calibrated.

## Outstanding release gates

November 14 +171 s historical high-resolution R-log, independently held-out stability/reversal and actual lane-crossing trajectory, factory ACC throttle/brake attribution, PAM-to-EPS command ownership, authorized parked Comma CPU/private-memory retest, independent control/safety review and supervised vehicle acceptance remain unfulfilled. The reported November 18 OEM auto-park flag is a verified EPS *status* observation, not third-party steering permission. Remain research-only; no installer, steering parameters, brake/throttle commands, or safety changes.
