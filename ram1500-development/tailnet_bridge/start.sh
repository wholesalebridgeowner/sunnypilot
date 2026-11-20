#!/bin/sh
# Dedicated read-only Tailscale client. TS_AUTHKEY preferred; TS_API_KEY can
# mint a one-off ephemeral auth key. Neither credential enters stdout/logs.
set -eu
umask 077
if [ -z "${TS_AUTHKEY:-}" ] && [ -z "${TS_API_KEY:-}" ]; then
  printf '%s\n' 'RAM_PI_TAILNET {"status":"awaiting_railway_tailscale_auth","deviceWrites":false}'
  exit 0
fi
if [ -z "${TAILNET_PI_IP:-}" ]; then
  printf '%s\n' 'RAM_PI_TAILNET {"status":"missing_pi_tailnet_ip","deviceWrites":false}'
  exit 0
fi

if [ -n "${TS_AUTHKEY:-}" ]; then
  AUTHKEY="$TS_AUTHKEY"
else
  # Capture temporary tskey-auth in shell memory only; no print to Railway.
  if AUTHKEY="$(python3 /opt/ram/mint_authkey.py)"; then
    :
  else
    printf '%s\n' 'RAM_PI_TAILNET {"status":"api_token_exchange_failed","deviceWrites":false}'
    exit 2
  fi
fi
unset TS_API_KEY TS_AUTHKEY

# No kernel TUN privileges required. The SOCKS5 port is loopback-only.
tailscaled --tun=userspace-networking --state=mem: --socks5-server=127.0.0.1:1055 >/tmp/tailscaled.log 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if tailscale status >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! tailscale up --auth-key="$AUTHKEY" --hostname=ram-railway-pi-probe --accept-dns=false --accept-routes=true >/tmp/tailscale-login.log 2>&1; then
  printf '%s\n' 'RAM_PI_TAILNET {"status":"tailnet_auth_failed","deviceWrites":false}'
  exit 2
fi
unset AUTHKEY
printf '%s\n' 'RAM_PI_TAILNET {"status":"tailnet_node_registered","deviceWrites":false}'
python3 -u /opt/ram/comma_subnet_probe.py
exec python3 -u /opt/ram/one_time_session.py
