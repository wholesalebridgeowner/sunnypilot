# November 18 — ACC radar provenance and mixed-route integrity gate

Status: **read-only analysis correction; not a driving-control or installer change**.

## Source and validation scope
- Recorded Sunnypilot/schema pin: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, with a still-unverified dirty on-device working tree.
- Prior private Railway comfort execution: one-shot `ram-private-comfort-once` deployment `private-railway-id`, successfully executed from source `c54b28ec58ecbac7d655c33a40bfcd44a9500309`, **before** the provenance correction below.
- This branch is stacked on draft PR #3; the baseline `ram1500-custom-development`, the steering/Claude branches, and existing Railway deployments remain unchanged.

## Newly identified observational integrity risk
`ramtools.acc_motion_integrity` previously performed a global radarState as-of join across all selected segments. A radar lead frame at the end of one segment could classify carState samples in the next segment when the frame was within the nominal 250 ms freshness window. With equal timestamps, an irrelevant segment's radar row could also win the global sorted lookup. Segments also repeat across different routes, making an accidental multi-route input particularly problematic.

## Narrow correction
1. RadarState records are indexed **per verified integer segment**. For each carState, use only that segment's at-or-before radar record. Absent, stale and unscoped radar rows become `lead_unavailable`, not inferred confirmed leads.
2. `investigate_comfort.py` refuses input files with missing recognizable route/segment naming or multiple routes. Internally compared route keys are never printed.
3. The separate observed following-gap episode analyzer now also rejects radar observations without verified segment identity, rejects fresh carState from a different segment, and never joins a sustained following episode across segment boundaries.
4. Eight additional deterministic tests challenge cross-segment freshness, equal-timestamp interleaving, missing radar segment provenance, valid adjacent files, mixed-route/unidentified paths, adjacent following intervals, mismatched carState and unknown radar segment. Fixtures contain only synthetic names and records.

## Previous real-data aggregates (NOT post-fix validation)
The *preceding* private Railway run decoded **316,310 messages from three completed R-logs**, reporting zero truncated files. It found **six** qualifying observed-motion intervals: three model-flagged lead intervals totaling **40.618 seconds** and three no-confirmed-lead intervals totaling **46.240 seconds**. Their reported p90 median-window jerk proxies were **0.476** and **0.218 m/s³**, respectively. The same run found three observational headway episodes totaling 40.507 seconds, with median observed gap 1.179 seconds.

These numbers demonstrate the private decoder ran successfully; they **must not be treated as recalculated with the new segment gates**. Also, time-correlated intervals are not independent trials. The model/radar lead flags are not evidence of what the OEM ACC ECU targeted, and `carState.aEgo` is not a measured throttle position or factory acceleration command. Quantitative results may change after rerunning the new logic privately.

## Remaining gates
- CI must validate the latest branch commit; all new changes are analysis-only and are not installed on Comma.
- Rerun the source-pinned private comfort study with **this exact tested commit** and compare quality/episode counts against the earlier run before drawing behavioral conclusions.
- Establish OEM ACC gap-setting and throttle/brake command provenance separately, if legally and technically supported by authenticated read-only documentation. Never equate an observational lead/headway statistic to a factory control target.
- Continue the separate November 14 +171s steering saturation and parking PAM→EPS network-ownership investigations, without CAN transmission or ECU authorization changes.

No raw compressed logs, raw CAN bytes, GPS, VIN, device identifiers, or route keys appear in this report.
