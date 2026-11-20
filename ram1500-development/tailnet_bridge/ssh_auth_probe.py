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
    except subprocess.TimeoutExpired:
        return {"status": "pi_ssh_auth_not_confirmed",
                "sshAuthenticated": False,
                "failureClass": "timeout_or_interactive_check",
                "deviceWrites": False}
    except OSError:
        return {"status": "pi_ssh_auth_not_confirmed",
                "sshAuthenticated": False,
                "failureClass": "local_ssh_client_unavailable",
                "deviceWrites": False}
    return {"status": "pi_ssh_authenticated_readonly" if completed.returncode == 0
            else "pi_ssh_auth_not_confirmed",
            "sshAuthenticated": completed.returncode == 0,
            "failureClass": None if completed.returncode == 0
            else classify_ssh_failure(completed.stderr),
            "deviceWrites": False}



def classify_ssh_failure(message: str, timed_out: bool = False) -> str:
    """Closed categories only; never publish raw stderr or peer addresses."""
    if timed_out:
        return "timeout_or_interactive_check"
    msg = message.lower()
    if "host key verification failed" in msg or "host key" in msg and "not found" in msg:
        return "host_key_unverified"
    if "permission denied" in msg or "no supported authentication methods" in msg:
        return "no_authorized_client_identity"
    if "connection refused" in msg or "connect: connection" in msg:
        return "ssh_server_rejected_connection"
    if "tailscale ssh" in msg and ("not enabled" in msg or "not supported" in msg):
        return "tailscale_ssh_server_not_enabled"
    if "access denied" in msg or "not allowed" in msg or "policy" in msg:
        return "tailnet_ssh_policy_or_identity"
    if "no route" in msg or "network is unreachable" in msg:
        return "tailnet_network_unreachable"
    return "unclassified_authentication_failure"


def ssh_banner_probe(ip, runner=subprocess.run):
    """Read the server SSH greeting through the authenticated Tailscale tunnel.

    No client credentials are offered, and no SSH command is sent. The
    greeting is reduced to a boolean (never print the server version).
    """
    validated_pi_address(ip)
    try:
        result = runner(
            ["tailscale", "nc", ip, "22"], timeout=6,
            input="", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**os.environ, "TERM": "dumb"},
        )
    except (subprocess.TimeoutExpired, OSError):
        return {"sshProtocolBannerObserved": None}
    first = result.stdout.splitlines()[0] if result.stdout else ""
    return {"sshProtocolBannerObserved": first.startswith("SSH-2.0-")}



def ssh_advertised(ip, runner=subprocess.run):
    """Only a boolean; never log peer IDs, host keys or private tailnet state."""
    validated_pi_address(ip)
    try:
        response = runner(
            ["tailscale", "status", "--json"], timeout=8,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        if response.returncode:
            return None
        state = json.loads(response.stdout)
        peers = state.get("Peer", {}) if isinstance(state, dict) else {}
        if not isinstance(peers, dict):
            return None
        for peer in peers.values():
            if not isinstance(peer, dict):
                continue
            addresses = peer.get("TailscaleIPs", [])
            if isinstance(addresses, list) and ip in addresses:
                keys = peer.get("SSHHostKeys", peer.get("SSH_HostKeys", None))
                if isinstance(keys, list):
                    return bool(keys)
                return None
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
        return None
    return None


def main():
    try:
        ip = os.getenv("TAILNET_PI_IP", "")
        advertised = ssh_advertised(ip)
        result = auth_probe(ip)
        result["peerAdvertisesTailscaleSSH"] = advertised
        result.update(ssh_banner_probe(ip))
    except (ProbeError, ValueError):
        result = {"status": "pi_ssh_auth_skipped_bad_target",
                  "sshAuthenticated": False, "deviceWrites": False}
    print("RAM_PI_SSH_AUTH " + json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
