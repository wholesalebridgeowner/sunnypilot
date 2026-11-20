# November 20 — factory ACC observed-motion equal-clock quarantine (offline)

**Status:** source-reviewed measurement-integrity correction; synthetic regression pending independent real-route R-log replay. No drive software, EPS, Panda, CAN commands, throttle or brakes changed.

## Source and scope

- Exact recorded truck build: Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (reported dirty, so a byte-exact installed executable remains unverified).
- Base for this review: draft PR #18, commit `4f65a19a5229dec8938ae60efb36193b30e49bdc`, with its source-pinned `DAS_3.ACC_ACTIVE` bus-2 audit and privately recovered morning segments. The main `ram1500-custom-development` branch and Claude PRs #1/#2 remain untouched.
- This adjustment is limited to `ramtools/acc_motion_integrity.py`, its synthetic tests, and this report. It does **not** establish the OEM ACC set gap, throttle position, brake pressure, actual ECU demand or a change in comfort.

## Failure mode and correction

The existing observer sorted `carState` and `radarState` by timestamp, while leaving contradictory equal-clock observations to their input ordering. A duplicate carState time was previously sufficient to split a longitudinal episode, but a conflicting radar pair could choose `leadOne.status` or sensor-source classification based on which observation sorted last. That creates order-dependent observational headway context and can misclassify acceleration or jerk samples.

- Equal-clock **carState** rows: collapse exact duplicates; quarantine contradictory rows and break the continuous episode. No zero-duration derivative or interpolated gap.
- Equal-clock **radarState** rows: deduplicate exact copies within the same verified recording segment; quarantine incompatible copies at that segment/clock. A quarantined clock yields `lead_unavailable`, and a preceding good radar frame cannot be reused to override it.
- Preserve past-only per-segment radar matching, reported pedal/gear/CAN quality gates, and no lead attribution to factory ACC.
- Emit aggregate duplicate/conflict counts only. No raw routes, timestamps, coordinates, VIN, CAN frames or vehicle identifiers are exported.
- Bump observational `measurementQualification` to `observed_factory_acc_motion_equal_clock_integrity_v2`. Prior v1 and new v2 comfort metrics cannot be compared as matched evidence without reprocessing the **same** private inputs and rechecking qualification.

## Reproduction

On the analysis branch, with Python 3.11 and pytest:

```sh
cd ram1500-development
python -m pytest -q tests/test_acc_motion_integrity.py
```

Six new adversarial synthetic tests exercise reversed input ordering, same-clock lead contradiction, no older-radar fallback, identical duplicate collapse, vehicle-state contradictory acceleration and cross-segment isolation. The existing pinned-comfort GitHub Actions workflow also runs this file on pull requests.

**Limitations:** Passing synthetic tests and green CI only validate the parser logic. The private Railway scheduled reviewer remains pinned to earlier code and was not redeployed in this change. There is no v2 real-route replay, no independently matched held-out acceleration/jerk scorecard and no vehicle-ready calibration. OEM factory ACC owns longitudinal actuation; observed `carState.aEgo` is estimated vehicle acceleration, not commanded throttle. Retain full 11-stage safety and release gates.
