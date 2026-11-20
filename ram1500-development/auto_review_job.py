#!/usr/bin/env python3
"""Scheduled private S3 route analyzer. Never contacts or commands a Comma.

Reviews one eligible route per run to bound cost and memory. Stores aggregate
analysis in the existing private bucket, never raw telemetry in public logs.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from auto_review import SCHEMA_COMMIT, compare, summarize_log_messages
from auto_review_clock_audit import audit_clock_order, ClockAuditSourceError
from auto_review_bounded_order import source_pinned_bounded_messages, OrderingTrialError
from auto_review_crosssegment_order import source_pinned_route_messages, RouteOrderingError

PATH = re.compile(r"^logs/(.+)/([0-9]+)/qlog\.zst$")
MAX_SEGMENTS = 96
MAX_COMPRESSED = 25_000_000
QUIET_HOURS = 0.5  # 30-minute upload quiet period; later uploads produce a new fingerprint
LOOKBACK_DAYS = 14
PREFIX = "automated-ram-reviews/v1/"

def group_routes(objects, now, *, quiet_hours=QUIET_HOURS, lookback_days=LOOKBACK_DAYS):
    """Route grouping, strict contiguous segment gate; newest route first."""
    groups = defaultdict(list)
    for o in objects:
        match = PATH.fullmatch(o.get("Key", ""))
        if not match:
            continue
        updated = o.get("LastModified")
        size = o.get("Size")
        route, segment = match.groups()
        # Retain all objects so recent or invalid segments invalidate the route.
        groups[route].append((int(segment), o))
    candidates = []
    for route, segments in groups.items():
        if not 1 <= len(segments) <= MAX_SEGMENTS:
            continue
        if any(not isinstance(o.get("LastModified"), datetime)
               or o["LastModified"].tzinfo is None
               or not (now - timedelta(days=lookback_days) <= o["LastModified"]
                       <= now - timedelta(hours=quiet_hours))
               or not isinstance(o.get("Size"), int)
               or not (0 < o["Size"] <= MAX_COMPRESSED)
               for _, o in segments):
            continue
        nums = sorted(s for s, _ in segments)
        if nums != list(range(len(nums))):
            continue
        # Only accept a route if *all* of its known objects have aged out.
        entries = sorted(segments)
        latest = max(o["LastModified"] for _, o in entries)
        if latest > now - timedelta(hours=quiet_hours):
            continue
        h = hashlib.sha256()
        h.update(SCHEMA_COMMIT.encode())
        for segment, o in entries:
            h.update(f"{segment}:{o['Size']}:{o.get('ETag','')}:".encode())
            h.update(o["LastModified"].isoformat().encode())
        route_hash = hashlib.sha256(route.encode()).hexdigest()[:20]
        candidates.append({"hash": route_hash, "route": route, "items": entries,
                           "fingerprint": h.hexdigest()[:24], "updated": latest})
    return sorted(candidates, key=lambda x: x["updated"], reverse=True)

def route_key(candidate):
    return PREFIX + "reports/" + candidate["hash"] + "/" + candidate["fingerprint"] + ".json"

def clock_audit_key(candidate):
    return PREFIX + "clock-audits/" + candidate["hash"] + "/" + candidate["fingerprint"] + ".json"

def bounded_trial_key(candidate):
    return PREFIX + "bounded-trials/" + candidate["hash"] + "/" + candidate["fingerprint"] + ".json"

def regression_origin_key(candidate):
    return PREFIX + "regression-origins/" + candidate["hash"] + "/" + candidate["fingerprint"] + ".json"

def read_json_or_none(s3, bucket, key):
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(raw)
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            return None
        raise

def put_private_json(s3, bucket, key, payload):
    s3.put_object(Bucket=bucket, Key=key, Body=(json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n").encode(),
                  ContentType="application/json")

class SourcePinError(ValueError):
    """A selected segment does not prove the full installed-build source."""


def pinned_qlog_messages(paths, reader):
    """Discard replayed old-clock initData AFTER exact per-segment verification.

    Q-log initData headers can repeat at segment boundaries with timestamps
    from an older process. They are metadata, not route motion observations.
    Other non-monotonic traffic is never reordered or silently discarded.
    """
    for path in paths:
        headers = 0
        for message in reader(path):
            kind = str(message.which())
            if kind == "initData":
                recorded = getattr(message.initData, "gitCommit", None)
                if type(recorded) is not str or recorded != SCHEMA_COMMIT:
                    raise SourcePinError("recorded source is missing or does not match")
                headers += 1
                continue
            yield message
        if not headers:
            raise SourcePinError("segment has no exact source header")


def anonymous_clock_counts(report):
    """Explicit allowlist for console-safe diagnostics; never emit private keys."""
    if not isinstance(report, dict) or report.get("status") != "clock_order_audited":
        return None
    names = ("segmentsVerified", "observations", "inversionsOverHalfSecond",
             "withinSegmentInversions", "crossSegmentInversions")
    counts = {key: report[key] for key in names
              if type(report.get(key)) is int and report[key] >= 0}
    bands = report.get("inversionSizeBands")
    if isinstance(bands, dict):
        counts["bands"] = {key: bands[key] for key in
                           ("underTwoSeconds", "twoToTenSeconds", "overTenSeconds")
                           if type(bands.get(key)) is int and bands[key] >= 0}
    return counts


def run_quarantine_clock_audit(s3, bucket, selected, reader):
    """Privately classify a quarantined recording without rewriting its tombstone.

    Exactly one anonymous, immutable diagnostic per fingerprint. Read-only
    source-pinned inspection of timestamp ordering; NEVER a driving report.
    """
    with tempfile.TemporaryDirectory(prefix="ram-clock-audit-") as folder:
        paths = []
        for segment, obj in selected["items"]:
            output = Path(folder) / str(segment) / "qlog.zst"
            output.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(output))
            if not (0 < output.stat().st_size <= MAX_COMPRESSED):
                raise ValueError("Invalid compressed input size")
            paths.append(output)
        try:
            diagnostic = audit_clock_order(paths, reader)
        except ClockAuditSourceError:
            diagnostic = {"status": "source_unverified", "auditVersion": 1}
        except Exception:
            # Never persist exception strings: decoder failures may echo raw data.
            diagnostic = {"status": "reader_or_clock_invalid", "auditVersion": 1}
    diagnostic.update({"routeHash": selected["hash"],
                       "inputFingerprint": selected["fingerprint"],
                       "segmentCount": len(paths)})
    put_private_json(s3, bucket, clock_audit_key(selected), diagnostic)
    return {"status": "clock_diagnostic_saved",
            "diagnostic": diagnostic["status"], "segments": len(paths),
            "anonymousClockCounts": anonymous_clock_counts(diagnostic)}


def safe_trial_rejection_reason(exc):
    """Map only known local failure tokens to a privacy-safe categorical reason.

    Never put exception messages or arbitrary reader-supplied strings in S3 or
    console output. This is a diagnostic label, not a release acceptance.
    """
    if type(exc) is not OrderingTrialError:
        return "decode_or_metrics_invalid"
    known = {
        "recorded-source-not-verified": "source_unverified",
        "missing-segment-source-header": "source_unverified",
        "invalid-event-clock": "invalid_clock",
        "non-finite-event-clock": "invalid_clock",
        "late-event-beyond-window": "lateness_exceeds_window",
        # Keep the old combined token for interpretation of older fixtures;
        # new runs distinguish two disjoint rejection origins, not vehicle faults.
        "cross-segment-or-watermark-regression": "event_clock_regression",
        "cross-segment-clock-regression": "segment_boundary_regression",
        "emitted-watermark-regression": "emitted_order_regression",
        "bounded-memory-ceiling": "bounded_memory_exceeded",
        "non-empty-counters": "internal_contract_invalid",
    }
    return known.get(str(exc), "bounded_order_or_source_invalid")


def run_quarantine_bounded_trial(s3, bucket, selected, reader):
    """Keep original quarantine; save a distinct private, non-release trial."""
    with tempfile.TemporaryDirectory(prefix="ram-qlog-trial-") as folder:
        paths = []
        for segment, obj in selected["items"]:
            output = Path(folder) / str(segment) / "qlog.zst"
            output.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(output))
            if not (0 < output.stat().st_size <= MAX_COMPRESSED):
                raise ValueError("Invalid compressed input size")
            paths.append(output)
        counters = {}
        try:
            metrics = summarize_log_messages(
                source_pinned_bounded_messages(paths, reader, counters))
            if counters["verifiedSegments"] != len(paths):
                raise OrderingTrialError("source-verification-incomplete")
            result = {
                "status": "experimental_bounded_observation",
                "reportVersion": 1, "schemaCommit": SCHEMA_COMMIT,
                "routeHash": selected["hash"],
                "inputFingerprint": selected["fingerprint"],
                "segmentCount": len(paths),
                "clockOrder": counters,
                "metrics": metrics,
                "releaseAuthorized": False,
                "caution": "Previously quarantined data; candidate observation only, not a validated drive review or controller replay",
            }
            outcome = {"status": "bounded_trial_saved",
                       "segments": len(paths),
                       "arrivalInversions": counters["arrivalInversionsOverHalfSecond"],
                       "releaseAuthorized": False}
        except OrderingTrialError as exc:
            reason = safe_trial_rejection_reason(exc)
            result = {"status": "trial_rejected", "reportVersion": 1,
                      "reason": reason,
                      "routeHash": selected["hash"],
                      "inputFingerprint": selected["fingerprint"],
                      "segmentCount": len(paths)}
            outcome = {"status": "bounded_trial_rejected",
                       "reason": reason,
                       "segments": len(paths)}
        except Exception:
            result = {"status": "trial_rejected", "reportVersion": 1,
                      "reason": "decode_or_metrics_invalid",
                      "routeHash": selected["hash"],
                      "inputFingerprint": selected["fingerprint"],
                      "segmentCount": len(paths)}
            outcome = {"status": "bounded_trial_rejected",
                       "reason": "decode_or_metrics_invalid",
                       "segments": len(paths)}
    put_private_json(s3, bucket, bounded_trial_key(selected), result)
    return outcome


def run_quarantine_regression_origin_audit(s3, bucket, selected, reader):
    """Re-classify only an OLD, combined clock rejection in separate private data.

    No metrics, timestamps, events, per-segment details or original trial result
    are written. This cannot turn a quarantined recording into an accepted one.
    """
    with tempfile.TemporaryDirectory(prefix="ram-qlog-origin-") as folder:
        paths = []
        for segment, obj in selected["items"]:
            output = Path(folder) / str(segment) / "qlog.zst"
            output.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(output))
            if not (0 < output.stat().st_size <= MAX_COMPRESSED):
                raise ValueError("Invalid compressed input size")
            paths.append(output)
        try:
            # Exhaust the same source-pinned bounded-order stream, but NEVER
            # pass observations into the driving-metrics aggregator.
            for _ in source_pinned_bounded_messages(paths, reader, {}):
                pass
            origin = "previous_rejection_not_reproduced"
        except OrderingTrialError as exc:
            origin = safe_trial_rejection_reason(exc)
        except Exception:
            origin = "decode_or_metrics_invalid"
    put_private_json(s3, bucket, regression_origin_key(selected), {
        "status": "diagnostic_only", "reportVersion": 1,
        "reason": origin, "routeHash": selected["hash"],
        "inputFingerprint": selected["fingerprint"],
        "segmentCount": len(paths), "schemaCommit": SCHEMA_COMMIT,
        "releaseAuthorized": False,
        "caution": "Previously quarantined: do not promote to accepted route or infer a vehicle fault",
    })
    return {"status": "regression_origin_saved", "reason": origin,
            "segments": len(paths), "releaseAuthorized": False}


def run(s3, bucket, now, reader):
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="logs/")
    objects = (obj for page in pages for obj in page.get("Contents", ()))
    candidates = group_routes(objects, now)
    selected = None
    quarantined_for_diagnostic = None
    candidate_for_trial = None
    candidate_for_regression_origin = None
    previous_anonymous_counts = None
    for candidate in candidates:
        existing = read_json_or_none(s3, bucket, route_key(candidate))
        if existing is None and selected is None:
            selected = candidate
        elif isinstance(existing, dict) and existing.get("status") == "quarantined":
            previous = read_json_or_none(s3, bucket, clock_audit_key(candidate))
            if previous is None and quarantined_for_diagnostic is None:
                quarantined_for_diagnostic = candidate
            elif previous_anonymous_counts is None:
                previous_anonymous_counts = anonymous_clock_counts(previous)
            old_trial = read_json_or_none(s3, bucket, bounded_trial_key(candidate))
            if (candidate_for_trial is None
                    and isinstance(previous, dict)
                    and previous.get("status") == "clock_order_audited"
                    and type(previous.get("inversionsOverHalfSecond")) is int
                    and previous["inversionsOverHalfSecond"] > 0
                    and old_trial is None):
                candidate_for_trial = candidate
            # Never rewrite the older coarse rejection; add only a separate
            # immutable diagnostic scoped to that exact source/input fingerprint.
            if (candidate_for_regression_origin is None
                    and isinstance(old_trial, dict)
                    and old_trial.get("status") == "trial_rejected"
                    and old_trial.get("reason") == "event_clock_regression"
                    and read_json_or_none(s3, bucket, regression_origin_key(candidate)) is None):
                candidate_for_regression_origin = candidate
    # Prioritize fresh uploads over backlog diagnostics; every new route
    # gets at most one immutable report per input fingerprint. Old quarantines
    # remain untouched and can be audited when no new upload is ready.
    if selected is None:
        if candidate_for_trial is not None:
            return run_quarantine_bounded_trial(s3, bucket, candidate_for_trial, reader)
        if candidate_for_regression_origin is not None:
            return run_quarantine_regression_origin_audit(
                s3, bucket, candidate_for_regression_origin, reader)
        if quarantined_for_diagnostic is not None:
            result = run_quarantine_clock_audit(s3, bucket, quarantined_for_diagnostic, reader)
            if previous_anonymous_counts is not None:
                result["priorAnonymousClockCounts"] = previous_anonymous_counts
            return result
        return {"status": "no_new_eligible_routes", "eligible": len(candidates)}
    with tempfile.TemporaryDirectory(prefix="ram-auto-review-") as folder:
        paths = []
        for segment, o in selected["items"]:
            output = Path(folder) / str(segment) / "qlog.zst"
            output.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, o["Key"], str(output))
            if not (0 < output.stat().st_size <= MAX_COMPRESSED):
                raise ValueError("Invalid compressed input size")
            paths.append(output)
        try:
            ordering = {}
            metrics = summarize_log_messages(
                source_pinned_route_messages(paths, reader, ordering))
            if ordering["verifiedSegments"] != len(paths):
                raise RouteOrderingError("missing-source-header")
        except Exception as exc:
            # Persist a private, source-scoped tombstone so one invalid historical
            # route cannot fail every five-minute cron and starve older routes.
            # Neither the exception message nor private file identifiers escape.
            reason = ("source_unverified" if isinstance(exc, SourcePinError)
                      else "decode_or_order_invalid")
            put_private_json(s3, bucket, route_key(selected), {
                "reportVersion": 1, "status": "quarantined",
                "reason": reason, "schemaCommit": SCHEMA_COMMIT,
                "sourcePinRequiredPerSegment": True,
                "routeHash": selected["hash"],
                "inputFingerprint": selected["fingerprint"],
                "segments": len(paths),
            })
            return {"status": "route_quarantined", "reason": reason,
                    "segments": len(paths)}
    latest_key = PREFIX + "latest.json"
    latest = read_json_or_none(s3, bucket, latest_key)
    # A later upload can change a route's fingerprint; never use that
    # same route's older partial report as an independent comparator.
    current_active = metrics.get("cruiseOwnership", {}).get("latActiveSamples", {}).get("True", 0)
    if type(current_active) is not int or current_active < 0:
        current_active = 0
    # Do not compare a manual/non-engaged run with an assisted-steering run.
    valid_prior = bool(
        latest and isinstance(latest.get("metrics"), dict)
        and latest.get("routeHash") != selected["hash"]
        and latest["metrics"].get("cruiseOwnership", {}).get("latActiveSamples", {}).get("True", 0) > 0)
    prior = latest["metrics"] if valid_prior and current_active > 0 else None
    is_newest = not latest or selected["updated"].isoformat() >= latest.get("routeUpdatedAt", "")
    delta = compare(metrics, prior if is_newest else None)
    report = {
        "reportVersion": 1, "routeHash": selected["hash"],
        "inputFingerprint": selected["fingerprint"],
        "schemaCommit": SCHEMA_COMMIT,
        "analyzedAt": now.isoformat(),
        "routeUpdatedAt": selected["updated"].isoformat(),
        "segments": len(selected["items"]),
        "sourceVerifiedSegments": len(paths),
        "eventTimeOrdering": {
            "version": ordering["orderingVersion"],
            "windowSeconds": ordering["reorderingWindowSeconds"],
            "maxBufferedEvents": ordering["maxBufferedEvents"],
            "intraSegmentInversions": ordering["intraSegmentInversions"],
            "crossSegmentInversions": ordering["crossSegmentInversions"],
        },
        "lateralActiveSamples": current_active,
        "assistedSteeringReviewEligible": bool(current_active > 0),
        "completeness": "contiguous-from-zero-after-30-minutes-quiet; final-route-count-unverified",
        "metrics": metrics, "comparison": delta,
        "recommendation": "Observational review only. No automatic steering or braking code deployment.",
    }
    put_private_json(s3, bucket, route_key(selected), report)
    if is_newest and current_active > 0:
        put_private_json(s3, bucket, latest_key, report)
    # No location, route names, identifiers or telemetry in console output.
    return {"status": "review_saved", "segments": len(selected["items"]),
            "lateralActiveSamples": current_active,
            "assistedSteeringReviewEligible": current_active > 0,
            "laneChanges": metrics["laneChange"]["completeStartingWindows"],
            "steeringEventEpisodes": metrics["steering"]["steerSaturatedEventEpisodes"],
            "comparisonBaseline": delta["baselineAvailable"],
            "becameLatest": is_newest and current_active > 0}

def main():
    import boto3
    from botocore.config import Config
    from private_route_probe import reader_from_cereal
    env = os.environ
    required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise RuntimeError("Missing storage configuration: " + ",".join(missing))
    s3 = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"],
                      aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                      aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
                      region_name=env.get("S3_REGION", "auto"),
                      config=Config(s3={"addressing_style": "virtual"}))
    schema = Path(env.get("PINNED_CEREAL_LOG_SCHEMA", "/opt/sunnypilot/cereal/log.capnp"))
    reader = reader_from_cereal(schema)
    result = run(s3, env["S3_BUCKET"], datetime.now(timezone.utc), reader)
    print("RAM_AUTO_REVIEW " + json.dumps(result, separators=(",", ":")), flush=True)

if __name__ == "__main__":
    main()
