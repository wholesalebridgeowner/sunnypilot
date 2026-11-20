#!/usr/bin/env python3
"""Read-only Railway -> Tailscale subnet router -> known Comma SSH port probe.

Validates only the known Comma's TCP/22 and SSH protocol banner. No
credentials, Comma files, CAN, or controller commands are transmitted.
"""
import json
import socket
import struct

COMMA_LAN_IPV4 = "10.0.0.174"
COMMA_SSH_PORT = 22


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        piece = sock.recv(count-len(data))
        if not piece:
            raise EOFError
        data.extend(piece)
    return bytes(data)


def check_comma(proxy_host="127.0.0.1", proxy_port=1055, connect=socket.create_connection):
    ip = socket.inet_aton(COMMA_LAN_IPV4)
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
