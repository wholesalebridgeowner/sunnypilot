#!/usr/bin/env python3
"""Exchange an operator-supplied Tailscale API access token for a one-off auth key.

Only the returned tskey-auth- token is printed to stdout, which the invoking
shell captures. Neither the input API token, response body, nor errors are
written to the Railway logs. The generated auth key is short-lived and only
used for userspace Tailscale node registration. No device management API
other than creation of this temporary auth key is called.
"""
from __future__ import annotations
import json
import os
from urllib.request import Request, urlopen

API = "https://api.tailscale.com/api/v2/tailnet/-/keys"
OPTIONS = {
    "capabilities": {"devices": {"create": {
        "reusable": False,
        "ephemeral": True,
        "preauthorized": True,
        "tags": [],
    }}},
    "expirySeconds": 3600,
    "description": "Temporary Railway read-only Pi connectivity probe",
}

def mint_from_api_token(api_token: str, opener=urlopen) -> str:
    if not isinstance(api_token, str) or not api_token.startswith("tskey-api-"):
        raise ValueError("missing_or_wrong_api_token_type")
    request = Request(
        API,
        data=json.dumps(OPTIONS, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_token,
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    with opener(request, timeout=15) as response:
        body = response.read(4096)
    obj = json.loads(body)
    key = obj.get("key") if isinstance(obj, dict) else None
    if not isinstance(key, str) or not key.startswith("tskey-auth-") or len(key) > 512:
        raise ValueError("auth_key_not_returned")
    return key

def main():
    try:
        key = mint_from_api_token(os.getenv("TS_API_KEY", ""))
    except Exception:
        # Important: omit untrusted exceptions; HTTP errors might echo the key.
        raise SystemExit(2)
    print(key, flush=True)

if __name__ == "__main__":
    main()
