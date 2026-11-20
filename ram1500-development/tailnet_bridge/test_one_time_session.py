"""Offline regression tests for one-time Tailscale SSH approval and Pi reachability."""
import json
import unittest
from unittest.mock import patch

from one_time_session import authorization_link, results_from_output, read_only_command, validated_pi_ip, PI_DIAGNOSTIC

class TestOneTimeSession(unittest.TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"TAILNET_PI_USER": "pi-user", "COMMA_HOME_IP": "10.0.0.50", "COMMA_OTHER_IP": "10.81.0.20"})
        env.start()
        self.addCleanup(env.stop)

    def test_missing_private_settings_fail_closed(self):
        for name in ("TAILNET_PI_USER", "COMMA_HOME_IP"):
            with self.subTest(name=name), patch.dict("os.environ", {name: ""}):
                with self.assertRaises(ValueError):
                    read_only_command("100.64.0.10")
        with patch.dict("os.environ", {"COMMA_HOME_IP": "8.8.8.8"}):
            with self.assertRaises(ValueError):
                read_only_command("100.64.0.10")

    def test_only_expected_tailscale_approval_url_is_emitted(self):
        sample="Before\r\nVisit https://login.tailscale.com/a/l1c338d882b1e39 now\r\n"
        self.assertEqual(authorization_link(sample),"https://login.tailscale.com/a/l1c338d882b1e39")
        self.assertIsNone(authorization_link("https://evil.example/a/l1c338d882b1e39"))
        self.assertIsNone(authorization_link("https://login.tailscale.com/admin/keys"))

    def test_pi_and_comma_targets_are_limited(self):
        command=read_only_command("100.64.0.10")
        self.assertEqual(command[:3],["tailscale","ssh","pi-user@100.64.0.10"])
        self.assertIn("python3 -c ",command[3])
        self.assertIn("10.0.0.50",command[3])
        self.assertNotIn("10.0.0.50",PI_DIAGNOSTIC)
        self.assertIn("StrictHostKeyChecking=yes",PI_DIAGNOSTIC)
        self.assertIn("BatchMode=yes",PI_DIAGNOSTIC)
        self.assertNotIn("rm -",command[3])
        self.assertNotIn("sudo ",command[3])
        for invalid in ("192.168.1.250","localhost","8.8.8.8","100.128.0.1"):
            with self.assertRaises(ValueError):
                validated_pi_ip(invalid)

    def test_privacy_allowlisted_pi_success(self):
        data = {"piShellConfirmed":True,"commaSshTcpReachable":True,
                "commaSshAuthentication":"pi_key_not_authorized_on_comma",
                "commaAuthenticated":False,"deviceWrites":False}
        raw = "Login info\nRAM_PI_TO_COMMA " + json.dumps(data) + "\n"
        parsed = results_from_output(raw)
        self.assertTrue(parsed["piShellAuthenticated"])
        self.assertFalse(parsed["commaAuthenticated"])
        self.assertEqual(parsed["commaSshAuthentication"], "pi_key_not_authorized_on_comma")
        self.assertNotIn("Login info",str(parsed))

    def test_untrusted_output_not_interpreted(self):
        self.assertIsNone(results_from_output('RAM_PI_TO_COMMA {"piShellConfirmed":true,"commaSshTcpReachable":true,"commaSshAuthentication":"secret"}'))
        self.assertIsNone(results_from_output('RAM_PI_TO_COMMA {"piShellConfirmed":true,"commaSshTcpReachable":true,"commaSshAuthentication":"ssh_authenticated","commaAuthenticated":false}'))
        self.assertIsNone(results_from_output('PI_RESPONSE private_data'))


if __name__=="__main__":
    unittest.main()
