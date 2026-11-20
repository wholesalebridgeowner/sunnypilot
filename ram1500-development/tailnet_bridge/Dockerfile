# Railway build of a private outbound-only Tailscale SOCKS5 reachability probe.
# No --privileged, no /dev/net/tun and no inbound public HTTP domain.
FROM alpine:3.21
RUN apk add --no-cache python3 tailscale openssh-client ca-certificates
WORKDIR /opt/ram
COPY probe.py /opt/ram/probe.py
COPY mint_authkey.py /opt/ram/mint_authkey.py
COPY ssh_auth_probe.py /opt/ram/ssh_auth_probe.py
COPY one_time_session.py /opt/ram/one_time_session.py
COPY comma_subnet_probe.py /opt/ram/comma_subnet_probe.py
COPY test_comma_subnet_probe.py /opt/ram/test_comma_subnet_probe.py
COPY test_one_time_session.py /opt/ram/test_one_time_session.py
RUN python3 -m py_compile /opt/ram/one_time_session.py /opt/ram/mint_authkey.py /opt/ram/probe.py /opt/ram/comma_subnet_probe.py \
  && cd /opt/ram && python3 -m unittest test_one_time_session.py test_comma_subnet_probe.py -v
COPY set_pi_key_expiry.py /opt/ram/set_pi_key_expiry.py
COPY test_set_pi_key_expiry.py /opt/ram/test_set_pi_key_expiry.py
RUN cd /opt/ram && python3 -m unittest test_set_pi_key_expiry.py -v
COPY start.sh /opt/ram/start.sh
CMD ["/bin/sh","/opt/ram/start.sh"]
