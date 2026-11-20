#!/usr/bin/env python3
"""One-shot read-only Tailscale SSH authorization probe.

A successful SOCKS5 TCP port check does not prove SSH authentication.
Attempt exactly one command ("true") as the known Pi OS user through the
Tailscale SSH wrapper, which validates the advertised host key. No password,
key material, vehicle operation, or file write is attempted.
"""
from __future__ import annotations
import json
import os
import subprocess
from probe import validated_pi_address, ProbeError

PI_USERNAME = "markswerner"
MAX_SECONDS = 12


def auth_probe(ip, runner=subprocess.run):
    validated_pi_address(ip)
    command = ["tailscale", "ssh", f"{PI_USERNAME}@{ip}", "true"]
    try:
        completed = runner(
            command, timeout=MAX_SECONDS, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "SSH_ASKPASS_REQUIRE": "never", "TERM": "dumb"},
        )
    except (subprocess.TimeoutExpired, OSError):
        return {"status": "pi_ssh_auth_not_confirmed",
                "sshAuthenticated": False, "deviceWrites": False}
    return {"status": "pi_ssh_authenticated_readonly" if completed.returncode == 0
            else "pi_ssh_auth_not_confirmed",
            "sshAuthenticated": completed.returncode == 0,
            "deviceWrites": False}


def main():
    try:
        result = auth_probe(os.getenv("TAILNET_PI_IP", ""))
    except (ProbeError, ValueError):
        result = {"status": "pi_ssh_auth_skipped_bad_target",
                  "sshAuthenticated": False, "deviceWrites": False}
    print("RAM_PI_SSH_AUTH " + json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
