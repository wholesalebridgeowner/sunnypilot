"""Private-subnet SOCKS5 test, with zero SSH credential or device writes."""
import io
import struct
import unittest
from unittest.mock import patch
from comma_subnet_probe import only_home_comma,inspect_port

class FakeSock:
    def __init__(self,data):
        self.data=io.BytesIO(data)
        self.sent=b""
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def settimeout(self,t):pass
    def sendall(self,b):self.sent+=b
    def recv(self,n):return self.data.read(n)

class Tests(unittest.TestCase):
    def test_exact_private_address_not_public(self):
        self.assertEqual(only_home_comma("10.0.0.174"),bytes((10,0,0,174)))
        for value in ("192.168.43.1","100.93.4.64","1.1.1.1","invalid"):
            with self.assertRaises(ValueError):only_home_comma(value)
    def test_successful_proxy_ssh_banner_stays_anonymous(self):
        data=b"\x05\x00"+b"\x05\x00\x00\x01"+b"\x00"*6+b"SSH-2.0-OpenSSH"
        sock=FakeSock(data)
        with patch("socket.create_connection",return_value=sock):
            result=inspect_port("10.0.0.174")
        self.assertTrue(result["commaTcp22Reachable"])
        self.assertTrue(result["sshProtocolBanner"])
        self.assertFalse(result["commaAuthenticated"])
        self.assertFalse(result["deviceWrites"])
        self.assertIn(b"\x05\x01\x00\x01"+bytes((10,0,0,174))+struct.pack("!H",22),sock.sent)
    def test_unreachable_proxy(self):
        sock=FakeSock(b"\x05\x00"+b"\x05\x05\x00\x01")
        with patch("socket.create_connection",return_value=sock):
            result=inspect_port("10.0.0.174")
        self.assertFalse(result["commaTcp22Reachable"])
        self.assertEqual(result["status"],"comma_ssh_not_reachable_via_tailnet")

if __name__=="__main__":
    unittest.main()
