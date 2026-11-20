"""Offline regression tests for one-time Tailscale SSH approval and Pi reachability."""
import json
import unittest

from one_time_session import authorization_link, results_from_output, read_only_command, validated_pi_ip, PI_DIAGNOSTIC

class TestOneTimeSession(unittest.TestCase):
    def test_only_expected_tailscale_approval_url_is_emitted(self):
        sample="Before\r\nVisit https://login.tailscale.com/a/l1c338d882b1e39 now\r\n"
        self.assertEqual(authorization_link(sample),"https://login.tailscale.com/a/l1c338d882b1e39")
        self.assertIsNone(authorization_link("https://evil.example/a/l1c338d882b1e39"))
        self.assertIsNone(authorization_link("https://login.tailscale.com/admin/keys"))

    def test_pi_and_comma_targets_are_limited(self):
        command=read_only_command("100.93.4.64")
        self.assertEqual(command[:3],["tailscale","ssh","markswerner@100.93.4.64"])
        self.assertIn("python3 -c ",command[3])
        self.assertIn("192.168.43.1",PI_DIAGNOSTIC)
        self.assertIn("10.81.244.220",PI_DIAGNOSTIC)
        self.assertNotIn("rm -",command[3])
        self.assertNotIn("sudo ",command[3])
        for invalid in ("192.168.1.30","localhost","8.8.8.8","100.128.0.1"):
            with self.assertRaises(ValueError):
                validated_pi_ip(invalid)

    def test_privacy_allowlisted_pi_success(self):
        data={"piShellConfirmed":True,"commaCandidateSsh":{
            "known_hotspot":"no_tcp22_route","observed_other":"tcp22_reachable"},
            "commaAuthenticated":False,"deviceWrites":False}
        raw="Login info\nRAM_PI_TO_COMMA "+json.dumps(data)+"\n"
        parsed=results_from_output(raw)
        self.assertTrue(parsed["piShellAuthenticated"])
        self.assertFalse(parsed["commaAuthenticated"])
        self.assertEqual(parsed["commaCandidateSsh"]["observed_other"],"tcp22_reachable")
        self.assertNotIn("Login info",str(parsed))

    def test_untrusted_output_not_interpreted(self):
        self.assertIsNone(results_from_output('RAM_PI_TO_COMMA {"piShellConfirmed":true,"commaCandidateSsh":{"anything":"yes"}}'))
        self.assertIsNone(results_from_output('RAM_PI_TO_COMMA {"piShellConfirmed":true,"commaCandidateSsh":{"known_hotspot":"secret","observed_other":"tcp22_reachable"}}'))
        self.assertIsNone(results_from_output('PI_RESPONSE private_data'))
        
if __name__=="__main__":
    unittest.main()
