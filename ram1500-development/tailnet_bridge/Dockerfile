# Railway build of a private outbound-only Tailscale SOCKS5 reachability probe.
# No --privileged, no /dev/net/tun and no inbound public HTTP domain.
FROM alpine:3.21
RUN apk add --no-cache python3 tailscale openssh-client ca-certificates
WORKDIR /opt/ram
COPY probe.py /opt/ram/probe.py
COPY mint_authkey.py /opt/ram/mint_authkey.py
COPY ssh_auth_probe.py /opt/ram/ssh_auth_probe.py
COPY one_time_session.py /opt/ram/one_time_session.py
COPY test_one_time_session.py /opt/ram/test_one_time_session.py
RUN python3 -m py_compile /opt/ram/one_time_session.py /opt/ram/mint_authkey.py /opt/ram/probe.py \
  && cd /opt/ram && python3 -m unittest test_one_time_session.py -v
COPY start.sh /opt/ram/start.sh
CMD ["/bin/sh","/opt/ram/start.sh"]
