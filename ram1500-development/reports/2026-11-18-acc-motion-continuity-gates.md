# November 18 — cruise-enabled longitudinal motion continuity audit

**Status:** offline analytic capability + synthetic fixtures only. **No private November 17 ACC acceleration values measured in this report**; the pinned R-logs need a private run. No throttle or factory ACC target has been independently decoded.

## Scope and provenance

The new `ramtools/acc_motion_integrity.py` accepts the existing privacy-whitelisted `ramtools.logio` decoded records from the installed-source-pinned `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` cereal schema. The `investigate_comfort.py` output now includes `accMotionIntegrity` in addition to existing headway, following, and lane-state summaries.

Run with *completed* private Q-logs or preferably 100 Hz R-logs, using the same verified schema and segregated output directory already required by `investigate_comfort.py`:

```sh
python3 ram1500-development/investigate_comfort.py \
  --schema-dir /private/schema-1f7233cb \
  --out /private/analysis/comfort-integrity.json \
  /private/log-segments/*/rlog.zst
```

Do **not** use a public repo path for inputs or results. The analyzer never emits times, route IDs, segment IDs, CAN payloads, VINs, GPS or individual-sample traces. Operator must inspect `truncatedFiles` before interpreting any result; a partial decode is not complete-route evidence. Real longitudinal report remains blocked until the private container runs this analysis.

## Continuity gates

- Only verified-valid `carState` observations, valid CAN, explicitly enabled cruise, both driver pedals explicitly released, Drive gear, known segment identity, plausible ego speed >2 and <65 m/s, and finite `aEgo` are included.
- Any excluded **intervening** carState row ends a motion episode, even when neighboring accepted timestamps are close. Timestamp duplicates, gaps >250 ms, segment boundaries and observational lead classification changes also end an episode.
- Latest at-or-before radar observation must be <=250 ms old. Invalid/stale/unknown radar observations are **unavailable**, not 'no lead' or evidence of factory-free cruising. The `leadOne.radar` boolean is logged separately from unknown source provenance.
- At least 10 samples and two consecutive seconds are required per episode. The minimum is a diagnostic-quality gate, **not** a claim of independent driving experiments.
- For each observed lead class, the tool reports episode counts, exposure duration, median and p90 absolute `carState.aEgo`, raw 1/dt finite-difference jerk, and a less-noisy half-second median-acceleration-bin jerk proxy. Window size and pair counts are disclosed. Empty groups return null percentiles rather than fabricated zeros.

## Interpretation boundaries

1. `carState.aEgo` describes estimated acceleration, **not an electronically measured throttle command**, brake percentage or factory longitudinal controller target. The recorded Ram interface appears to retain factory ACC longitudinal ownership, but exact ACC mode and programmed following-distance setting have not been decoded.
2. Radar/model lead classification may not match what the factory ACC ECU chose to follow. Do not infer a set headway from observed gaps.
3. Jerk derived from acceleration estimates is noisy, and half-second smoothing is an **observational jerk proxy**, not proof of perceptual comfort or direct pedal actuation. R-log frequency improves temporal detail but cannot eliminate these uncertainties.
4. No source-grounded reason yet exists to change throttle/brake/steering logic, Panda limits, or OEM controls.

## Validation and next safe task

Ten synthetic regression scenarios verify censoring around pedal/ACC/CAN transitions, segment and timing gaps, causal radar sampling, invalid lead state, model/radar provenance, unknown sensor inputs, timestamp duplicates, quality parameter rejection, and empty results. The fixture is added to `Ram pinned comfort fixture tests` CI. Real-data validation, including Nov 17 high-resolution ACC-following exposure, remains **pending**.

Independently validate lead-source definitions and actual factory ACC target/mode from supported read-only OEM diagnostics before making causal or throttle-smoothness statements. Keep parked logger CPU/memory testing operator-approved; do not install on the active truck.
