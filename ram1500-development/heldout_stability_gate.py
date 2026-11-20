#!/usr/bin/env python3
"""Fail-closed, aggregate-only Ram held-out steering evidence scorecard.

Reads a *locally prepared* manifest of anonymized, independently attested
paired offline replays. Does not decode raw logs, publish identifiers, change
vehicle software, assert OEM command authority or approve vehicle release.
"""
import argparse
import json
import math
from pathlib import Path

SOURCE = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
QUALITY = "source_pinned_rlog_independent_lateral_and_physical_lane_ground_truth_v1"
# Exactly comparable paired replay quantities. "Smaller" must mean no worse;
# no weights or fabricated target controller values are introduced.
FLOAT_METRICS = (
    "laneCenterRmseMeters",
    "lateralOvershootP95Meters",
    "steeringReversalsPerMinute",
    "steeringOscillationEnergy",
    "nativeCommandClippingFraction",
    "laneChangeEntryPeakLateralAccelMps2",
    "laneChangeRecenterP95Meters",
)
COUNT_METRICS = ("steerSaturatedEpisodes", "driverOverrideEpisodes")
QUALITY_FLAGS = (
    "schemaPinned", "segmentBoundariesVerified", "freshHealthyLateralOwnership",
    "independentLateralPositionMeasured", "physicalLaneCrossingVerified",
    "controllerPhaseResolved", "noUnexplainedDataGaps", "identicalReplayInput",
)
MIN_INDEPENDENT_ROUTES = 2
MIN_ELIGIBLE_SECONDS = 60.0  # Conservative screen, NOT a vehicle safety standard.


def finite_nonnegative(value):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0)


def evaluate(doc):
    """Return a privacy-preserving decision: never echo route tokens/measurements.

    An operator's provenance attestations are NOT cryptographic proof. A
    positive result is ONLY eligible for independent offline review.
    """
    problems = []
    if not isinstance(doc, dict):
        return {"status": "INSUFFICIENT_EVIDENCE", "reasons": ["invalid_manifest"],
                "vehicleReleaseAuthorized": False}
    if doc.get("schemaVersion") != 1 or doc.get("sourceCommit") != SOURCE:
        problems.append("pinned_source_or_manifest_mismatch")
    if doc.get("measurementQualification") != QUALITY:
        problems.append("missing_independent_ground_truth_qualification")
    if doc.get("purpose") != "OFFLINE_PAIRED_REPLAY_NOT_INSTALLABLE":
        problems.append("not_explicitly_offline_only")
    if doc.get("holdoutFrozenBeforeCandidateFit") is not True:
        problems.append("holdout_not_precommitted")
    if doc.get("independentReviewComplete") is not True:
        problems.append("independent_review_pending")
    dev = doc.get("developmentRoutes")
    hold = doc.get("heldoutRoutes")
    if not isinstance(dev, list) or len(dev) < MIN_INDEPENDENT_ROUTES:
        problems.append("insufficient_independent_development_routes")
        dev = []
    if not isinstance(hold, list) or len(hold) < MIN_INDEPENDENT_ROUTES:
        problems.append("insufficient_independent_heldout_routes")
        hold = []
    # Opaque tokens live only in local memory. Never include them in output.
    devkeys = [r.get("token") if isinstance(r, dict) else None for r in dev]
    holdkeys = [r.get("token") if isinstance(r, dict) else None for r in hold]
    keys = devkeys + holdkeys
    if (any(not isinstance(k, str) or not k or len(k) > 128 for k in keys)
            or len(set(keys)) != len(keys)):
        problems.append("route_identity_missing_repeated_or_overlapping")
    if any(not isinstance(r, dict) or r.get("sourceCommit") != SOURCE for r in dev):
        problems.append("development_source_not_pinned")
    improved = 0
    for i, route in enumerate(hold):
        label = f"heldout_{i + 1}"
        if not isinstance(route, dict) or route.get("sourceCommit") != SOURCE:
            problems.append(label + "_source_not_pinned")
            continue
        q = route.get("quality")
        if not isinstance(q, dict) or any(q.get(k) is not True for k in QUALITY_FLAGS):
            problems.append(label + "_quality_or_attribution_unverified")
        if type(route.get("laneChangeEpisodes")) is not int or route["laneChangeEpisodes"] < 1:
            problems.append(label + "_physical_lane_change_unobserved")
        baseline, candidate = route.get("baseline"), route.get("candidate")
        if not isinstance(baseline, dict) or not isinstance(candidate, dict):
            problems.append(label + "_missing_paired_replay")
            continue
        # Both runs must evaluate the same qualified window/inputs.
        seconds = [x.get("eligibleSeconds") for x in (baseline, candidate)]
        samples = [x.get("eligibleSamples") for x in (baseline, candidate)]
        if (not all(finite_nonnegative(v) for v in seconds)
                or seconds[0] < MIN_ELIGIBLE_SECONDS
                or seconds[0] != seconds[1]
                or not all(type(v) is int and v > 0 for v in samples)
                or samples[0] != samples[1]):
            problems.append(label + "_insufficient_or_unpaired_exposure")
        for key in FLOAT_METRICS + COUNT_METRICS:
            a, b = baseline.get(key), candidate.get(key)
            if (not finite_nonnegative(a) or not finite_nonnegative(b)
                    or (key in COUNT_METRICS and (type(a) is not int or type(b) is not int))
                    or (key == "nativeCommandClippingFraction" and (a > 1 or b > 1))):
                problems.append(label + "_invalid_" + key)
            elif b > a:
                problems.append(label + "_regressed_" + key)
            elif b < a:
                improved += 1
    if not improved:
        problems.append("no_measured_nonregressing_improvement")
    # No release decision can be made from an aggregate scorecard alone.
    return {
        "status": ("OFFLINE_EVIDENCE_ELIGIBLE_FOR_REVIEW"
                   if not problems else "INSUFFICIENT_EVIDENCE"),
        "reasons": sorted(set(problems)),
        "independentDevelopmentRouteCount": len(dev),
        "independentHeldoutRouteCount": len(hold),
        "metricImprovementsObserved": improved,
        "vehicleReleaseAuthorized": False,
        "calibrationAuthorized": False,
        "interpretation": (
            "Attested offline paired-replay gate only; no causal or real-world "
            "safety claim. Independent log provenance, hardware and controlled "
            "vehicle testing remain separate mandatory gates."
        ),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path, help="Local private aggregate-only input")
    args = p.parse_args()
    try:
        doc = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = evaluate(doc)
    except (OSError, ValueError, TypeError):
        result = {"status": "INSUFFICIENT_EVIDENCE",
                  "reasons": ["manifest_unreadable_or_invalid"],
                  "vehicleReleaseAuthorized": False, "calibrationAuthorized": False}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "OFFLINE_EVIDENCE_ELIGIBLE_FOR_REVIEW" else 2)


if __name__ == "__main__":
    main()
