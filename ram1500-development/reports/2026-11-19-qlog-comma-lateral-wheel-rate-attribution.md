# Ram Q-log wheel-rate attribution and factory parking isolation (2026-11-19)

**Status: offline analytical change only; not on-road calibration or device deployment.**

## Concrete motivation and evidence

A source-pinned private Railway **cross-segment Q-log aggregate** completed on November 19 local / November 20 UTC. It reported, for a **three-segment** recording, **1,479 carState samples**, **1,479 carControl samples with latActive=false**, and a **255.0 deg/s p90 absolute steering-wheel-rate** across all driving observations. No positive Comma lateral-active samples were reported for that recording. These are private-research aggregate counts only; no route identifier, raw CAN, timestamp of the driver's movements, GPS or payload belongs in this report. Research service: `ram-crosssegment-one-shot`, private runtime aggregate observed November 20 UTC; the executing branch was `ram1500-crosssegment-readonly-pilot` and the review output was **non-release experimental**.

This demonstrates a **measurement-attribution problem**: the old `steering.p90AbsSteeringRateDegPerSecond` is *all vehicle wheel motion*, including motion while Comma lateral control is inactive. It is not evidence of Sunnypilot steering responsiveness, overshoot, or comfort. Independently, the November 18 OEM parking R-log analysis decoded `EPS_2.AUTO_PARK_HAS_CONTROL_2` on the installed build's EPS status frame and measured ~14.6 seconds of factory control while Comma lateral control was inactive. The three-segment aggregate is **not proved to be that OEM parking maneuver**; no cross-route identification is asserted.

## Narrow source change

`auto_review.RouteMetrics` now **keeps** the old `p90AbsSteeringRateDegPerSecond` as a backward-compatible **all-vehicle** raw wheel-rate statistic with an explicit caution and exposure count. A *separate* `p90AbsCommaLateralWheelRateDegPerSecond` and sample count include a carState steering-rate observation only when:
- the immediately past `carControl.latActive` is **exactly true**, with age 0–150 ms;
- the **current** carState has `canValid=true`, no temporary or permanent EPS steering fault, and `steeringPressed=false`;
- observations remain inside an explicitly emitted, independently source-verified log-segment boundary; the latest lateral state is discarded on each boundary.

The new **v1 qualification marker** distinguishes the separately gated wheel-rate series from historical unqualified raw data. No old wheel-rate delta is added to the cross-route comparator. No speed cutoff is assumed: the truck may continue lateral steering below its initial ~32 mph engagement threshold. A clean low-speed latActive observation remains eligible; an OEM parking or manually steered sample with latActive=false is not.

These source checks **do not independently prove** who physically turned the wheel, EPS torque acceptance, actual lateral tracking error, or an on-road control benefit. `steeringPressed=false` is a telemetry gate, not proof of hand absence. The driver/EPS mode and Comma active state can be misaligned within Q-log sampling; full R-log and independent safety review remain mandatory.

## Synthetic regression and release gates

`test_auto_review.py` adds four synthetic tests: unassisted high-rate wheel motion excluded from Comma series; verified Comma active/healthy steering included; stale/disengaged/CAN-fault/driver-override excluded; and segment-boundary/unverified source behavior. Test results must be read from the exact head's GitHub Actions run, not assumed from source.

The scheduled private reviewer is **not** moved to this branch; successful fixture CI cannot authorize a real-route replay or calibration. No private log, VIN, location, CAN payload, on-device code, factory control channel, steering/braking actuation or Panda/OEM safety gate has been altered.

**Still blocked:** historical Nov 14 high-resolution steering saturation attribution; route-held-out stability and lane-centering measurements; factory ACC actuation, not just observed headway; PAM-to-EPS command semantics; operator-approved parked resource test; independent safety and vehicle-release gates.
