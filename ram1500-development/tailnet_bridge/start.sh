#!/bin/sh
# Dedicated read-only tailnet reachability worker. No inbound ports.
set -eu
umask 077
if [ -z "${TS_AUTHKEY:-}" ]; then
  printf '%s\n' 'RAM_PI_TAILNET {"status":"awaiting_railway_tailscale_auth","deviceWrites":false}'
  exit 0
fi
if [ -z "${TAILNET_PI_IP:-}" ]; then
  printf '%s\n' 'RAM_PI_TAILNET {"status":"missing_pi_tailnet_ip","deviceWrites":false}'
  exit 0
fi
# No kernel TUN privileges required. The socket is local-only.
tailscaled --tun=userspace-networking --state=mem: --socks5-server=127.0.0.1:1055 >/tmp/tailscaled.log 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if tailscale status >/dev/null 2>&1; then break; fi
  sleep 1
done
# Keep the key out of Railway stdout; OAuth or ephemeral reusable auth keys
# are recommended for workloads that restart without persisted node state.
if ! tailscale up --auth-key="$TS_AUTHKEY" --hostname=ram-railway-pi-probe --accept-dns=false >/tmp/tailscale-login.log 2>&1; then
  printf '%s\n' 'RAM_PI_TAILNET {"status":"tailnet_auth_failed","deviceWrites":false}'
  exit 2
fi
unset TS_AUTHKEY
exec python3 -u /opt/ram/probe.py
