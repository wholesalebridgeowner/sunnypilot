# Ram 1500: independent vehicle-state clock integrity in steering reversal observations

**2026-11-20 | Read-only research | Draft, not installable**

## Source and scope

- Exact recorded vehicle software reference: Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, with on-device working-tree dirtiness unresolved.
- New branch is stacked exclusively on draft PR #53 at parent `728b3862e5590183c4a0badfc34ff945e9d3a7ec`, rather than altering `ram1500-custom-development` or Claude's concurrent PR #1 / #2.
- Addresses 11-step development plan **steps 5, 6 and 8**: steering-observation qualification, held-out stability measurement integrity and reproducible offline regression. It does not solve measured physical lane position or release gates.
- Files changed: the offline `ramtools/lateral_stability_observer.py`, its fixture tests and this report. No device/Railway collector or controller changes.

## Defect and reproducible correction

The earlier straight-driving observer quarantined conflicting equal-clock `carControl`, curvature and `carOutput` sources, but **did not canonicalize `carState` itself**. Two carState rows with an identical `logMonoTime` yet contradictory wheel-rate sign, speed, CAN validity or driver intervention could be counted as separate steering evidence. The result depended on input ordering and could distort qualification and reversal continuity, particularly near a detected sign change.

The revised observer first groups each segment's vehicle samples by strictly validated integer nanosecond clock:

1. Exact duplicate state rows (including matching scalar types) count **once**, not twice.
2. Any conflicting values at the same timestamp quarantine **that entire clock**, regardless of arrival order, increment `ambiguous_vehicle_timestamp` once, and **break reversal continuity**. A later valid source timestamp can resume observation, but earlier sign history cannot bridge the rejected instant.
3. Boolean, negative, missing or fractional vehicle timestamps are invalid before sorting. Contradictory `None`/unknown values do not become default valid measurements.
4. `measurementQualification` advances to `straight_wheel_rate_reversal_vehicle_clock_integrity_v3`, so v2 and v3 steering summaries cannot be combined as equivalent observations.

The following remain separately required for eligible wheel-rate observations: `carState.valid` and `canValid` exactly true, Drive gear, bounded speed, no reported override or steering fault, fresh past-only lateral activation, small requested curvature, fresh non-limited native steering output, and bounded continuous sample gaps. The 250-ms neutral dwell introduced in PR #53 is unchanged; it is **not** a tuning recommendation.

## Offline regression coverage

Synthetic fixtures include opposite-sign wheel rates on a shared vehicle timestamp, contradictory CAN/vehicle-health evidence in both arrival orders, repeated identical timestamp duplicates, malformed clock types, and a v2/v3 measurement-mixing rejection. Existing reversal timing, neutral dwell, source freshness, torque-limited, steering intervention and baseline aggregation fixtures still apply.

Run: `cd ram1500-development && python -m pytest tests/test_lateral_stability_observer.py -q`; existing pinned-comfort and full-route workflows include the observer tests. Review the exact GitHub Actions run for this PR's final HEAD rather than assuming these tests prove live performance.

## Evidence boundary and remaining plan

This fix corrects **synthetic measurement integrity**, not physical lane-center oscillation or steering comfort. Reversal frequency is a wheel-rate proxy, not independent lane error, cross-track oscillation, trajectory overshoot or physical lane-change recentering. In particular, the existing source-pinned morning measurement had only 18.859 seconds of strictly eligible straight driving across several short windows; it cannot establish robust held-out handling. No real private R-log replay of the revised v3 qualification is claimed.

Next validated steps still require multiple distinct laterally active drives with complete source/segment provenance, matched held-out controller replay plus actual lane-position evidence and safety scorecards, meaningful lane-change windows, further factory ACC observational sourcing, and parked-only device logger acceptance with explicit owner approval. No parameter, brake/throttle command, EPS access, Panda safety, installed Comma software or public log content was changed.
