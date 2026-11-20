#!/usr/bin/env python3
"""Source-verified TCP/SSH greeting probe over an existing Tailscale subnet route.

The Comma address lives only in Railway COMMA_HOME_IP, not public GitHub.
This tool does NOT attempt SSH authentication or write to either device.
"""
from __future__ import annotations
import ipaddress
import json
import os
import socket
import struct

_ALLOWED = ipaddress.ip_network("10.0.0.0/24")


def only_home_comma(raw):
    try:
        address=ipaddress.IPv4Address(raw)
    except ipaddress.AddressValueError:
        raise ValueError("invalid_comma_ipv4")
    if address not in _ALLOWED:
        raise ValueError("comma_not_on_expected_lan")
    return address.packed


def exactly(sock,n):
    data=b""
    while len(data)<n:
        part=sock.recv(n-len(data))
        if not part:
            raise OSError("upstream_closed")
        data+=part
    return data


def inspect_port(comma_ip, proxy_addr=("127.0.0.1",1055),timeout=6):
    target=only_home_comma(comma_ip)
    try:
        with socket.create_connection(proxy_addr,timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            if exactly(sock,2)!=b"\x05\x00":
                return {"status":"tailscale_socks_unavailable","deviceWrites":False}
            sock.sendall(b"\x05\x01\x00\x01"+target+struct.pack("!H",22))
            resp=exactly(sock,4)
            if resp[:2]!=b"\x05\x00":
                return {"status":"comma_ssh_not_reachable_via_tailnet",
                        "commaTcp22Reachable":False,"deviceWrites":False}
            if resp[3]==1:
                exactly(sock,6)
            elif resp[3]==4:
                exactly(sock,18)
            elif resp[3]==3:
                exactly(sock,exactly(sock,1)[0]+2)
            else:
                return {"status":"socks_invalid_reply","deviceWrites":False}
            try:
                greeting=sock.recv(256)
            except socket.timeout:
                greeting=b""
            return {"status":"comma_ssh_tcp_reachable",
                    "commaTcp22Reachable":True,
                    "sshProtocolBanner":greeting.startswith(b"SSH-2.0-"),
                    "commaAuthenticated":False,"deviceWrites":False}
    except (OSError,TimeoutError):
        return {"status":"comma_subnet_route_unverified",
                "commaTcp22Reachable":False,"deviceWrites":False}


def main():
    value=os.getenv("COMMA_HOME_IP")
    if not value:
        out={"status":"comma_home_ip_missing","deviceWrites":False}
    else:
        try:
            out=inspect_port(value)
        except ValueError:
            out={"status":"comma_home_ip_invalid","deviceWrites":False}
    print("RAM_COMMA_SUBNET "+json.dumps(out,separators=(",",":")),flush=True)


if __name__=="__main__":
    main()
