"""Approvals: signed, single-use, valid for 10 minutes."""
import unittest

from approval import ApprovalError, sign_approval, verify_approval

KEY = b"k" * 32
CHANGE = {"LagdToggle": False, "LagdToggleDelay": 0.1}


def make(**overrides):
    args = dict(suggestion_id="s1", mode="live", change=CHANGE, issued_at=1000.0, nonce="n" * 16)
    args.update(overrides)
    return sign_approval(KEY, **args)


class ApprovalTests(unittest.TestCase):
    def test_valid_approval_returns_fields(self):
        fields = verify_approval(KEY, make(), now=1100.0, used_nonces=set())
        self.assertEqual(fields["change"], CHANGE)
        self.assertEqual(fields["mode"], "live")

    def test_wrong_key_refused(self):
        with self.assertRaisesRegex(ApprovalError, "bad_signature"):
            verify_approval(b"x" * 32, make(), now=1100.0, used_nonces=set())

    def test_tampered_change_refused(self):
        approval = make()
        approval["change"] = {"LagdToggle": False, "LagdToggleDelay": 0.2}
        with self.assertRaisesRegex(ApprovalError, "bad_signature"):
            verify_approval(KEY, approval, now=1100.0, used_nonces=set())

    def test_expired_after_ten_minutes(self):
        with self.assertRaisesRegex(ApprovalError, "expired"):
            verify_approval(KEY, make(), now=1601.0, used_nonces=set())

    def test_future_issue_time_refused(self):
        with self.assertRaisesRegex(ApprovalError, "issued_in_future"):
            verify_approval(KEY, make(), now=900.0, used_nonces=set())

    def test_replay_refused(self):
        with self.assertRaisesRegex(ApprovalError, "already_used"):
            verify_approval(KEY, make(), now=1100.0, used_nonces={"n" * 16})

    def test_short_nonce_refused(self):
        with self.assertRaisesRegex(ApprovalError, "malformed"):
            verify_approval(KEY, make(nonce="abc"), now=1100.0, used_nonces=set())

    def test_nightly_approval_lasts_twelve_hours(self):
        approval = make(mode="nightly", change={"NeuralNetworkLateralControl": True})
        self.assertEqual(verify_approval(KEY, approval, now=1000.0 + 11 * 3600, used_nonces=set())["mode"], "nightly")
        with self.assertRaisesRegex(ApprovalError, "expired"):
            verify_approval(KEY, approval, now=1000.0 + 13 * 3600, used_nonces=set())

    def test_live_approval_still_expires_after_ten_minutes(self):
        with self.assertRaisesRegex(ApprovalError, "expired"):
            verify_approval(KEY, make(mode="live"), now=1601.0, used_nonces=set())

    def test_not_a_dict_refused(self):
        with self.assertRaisesRegex(ApprovalError, "malformed"):
            verify_approval(KEY, "approve", now=1100.0, used_nonces=set())


if __name__ == "__main__":
    unittest.main()
