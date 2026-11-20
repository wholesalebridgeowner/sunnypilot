# Ram private Q-log — one radar estimate per sampled car state (2026-11-19)

**Status:** read-only, synthetic-verified instrumentation improvement in draft PR #43, **not a measured driving-performance or release result**.

## Source / provenance

- Historical recorded Comma source: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. The physical device reported a dirty working tree, so this pin does not independently attest its running binaries.
- Code is isolated on `ram1500-qlog-carstate-radar-one-to-one-20261119`, based on draft PR #42's `ram1500-qlog-model-direction-integrity-20261119` head. The base vehicle-development and Claude research branches are unchanged.
- Changed only `ram1500-development/auto_review.py`, `test_auto_review.py`, and this report. No private raw driving data, route keys, timestamps, location, vehicle IDs, OEM status payloads or credentials included.

## Measurement attribution defect

The private Q-log aggregator previously appended **each** eligible `radarState.leadOne` estimate independently, while reusing the same at-or-before `carState.vEgo` for every radar observation inside a bounded 350 ms matching window. If a radar stream sampled more frequently than the car-state stream, one car-state speed estimate could have disproportionate influence on the median/p10/p90 observed headway. This was not an OEM target following-gap estimate and never proved OEM radar source ownership.

**Corrected behavior:** for each sampled `carState` record, retain the latest time-eligible, source/ACC/pedal/CAN-qualified model lead estimate; commit at most one headway value when the next carState sample or segment boundary arrives. A subsequent lost or invalid lead clears the pending value. An older out-of-order radar time cannot overwrite a newer observation. `report()` includes the uncommitted latest candidate without side effects, including when invoked repeatedly. Segment markers flush and reset the association instead of borrowing vehicle state across files.

The headway/motion provenance marker advances from `factory_acc_pedal_free_can_valid_source_pinned_segment_isolated_qlog_v2` to `factory_acc_pedal_free_can_valid_source_pinned_segment_isolated_latest_radar_per_carstate_qlog_v3`. **Old and new sampled headway distributions cannot be directly compared.** The already separate observed vehicle-acceleration jerk algorithm is unchanged, but the shared factory metric comparison marker is intentionally versioned conservatively.

## Validation

Exact code and six-fixture-test HEAD: `bf192e030e20ebb57b696d7a849deb91c76839cc`.

- [Dedicated Ram automated-review Actions #44648177692](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44648177692): **55/55 synthetic tests passed**.
- [General offline Ram analyzer Actions #44648174035](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44648174035): **201 tests run, one skipped, no failures**; recorded-build EPS DBC/mapping provenance check passed.
- New synthetic tests cover multiple radar samples for one car state, a newer lost-lead frame, out-of-order radar observations, boundary flushing without cross-file pairing, subsequent conflicting factory ACC CarParams invalidating prior headway, and version-incompatible comparisons.

The existing private Railway scheduled reviewer remains pinned to the older source `2377c055e1254498307b45aa471dcbda7814f763`; inspected logs showed `no_new_eligible_routes` with 16 inventory candidates. **This PR has not been deployed for private real-route replay**. The Q-log approximation cannot resolve R-log high-rate OEM lead ownership, exact selected headway, factory throttle/brake commands, or model lane crossing.

## Remaining release blockers

Nov 14 +171s high-resolution steering R-log coverage is not verified, held-out lane-centering and steering-reversal stability are inadequate for calibration, factory ACC actuation and PAM-to-EPS authorization are unverified, parked-device CPU/RSS testing awaits operator approval, and independent vehicle safety acceptance and controlled testing have not occurred. No numerical steering delay, torque, throttle, brake, Panda safety or OEM control setting was modified. No installable release is authorized.
