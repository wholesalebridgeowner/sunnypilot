#!/usr/bin/env python3
"""One-time authenticated Railway -> Tailscale -> Pi -> Comma reachability session.

This session requests normal Tailscale SSH authorization, publishes ONLY a
Tailscale one-time approval URL if prompted, and waits up to 15 minutes. Once
approved it runs one fixed, read-only Python program ON THE PI to test TCP/22
to two already-known Comma addresses. No passwords, host keys, shell access
tokens, VIN, GPS, raw telemetry or arbitrary remote commands are printed.

A reachable SSH port does NOT establish login to the Comma; no Comma SSH
authentication or installation is attempted by this stage.
"""
from __future__ import annotations
import ipaddress
import json
import os
import re
import selectors
import shlex
import subprocess
import time

TARGET_NETWORK = ipaddress.ip_network("100.64.0.0/10")
PI_USER = "markswerner"
MAX_SESSION_SECONDS = 900
AUTH_LINK = re.compile(r"https://login\.tailscale\.com/a/[A-Za-z0-9_-]{5,180}")

# Explicit known, historically observed Comma SSH locators; NOT assumed
# reachable from Sunnyslope or valid on the current home Wi-Fi.
PI_DIAGNOSTIC = """
import json,socket
known=(('known_hotspot','192.168.43.1'),('observed_other','10.81.244.220'))
results={}
for label,host in known:
    try:
        conn=socket.create_connection((host,22),timeout=2)
        conn.close()
        results[label]='tcp22_reachable'
    except (OSError,TimeoutError):
        results[label]='no_tcp22_route'
print('RAM_PI_TO_COMMA '+json.dumps({'piShellConfirmed':True,'commaCandidateSsh':results,'commaAuthenticated':False,'deviceWrites':False},separators=(',',':')),flush=True)
"""

def validated_pi_ip(raw: str) -> str:
    try:
        ip = ipaddress.IPv4Address(raw)
    except ipaddress.AddressValueError:
        raise ValueError("invalid_pi_tailnet_address")
    if ip not in TARGET_NETWORK:
        raise ValueError("pi_address_outside_tailnet")
    return str(ip)

def read_only_command(ip: str) -> list[str]:
    ip = validated_pi_ip(ip)
    # tailscale ssh is the built-in SSH client with Tailscale policy + check.
    # The host command is constant, never constructed from user text.
    remote = "python3 -c " + shlex.quote(PI_DIAGNOSTIC)
    return ["tailscale", "ssh", f"{PI_USER}@{ip}", remote]

def authorization_link(text: str) -> str | None:
    # Never print arbitrary SSH stderr. Only recognized login.tailscale.com/a
    # URLs are allowed to flow to the private Railway deployment log.
    found = AUTH_LINK.search(text)
    return found.group(0) if found else None

def results_from_output(text: str) -> dict | None:
    for line in text.splitlines():
        if not line.startswith("RAM_PI_TO_COMMA "):
            continue
        try:
            data = json.loads(line.removeprefix("RAM_PI_TO_COMMA "))
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("piShellConfirmed") is not True:
            continue
        values = data.get("commaCandidateSsh", {})
        allowed = {"tcp22_reachable", "no_tcp22_route"}
        if not isinstance(values, dict) or set(values) != {"known_hotspot", "observed_other"}:
            continue
        if not all(v in allowed for v in values.values()):
            continue
        return {"piShellAuthenticated": True, "commaCandidateSsh": values,
                "commaAuthenticated": False, "deviceWrites": False}
    return None

def run():
    try:
        ip = validated_pi_ip(os.getenv("TAILNET_PI_IP", ""))
    except ValueError:
        print('RAM_SESSION {"status":"invalid_or_missing_pi_ip","deviceWrites":false}',flush=True)
        return 2
    command = read_only_command(ip)
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env={**os.environ, "SSH_ASKPASS_REQUIRE": "never","TERM":"dumb"},
        )
    except OSError:
        print('RAM_SESSION {"status":"tailscale_ssh_client_unavailable","deviceWrites":false}',flush=True)
        return 2

    sel=selectors.DefaultSelector()
    for stream in (proc.stdout,proc.stderr):
        if stream is not None:
            sel.register(stream, selectors.EVENT_READ)
    tail = ""
    all_text = ""
    printed_link = False
    start = time.monotonic()
    completed = False
    try:
        while time.monotonic() - start < MAX_SESSION_SECONDS:
            for key,_ in sel.select(timeout=1.0):
                chunk=os.read(key.fileobj.fileno(),4096)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                data=chunk.decode("utf-8",errors="replace")
                tail=(tail+data)[-4096:]
                all_text=(all_text+data)[-16384:]
                if not printed_link:
                    link=authorization_link(tail)
                    if link:
                        print("RAM_SESSION "+json.dumps({
                            "status":"awaiting_approval",
                            "approvalUrl":link,
                            "maxWaitMinutes":15,
                            "deviceWrites":False,
                        },separators=(",",":")),flush=True)
                        printed_link=True
                result=results_from_output(all_text)
                if result:
                    print("RAM_SESSION "+json.dumps({
                        "status":"pi_readonly_diagnostics_completed",
                        **result,
                    },separators=(",",":")),flush=True)
                    completed=True
                    return 0
            if proc.poll() is not None and not sel.get_map():
                break
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        reason = ("approval_window_expired" if printed_link
                  else "ssh_not_authenticated_or_check_url_unavailable")
        print("RAM_SESSION "+json.dumps({
            "status":reason,"piShellAuthenticated":False,"deviceWrites":False,
        },separators=(",",":")),flush=True)
        return 2
    finally:
        if proc.poll() is None:
            proc.kill()
        sel.close()

if __name__=="__main__":
    raise SystemExit(run())
