# Railway build of a private outbound-only Tailscale SOCKS5 reachability probe.
# No --privileged, no /dev/net/tun and no inbound public HTTP domain.
FROM alpine:3.21
RUN apk add --no-cache python3 tailscale ca-certificates
WORKDIR /opt/ram
COPY probe.py /opt/ram/probe.py
COPY start.sh /opt/ram/start.sh
CMD ["/bin/sh","/opt/ram/start.sh"]
