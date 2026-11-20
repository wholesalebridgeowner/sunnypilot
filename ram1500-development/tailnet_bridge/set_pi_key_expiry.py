#!/usr/bin/env python3
"""Disable node-key expiry for exactly one independently verified Tailscale Pi.

Uses an API key passed only through the TS_API_KEY environment variable.
One GET before, at most one authorized POST to the *device key* endpoint, then
one GET to verify. Does not change ACLs, advertised routes, auth keys, or any
other device. Never prints credential values, server bodies or raw errors.
"""
from __future__ import annotations

import base64
import json
import os
from urllib.request import Request, urlopen

DEVICE_ID = "525633771324028"
EXPECTED_NAME = "sunnyslope.tail598870.ts.net"
BASE_URL = "https://api.tailscale.com/api/v2/device/" + DEVICE_ID
GET_URL = BASE_URL + "?fields=all"
POST_URL = BASE_URL + "/key"
MAX_RESPONSE_BYTES = 32_768


def make_headers(key: str):
    if not isinstance(key, str) or not key.startswith("tskey-api-"):
        raise ValueError("missing_api_credential")
    basic = base64.b64encode((key + ":").encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + basic, "Accept": "application/json"}


def read_json_response(response):
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("oversized_device_response")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("invalid_device_response")
    return data


def get_device(headers, opener=urlopen):
    req = Request(GET_URL, headers=headers, method="GET")
    with opener(req, timeout=25) as response:
        return read_json_response(response)


def check_device(data):
    if data.get("name") != EXPECTED_NAME:
        raise ValueError("device_name_mismatch")
    if str(data.get("id")) != DEVICE_ID:
        raise ValueError("device_id_mismatch")
    state = data.get("keyExpiryDisabled")
    if type(state) is not bool:
        raise ValueError("key_expiry_status_missing")
    return state


def post_disable(headers, opener=urlopen):
    req = Request(POST_URL,
                  data=b'{"keyExpiryDisabled":true}',
                  headers={**headers, "Content-Type": "application/json"},
                  method="POST")
    with opener(req, timeout=25) as response:
        response.read(1024)


def safe_result(status, before=None, after=None, expires=None, posted=False):
    # Every field is an explicit allowlisted value; don't copy API response.
    return {"status": status, "deviceId": DEVICE_ID, "deviceNameMatched": True,
            "beforeKeyExpiryDisabled": before,
            "afterKeyExpiryDisabled": after,
            "expires": expires if isinstance(expires, str) and len(expires) <= 64 else None,
            "postIssued": posted,
            "otherDeviceChanges": False}


def execute(key, opener=urlopen):
    headers = make_headers(key)
    first = get_device(headers, opener)
    prior = check_device(first)
    if prior:
        # No POST if already disabled. Independent GET still verifies identity.
        current = get_device(headers, opener)
        latest = check_device(current)
        return safe_result("already_disabled_verified" if latest else "state_changed_externally",
                           before=True, after=latest, expires=current.get("expires"), posted=False)
    # Exactly one POST. Never retry a mutating request after a timeout/error.
    post_disable(headers, opener)
    latest = get_device(headers, opener)
    after = check_device(latest)
    return safe_result("key_expiry_disabled_verified" if after else "post_not_reflected",
                       before=False, after=after, expires=latest.get("expires"), posted=True)


def main():
    token = os.getenv("TS_API_KEY", "")
    try:
        result = execute(token)
    except ValueError as exc:
        # ValueError text consists solely of our own static error labels.
        result = {"status": "no_change_or_unverified", "reason": str(exc),
                  "postStatusUnknown": False}
    except Exception:
        # After a network failure the POST might have reached the API. Never
        # automatically retry. Any unknown state must be manually rechecked.
        result = {"status": "api_failure_manual_verification_required",
                  "postStatusUnknown": True}
    print("RAM_PI_KEY_EXPIRY " + json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result.get("status") in ("key_expiry_disabled_verified",
                                        "already_disabled_verified") else 2


if __name__ == "__main__":
    raise SystemExit(main())
