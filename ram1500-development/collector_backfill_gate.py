"""Offline audit of the read-only Railway collector's historical-route discovery gap.

No Comma credentials, route tokens, object keys, network access, vehicle or CAN code.
This tool does not initiate uploads; it distinguishes eligibility from verified data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Audited from deployed `comma-log-sync` Bun Function on 2026-11-18.
LOOKBACK_HOURS = 72
RECENT_ROUTE_LIMIT = 4
MAX_FILES_PER_RUN = 12
REQUESTS_PER_ROUTE = 4


def _utc_datetime(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be an aware datetime")
    return value.astimezone(timezone.utc)


def audit_historical_segment(
    *,
    now: datetime,
    route_end: datetime,
    recent_route_rank: Optional[int] = None,
    upload_requested: bool = False,
    cloud_rlog_url: bool = False,
    private_rlog_object: bool = False,
    decompress_verified: bool = False,
    pinned_schema_verified: bool = False,
    segment_identity_verified: bool = False,
) -> dict[str, object]:
    """Return non-identifying, conservative eligibility and evidence stages.

    ``recent_route_rank`` is zero-based, counted after sorting by route recentness.
    ``None`` means not independently measured; never infer a rank from route age.
    Any positive integrity flag alone is insufficient for high-resolution analysis.
    """
    clock = _utc_datetime(now, "now")
    end = _utc_datetime(route_end, "route_end")
    if end > clock:
        raise ValueError("route_end cannot be in the future")
    if recent_route_rank is not None and (
        type(recent_route_rank) is not int or recent_route_rank < 0
    ):
        raise ValueError("recent_route_rank must be a zero-based integer or None")
    signals = (
        upload_requested, cloud_rlog_url, private_rlog_object,
        decompress_verified, pinned_schema_verified, segment_identity_verified,
    )
    if any(type(signal) is not bool for signal in signals):
        raise ValueError("all evidence flags must be explicit booleans")

    age_seconds = (clock - end).total_seconds()
    # The collector asks for segments inside this time window, then processes
    # only the four most recent routes. No inference about actual Comma storage.
    if age_seconds > LOOKBACK_HOURS * 3600:
        discovery = "outside_collector_lookback"
    elif recent_route_rank is None:
        discovery = "inside_lookback_rank_unverified"
    elif recent_route_rank >= RECENT_ROUTE_LIMIT:
        discovery = "inside_lookback_but_outside_top_routes"
    else:
        discovery = "eligible_for_collector_route_selection_only"

    if (decompress_verified or pinned_schema_verified or segment_identity_verified) and not private_rlog_object:
        evidence = "inconsistent_provenance_require_review"
    elif private_rlog_object and decompress_verified and pinned_schema_verified and segment_identity_verified:
        evidence = "high_resolution_segment_evidence_verified"
    elif private_rlog_object:
        evidence = "private_object_present_integrity_unverified"
    elif cloud_rlog_url:
        evidence = "cloud_link_seen_private_object_unverified"
    elif upload_requested:
        evidence = "upload_request_only_not_file"
    else:
        evidence = "no_high_resolution_object_evidence"

    return {
        "discoveryGate": discovery,
        "rlogEvidenceGate": evidence,
        "collectorLookbackHours": LOOKBACK_HOURS,
        "collectorRecentRoutes": RECENT_ROUTE_LIMIT,
        "collectorFilesPerRun": MAX_FILES_PER_RUN,
        "collectorRequestsPerRoute": REQUESTS_PER_ROUTE,
        "mayAnalyzeHighResolution": evidence == "high_resolution_segment_evidence_verified",
        "scope": "offline_eligibility_only_not_cloud_absence_or_actuator_safety",
    }
