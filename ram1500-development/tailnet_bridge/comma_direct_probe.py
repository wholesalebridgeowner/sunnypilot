#!/usr/bin/env python3
"""Read-only Railway -> Tailscale subnet router -> known Comma SSH port probe.

Validates only the known Comma's TCP/22 and SSH protocol banner. No
credentials, Comma files, CAN, or controller commands are transmitted.
"""
import ipaddress
import json
import os
import socket
import struct

# The Comma's home-LAN address is private: it comes only from the Railway
# COMMA_HOME_IP variable and is never committed to public git.
COMMA_SSH_PORT = 22


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        piece = sock.recv(count-len(data))
        if not piece:
            raise EOFError
        data.extend(piece)
    return bytes(data)


def comma_address(raw=None):
    raw = os.environ.get("COMMA_HOME_IP", "") if raw is None else raw
    try:
        address = ipaddress.IPv4Address(raw.strip())
    except (ipaddress.AddressValueError, AttributeError):
        return None
    return address.packed if address.is_private else None


def check_comma(proxy_host="127.0.0.1", proxy_port=1055, connect=socket.create_connection, comma_ip=None):
    ip = comma_address(comma_ip)
    if ip is None:
        return {"status":"comma_home_ip_missing","commaSshTcpReachable":False,
                "commaSshBannerObserved":None,"commaAuthenticated":False,"deviceWrites":False}
    result = {"status":"comma_direct_route_not_confirmed",
              "commaSshTcpReachable":False, "commaSshBannerObserved":None,
              "commaAuthenticated":False,"deviceWrites":False}
    try:
        with connect((proxy_host,proxy_port),timeout=8) as sock:
            sock.settimeout(8)
            sock.sendall(b"\x05\x01\x00")
            if exact(sock,2) != b"\x05\x00":
                return result
            sock.sendall(b"\x05\x01\x00\x01"+ip+struct.pack("!H",COMMA_SSH_PORT))
            hdr=exact(sock,4)
            if hdr[:2] != b"\x05\x00":
                return result
            if hdr[3] == 1:
                exact(sock,6)
            elif hdr[3] == 4:
                exact(sock,18)
            elif hdr[3] == 3:
                exact(sock,exact(sock,1)[0]+2)
            else:
                return result
            result["status"]="comma_ssh_tcp_reachable"
            result["commaSshTcpReachable"]=True
            try:
                greeting=sock.recv(256)
                result["commaSshBannerObserved"]=greeting.startswith(b"SSH-2.0-") if greeting else None
            except socket.timeout:
                result["commaSshBannerObserved"]=None
    except (OSError, EOFError, ValueError):
        pass
    return result


def main():
    print("RAM_COMMA_DIRECT "+json.dumps(check_comma(),separators=(",",":")),flush=True)


if __name__ == "__main__":
    main()
