"""Fixture-only SOCKS5 probe tests; no real tailnet or SSH connections."""
import io
import ipaddress
import socket
import struct
import unittest
from unittest.mock import patch

from probe import validated_pi_address, ssh_port_probe, ProbeError


class FakeSock:
    def __init__(self, values):
        self.values = bytearray(values)
        self.sent = bytearray()
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def settimeout(self, _): pass
    def sendall(self, data): self.sent.extend(data)
    def recv(self, count):
        out = self.values[:count]
        del self.values[:count]
        return bytes(out)


class ProbeTests(unittest.TestCase):
    def test_only_private_tailnet_ips(self):
        self.assertEqual(validated_pi_address("100.93.4.64"), bytes([100,93,4,64]))
        for candidate in ("192.168.1.30", "8.8.8.8", "127.0.0.1",
                          "not-an-address", "100.128.0.1"):
            with self.assertRaises(ProbeError):
                validated_pi_address(candidate)

    def test_socks5_connects_only_to_ssh_port_on_configured_ip(self):
        sock = FakeSock(b"\x05\x00" + b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00\x00\x00")
        with patch("socket.create_connection", return_value=sock):
            result = ssh_port_probe("100.93.4.64")
        self.assertEqual(result["status"], "pi_ssh_port_reachable")
        self.assertFalse(result["sshAuthenticated"])
        self.assertFalse(result["deviceWrites"])
        self.assertIn(b"\x05\x01\x00\x01" + bytes([100,93,4,64]) + struct.pack("!H",22), sock.sent)
        self.assertNotIn(b"\x00\x17", sock.sent)  # not an arbitrary port 23

    def test_socks5_denial_never_claims_ssh(self):
        sock = FakeSock(b"\x05\x00" + b"\x05\x05\x00\x01")
        with patch("socket.create_connection", return_value=sock):
            result = ssh_port_probe("100.93.4.64")
        self.assertEqual(result["status"], "pi_ssh_port_unreachable")
        self.assertFalse(result["piSshTcpReachable"])

    def test_socks5_closed_socket_fails_inconclusive(self):
        sock = FakeSock(b"\x05\x00")
        with patch("socket.create_connection", return_value=sock):
            result = ssh_port_probe("100.93.4.64")
        self.assertEqual(result["status"], "pi_ssh_probe_inconclusive")


if __name__ == "__main__":
    unittest.main()
