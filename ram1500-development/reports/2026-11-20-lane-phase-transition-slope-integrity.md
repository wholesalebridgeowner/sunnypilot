# Phase-boundary wheel-rate slope integrity — 2026-11-20

**Scope:** read-only, offline Ram lane-change comfort measurement correction; not a calibration, controller candidate, physically measured lane crossing, or vehicle release. Parent draft PR #60 source commit `80eef83d6e664ad778fedc1a68e1a1cef76cf6b1`; recorded source reference `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (device working-tree dirtiness remains unverified).

## Observational gap corrected

The previous joined maneuver kinematics used adjacent `carState.steeringRateDeg` samples *inside* the model-reported `laneChangeStarting` and `laneChangeFinishing` phases, but never the adjacent pair **across** their shared phase transition. A synthetic maneuver with steady wheel rate inside both phases and a sharp step exactly at the transition therefore reported a zero peak wheel-rate derivative. This could make an abrupt observed change appear smooth during offline comfort screening.

For sequences already qualified as continuous same-segment starting→finishing→off, the corrected implementation computes the maximum absolute sampled wheel-rate slope over **all adjacent healthy carState pairs**, including the single starting-to-finishing boundary pair. It also reports a separate P90 aggregate of the boundary-pair absolute slope. The current maximum statistic is now explicitly versioned `phase_boundary_inclusive_v2`; older phase-internal-only values must be recalculated using identical recordings and windows before numerical comparison. Nothing here estimates lateral acceleration, physical overshoot or lane position.

## Fail-closed evidence constraints

The existing source coalescer rejects contradictory same-clock observations. A maneuver needs at least two `carState` samples per phase; timestamps must be strictly increasing with no >150 ms adjacent gap under the default gate, with verified start/transition/end coverage. `carState` must be valid, same-segment, CAN-valid, EPS-fault-free, without driver steering override, with finite reported speed and wheel-rate at or above the observation speed gate. A complete model-phase sequence and fresh, past-only, continuous `carControl.latActive=true` ownership are required. If these conditions fail, no numeric slope is exported. Thresholds are analysis qualifications, not steering calibration values.

## Synthetic regression and reproducibility

Three adversarial tests cover: a within-phase-steady but boundary-abrupt change (previous blind spot), a gap at the phase transition, and contradictory or unhealthy vehicle observations. They assert order invariance, no false zero/qualified result on missing evidence, and privacy exclusions. Run `cd ram1500-development && python -m pytest tests/test_lane_maneuver_sequences.py tests/test_lane_phase_quality.py -q`. The existing `ram-lane-sequence-tests.yml` workflow is reused (no new workflow file).

**Not done:** no private real-route replay of this v2 metric, independently verified physical lane geometry/centering, held-out calibration acceptance, OEM longitudinal actuator attribution, approved parked device diagnostics or supervised road safety validation. No source data, route identifiers, coordinates, VIN, credentials or raw CAN frames published. No device/Panda/ECU software, active controller, private Railway services or vehicle bus traffic modified.
