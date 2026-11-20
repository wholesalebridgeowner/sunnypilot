# 2026-11-19 — source and segment provenance gates for private Q-log route comparisons

**Status:** offline analysis/fixture improvement only; no vehicle release, calibration or real-route replay authorization. This work is stacked on draft PR #40, separate from `ram1500-custom-development`, PR #1, PR #2, and Claude's ongoing reviews.

## Problem and scope

The scheduled private route comparator only checked section-level qualification strings for model-phase lane and factory-ACC observations. A hand-assembled or legacy report with the same labels but a different recorded source, or without proven per-file boundaries, could still produce numerical deltas. More importantly, it compared **unqualified all-driving jerk** and **raw steer-saturation event counts** regardless of verified segment provenance or matched active-driving exposure. Such deltas could be mistaken for evidence of better controller damping, fewer faults or greater comfort, despite the absence of comparable driving opportunity.

These are concrete *software acceptance and reporting risks* shown from source inspection. They are **not findings that the truck changed behavior** or that historic private reports were in fact wrong.

## Fail-closed correction

- The comparator now requires **both** reports to specify the exact recorded build `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` and `coverage.segmentIsolatedMetrics is True` before producing **any** numeric comparison.
- Otherwise `baselineAvailable` truthfully reports that a prior record exists, while `metrics` stays empty and `comparisonSuppressed` gives the fixed nonidentifying reason `source_or_segment_provenance_unverified`.
- Only same-version, explicitly quality-gated **sampled lane model-phase** and **factory-ACC-conditional following headway and observed acceleration jerk** can appear in `metrics`. Their existing section-specific quality and factory-ownership gates remain required.
- Raw steer-saturation event episodes and unfiltered all-driving acceleration jerk are still shown as individual route observations where appropriate, but **no longer receive cross-route plus/minus deltas**. Their eligible exposure, traffic, route and conditions are not normalized.
- No new steering constants, longitudinal targets, release flags or device behavior are introduced.

## Reproducible validation

The implementation and three new synthetic fixtures are in `ram1500-development/auto_review.py` and `ram1500-development/test_auto_review.py`, code/test commit `637271ef3670191fba016770218d3033cd465115`. The fixtures use invented values only; they assert valid same-source comparisons, suppression of unqualified jerk/alert differences, rejection of missing or conflicting source/segment metadata, and fail-closed treatment of an unsegmented current recording.

**Verified GitHub CI on documentation commit `3b5f43397b65518198148a622344e531fc66b1c4`:** [dedicated Q-log reviewer run #44631130536](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44631130536) **46/46 tests passed**, including all new synthetic cases. [General Ram telemetry run #44631130577](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44631130577) **192 tests, one skipped, zero failures**, including separately checked original installed-build EPS source/DBC SHA provenance. Both workflows completed successfully. These results verify Python fixtures, not real-route behavior. The live Railway reviewer remains on earlier isolated research commit `2377c055e1254498307b45aa471dcbda7814f763`, and its observed outcome was `no_new_eligible_routes` with 16 eligible inventory candidates as of 2026-11-19 22:35 UTC. This patch is *not* privately replayed or deployed to the collector, device or active reviewer.

## Remaining release blockers

- Source-pinned high-resolution November 14 +171-second steering saturation data are still unavailable; the earlier Q-log observation is not a command-to-EPS diagnosis.
- Independent held-out route analysis is needed to evaluate ping-pong, lane tracking, trajectory crossing, steering clipping and takeover exposure.
- Factory ACC selected following gap and OEM throttle/brake commands have not been independently attributed; reported sampled headway is not a target.
- OEM PAM-to-EPS CAN-C architecture and factory parking flag are observed, but not the specific control/authorization semantics of this truck.
- No approved parked Comma/Panda CPU/memory check, supervised closed-course validation, independent control review or install authorization has occurred.

The exact recorded source and CAN safety gates must remain intact. No private raw logs, route identities, VIN, GPS, ECU credentials or individual CAN frames belong in public reports.
