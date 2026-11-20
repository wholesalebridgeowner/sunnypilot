#!/usr/bin/env python3
"""Read-only Railway -> Tailscale -> Sunnyslope Pi SSH TCP availability check.

No SSH login, credentials, shell execution, vehicle control or file writes.
Uses userspace Tailscale's local SOCKS5 proxy and tests only port 22 on a
configured 100.64.0.0/10 peer. Emits one privacy-safe status per probe.
"""
from __future__ import annotations
import ipaddress
import json
import os
import socket
import struct
import time

TAILNET_RANGE = ipaddress.ip_network("100.64.0.0/10")


class ProbeError(Exception):
    pass


def validated_pi_address(value: str) -> bytes:
    try:
        address = ipaddress.IPv4Address(value)
        if address not in TAILNET_RANGE:
            raise ProbeError("not_a_tailscale_ipv4")
        return address.packed
    except ipaddress.AddressValueError:
        raise ProbeError("invalid_tailscale_ipv4")


def exactly(sock, count):
    result = bytearray()
    while len(result) < count:
        piece = sock.recv(count - len(result))
        if not piece:
            raise ProbeError("proxy_closed_connection")
        result.extend(piece)
    return bytes(result)


def ssh_port_probe(pi_address: str, *, proxy_host="127.0.0.1", proxy_port=1055,
                   timeout=8.0):
    target = validated_pi_address(pi_address)
    # RFC 1928 SOCKS5: no-auth request, single fixed target address and port 22.
    try:
        with socket.create_connection((proxy_host, proxy_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            if exactly(sock, 2) != b"\x05\x00":
                raise ProbeError("proxy_auth_or_version_rejected")
            sock.sendall(b"\x05\x01\x00\x01" + target + struct.pack("!H", 22))
            header = exactly(sock, 4)
            if header[0] != 5:
                raise ProbeError("proxy_protocol_mismatch")
            if header[1] != 0:
                return {"status": "pi_ssh_port_unreachable",
                        "tailnetProxyResponsive": True,
                        "piSshTcpReachable": False, "deviceWrites": False}
            if header[3] == 1:
                exactly(sock, 4 + 2)
            elif header[3] == 4:
                exactly(sock, 16 + 2)
            elif header[3] == 3:
                length = exactly(sock, 1)[0]
                exactly(sock, length + 2)
            else:
                raise ProbeError("proxy_bad_address_type")
            # Listening on SSH/22 is a network result only, not authorization.
            return {"status": "pi_ssh_port_reachable",
                    "tailnetProxyResponsive": True,
                    "piSshTcpReachable": True, "sshAuthenticated": False,
                    "deviceWrites": False}
    except (socket.timeout, OSError, ProbeError):
        return {"status": "pi_ssh_probe_inconclusive",
                "piSshTcpReachable": False, "deviceWrites": False}


def run_once():
    value = os.getenv("TAILNET_PI_IP", "")
    if not value:
        return {"status": "missing_pi_tailnet_ip", "deviceWrites": False}
    try:
        return ssh_port_probe(value)
    except ProbeError:
        return {"status": "invalid_pi_tailnet_ip", "deviceWrites": False}


def main():
    # A persistent, read-only monitor. No inbound service, no SSH key, no Comma
    # connection and no command execution of any kind.
    previous = None
    while True:
        result = run_once()
        status = result["status"]
        if status != previous:
            print("RAM_PI_TAILNET " + json.dumps(result, separators=(",", ":")), flush=True)
            previous = status
        time.sleep(300)


if __name__ == "__main__":
    main()
