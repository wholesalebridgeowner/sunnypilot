"""Requests between the Comma and Railway are signed and fresh."""
import unittest

from wire import WireError, sign_request, verify_request

KEY = b"k" * 32
BODY = b'{"type":"summary"}'


class WireTests(unittest.TestCase):
    def test_round_trip(self):
        headers = sign_request(KEY, "POST", "/v1/events", BODY, now=1000)
        verify_request(KEY, "POST", "/v1/events", BODY, headers, now=1010)

    def test_tampered_body_refused(self):
        headers = sign_request(KEY, "POST", "/v1/events", BODY, now=1000)
        with self.assertRaisesRegex(WireError, "bad_signature"):
            verify_request(KEY, "POST", "/v1/events", b'{"type":"x"}', headers, now=1000)

    def test_wrong_key_refused(self):
        headers = sign_request(b"x" * 32, "POST", "/v1/events", BODY, now=1000)
        with self.assertRaisesRegex(WireError, "bad_signature"):
            verify_request(KEY, "POST", "/v1/events", BODY, headers, now=1000)

    def test_other_path_refused(self):
        headers = sign_request(KEY, "GET", "/v1/approvals", b"", now=1000)
        with self.assertRaisesRegex(WireError, "bad_signature"):
            verify_request(KEY, "GET", "/v1/other", b"", headers, now=1000)

    def test_stale_request_refused(self):
        headers = sign_request(KEY, "POST", "/v1/events", BODY, now=1000)
        with self.assertRaisesRegex(WireError, "stale_request"):
            verify_request(KEY, "POST", "/v1/events", BODY, headers, now=1301)

    def test_missing_headers_refused(self):
        with self.assertRaisesRegex(WireError, "missing_auth"):
            verify_request(KEY, "POST", "/v1/events", BODY, {}, now=1000)


if __name__ == "__main__":
    unittest.main()
