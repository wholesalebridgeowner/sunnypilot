"""Tests for no-write, one-command Tailscale SSH authorization probe."""
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch

import ssh_auth_probe as auth


class TestSshAuthProbe(unittest.TestCase):
    def test_exact_readonly_command_and_no_key_leak(self):
        calls=[]
        def runner(args, **kwargs):
            calls.append((args,kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        result=auth.auth_probe("100.93.4.64", runner)
        self.assertEqual(result["status"], "pi_ssh_authenticated_readonly")
        self.assertTrue(result["sshAuthenticated"])
        self.assertFalse(result["deviceWrites"])
        command, kwargs=calls[0]
        self.assertEqual(command, ["tailscale","ssh","markswerner@100.93.4.64","true"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 12)
        self.assertEqual(kwargs["env"]["SSH_ASKPASS_REQUIRE"], "never")

    def test_failure_does_not_echo_untrusted_error(self):
        secret="sensitive_private_device_identifier"
        def runner(*args,**kwargs):
            return SimpleNamespace(returncode=255, stdout="",stderr=secret)
        result=auth.auth_probe("100.93.4.64",runner)
        self.assertEqual(result["status"], "pi_ssh_auth_not_confirmed")
        self.assertNotIn(secret,str(result))

    def test_rejects_non_tailnet_ip_before_runner(self):
        def runner(*args,**kwargs):
            raise AssertionError("runner must not be used")
        with self.assertRaises(Exception):
            auth.auth_probe("192.168.1.30",runner)

    def test_detects_advertised_ssh_keys_without_showing_them(self):
        import json
        sample={"Peer":{"private-peer":{
            "TailscaleIPs":["100.93.4.64"],
            "SSHHostKeys":["PRIVATE_HOST_KEY_DO_NOT_LOG"]}}}
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=0,stdout=json.dumps(sample),stderr="")
        self.assertIs(auth.ssh_advertised("100.93.4.64",runner),True)
        sample["Peer"]["private-peer"]["SSHHostKeys"]=[]
        self.assertIs(auth.ssh_advertised("100.93.4.64",runner),False)
        self.assertNotIn("PRIVATE_HOST_KEY",str(auth.ssh_advertised("100.93.4.64",runner)))

    def test_timeout_fails_closed(self):
        def runner(*args,**kwargs):
            raise subprocess.TimeoutExpired("ssh",12)
        result=auth.auth_probe("100.93.4.64",runner)
        self.assertFalse(result["sshAuthenticated"])


if __name__=="__main__":
    unittest.main()
