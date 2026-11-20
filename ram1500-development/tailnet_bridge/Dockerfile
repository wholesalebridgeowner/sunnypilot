# Railway build of a private outbound-only Tailscale SOCKS5 reachability probe.
# No --privileged, no /dev/net/tun and no inbound public HTTP domain.
FROM alpine:3.21
RUN apk add --no-cache python3 tailscale openssh-client ca-certificates
WORKDIR /opt/ram
COPY probe.py /opt/ram/probe.py
COPY mint_authkey.py /opt/ram/mint_authkey.py
COPY ssh_auth_probe.py /opt/ram/ssh_auth_probe.py
COPY one_time_session.py /opt/ram/one_time_session.py
COPY start.sh /opt/ram/start.sh
CMD ["/bin/sh","/opt/ram/start.sh"]
