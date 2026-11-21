"""Signed requests between the Comma agent and the Railway service.

Signature = HMAC-SHA256(key, "METHOD\\nPATH\\nTS\\n" + body). Requests older or
newer than 5 minutes are refused, so a captured request cannot be replayed later.
"""
from __future__ import annotations

import hashlib
import hmac
import time

MAX_SKEW_S = 300
TS_HEADER = "X-Advisor-Ts"
SIG_HEADER = "X-Advisor-Sig"


class WireError(ValueError):
    """A request is unsigned, forged or stale."""


def _mac(key: bytes, method: str, path: str, ts: str, body: bytes) -> str:
    message = f"{method.upper()}\n{path}\n{ts}\n".encode("utf-8") + body
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def sign_request(key: bytes, method: str, path: str, body: bytes, now: float | None = None) -> dict:
    ts = str(int(time.time() if now is None else now))
    return {TS_HEADER: ts, SIG_HEADER: _mac(key, method, path, ts, body)}


def verify_request(key: bytes, method: str, path: str, body: bytes, headers, now: float | None = None) -> None:
    ts, signature = headers.get(TS_HEADER), headers.get(SIG_HEADER)
    if not ts or not signature:
        raise WireError("missing_auth")
    try:
        ts_value = int(ts)
    except ValueError:
        raise WireError("bad_timestamp")
    current = time.time() if now is None else now
    if abs(current - ts_value) > MAX_SKEW_S:
        raise WireError("stale_request")
    if not hmac.compare_digest(signature, _mac(key, method, path, ts, body)):
        raise WireError("bad_signature")
