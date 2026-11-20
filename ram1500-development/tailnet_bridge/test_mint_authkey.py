"""Offline tests for ephemeral Tailscale auth-key creation. Never use real tokens."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from mint_authkey import mint_from_api_token, OPTIONS, API


class Response:
    def __init__(self, body):
        self.body = io.BytesIO(body)
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, n):
        return self.body.read(n)


class Tests(unittest.TestCase):
    def test_api_token_mints_ephemeral_one_off_key(self):
        token = "tskey-api-TEST-NOT-REAL"
        tempkey = "tskey-auth-EXAMPLE-NOT-REAL"
        seen = []
        def open_stub(req, timeout):
            seen.append((req, timeout))
            return Response(json.dumps({"key": tempkey}).encode())
        self.assertEqual(mint_from_api_token(token, open_stub), tempkey)
        request, timeout = seen[0]
        self.assertEqual(request.full_url, API)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + token)
        data = json.loads(request.data)
        device = data["capabilities"]["devices"]["create"]
        self.assertFalse(device["reusable"])
        self.assertTrue(device["ephemeral"])
        self.assertEqual(data["expirySeconds"], 3600)

    def test_rejects_auth_key_used_as_api_key(self):
        for token in ("", "tskey-auth-EXAMPLE", "x", "not-a-key"):
            with self.assertRaisesRegex(ValueError, "wrong_api"):
                mint_from_api_token(token)

    def test_rejects_missing_or_malformed_auth_response(self):
        for body in (b"{}", b"[]", b'{"key":"wrong-prefix"}', b'{"key":123}'):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    mint_from_api_token("tskey-api-TEST", lambda *_ , **__: Response(body))

    def test_api_error_is_not_logged_from_main(self):
        import mint_authkey as mod
        import contextlib
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"TS_API_KEY":"tskey-api-SENSITIVE-TEST"}):
            def bad_request(*_):
                raise ValueError("tskey-api-SENSITIVE-TEST")
            with patch.object(mod, "mint_from_api_token", bad_request):
                with self.assertRaises(SystemExit) as ctx:
                    mod.main()
        self.assertEqual(ctx.exception.code, 2)


if __name__=="__main__":
    unittest.main()
