"""One-time approvals for Drive Advisor changes.

Railway signs an approval when the owner taps Approve; the Comma verifies it
with the shared key before the applier writes anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math

APPROVAL_TTL_S = {"live": 600, "nightly": 12 * 3600}  # the Comma can be off overnight
MAX_CLOCK_SKEW_S = 60
_FIELDS = ("suggestion_id", "mode", "change", "issued_at", "nonce")


class ApprovalError(ValueError):
    """An approval is malformed, forged, expired or already used."""


def _payload(fields: dict) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sign_approval(key: bytes, *, suggestion_id: str, mode: str, change: dict,
                  issued_at: float, nonce: str) -> dict:
    fields = {"suggestion_id": suggestion_id, "mode": mode, "change": change,
              "issued_at": issued_at, "nonce": nonce}
    return {**fields, "sig": hmac.new(key, _payload(fields), hashlib.sha256).hexdigest()}


def verify_approval(key: bytes, approval: dict, *, now: float, used_nonces: set) -> dict:
    """Return the approved fields, or raise ApprovalError."""
    if not isinstance(approval, dict):
        raise ApprovalError("malformed")
    fields = {name: approval.get(name) for name in _FIELDS}
    issued = fields["issued_at"]
    if not (isinstance(fields["suggestion_id"], str) and isinstance(fields["nonce"], str)
            and len(fields["nonce"]) >= 16 and isinstance(fields["change"], dict)
            and fields["mode"] in ("live", "nightly")
            and isinstance(issued, (int, float)) and not isinstance(issued, bool)
            and math.isfinite(issued)):
        raise ApprovalError("malformed")
    try:
        expected = hmac.new(key, _payload(fields), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        raise ApprovalError("malformed")
    signature = approval.get("sig")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ApprovalError("bad_signature")
    age = now - issued
    if age < -MAX_CLOCK_SKEW_S:
        raise ApprovalError("issued_in_future")
    if age > APPROVAL_TTL_S[fields["mode"]]:
        raise ApprovalError("expired")
    if fields["nonce"] in used_nonces:
        raise ApprovalError("already_used")
    return fields
