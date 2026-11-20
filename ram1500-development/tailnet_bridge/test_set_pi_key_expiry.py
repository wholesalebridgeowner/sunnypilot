"""Offline, mocked request tests for the one-device expiry change."""
import base64
import io
import json
import unittest

from set_pi_key_expiry import (
    DEVICE_ID, EXPECTED_NAME, GET_URL, POST_URL,
    make_headers, check_device, execute
)


class Reply:
    def __init__(self, value):
        self.buffer = io.BytesIO(json.dumps(value).encode())
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, n):
        return self.buffer.read(n)


def device(disabled=False, name=EXPECTED_NAME, did=DEVICE_ID):
    return {"name":name,"id":did,"keyExpiryDisabled":disabled,
            "expires":"2026-11-23T15:11:00Z" if not disabled else "2026-11-23T15:11:00Z"}


class Tests(unittest.TestCase):
    def test_get_verify_then_one_post_then_get(self):
        calls = []
        responses = [Reply(device(False)), Reply({}), Reply(device(True))]
        def opener(request, timeout=25):
            calls.append(request)
            return responses.pop(0)
        report = execute("tskey-api-SYNTHETIC", opener)
        self.assertEqual(report["status"], "key_expiry_disabled_verified")
        self.assertEqual(report["beforeKeyExpiryDisabled"], False)
        self.assertEqual(report["afterKeyExpiryDisabled"], True)
        self.assertEqual([c.get_method() for c in calls], ["GET","POST","GET"])
        self.assertEqual([c.full_url for c in calls],[GET_URL,POST_URL,GET_URL])
        self.assertEqual(json.loads(calls[1].data),{"keyExpiryDisabled": True})
        auth=base64.b64encode(b"tskey-api-SYNTHETIC:").decode()
        self.assertEqual(calls[0].get_header("Authorization"),"Basic "+auth)
        self.assertFalse(report["otherDeviceChanges"])
        self.assertNotIn("SYNTHETIC",str(report))

    def test_wrong_name_aborts_before_post(self):
        calls=[]
        def opener(req, timeout=25):
            calls.append(req.get_method())
            return Reply(device(False,"wrong.tail.example"))
        with self.assertRaisesRegex(ValueError,"device_name_mismatch"):
            execute("tskey-api-TEST",opener)
        self.assertEqual(calls,["GET"])

    def test_wrong_id_aborts_before_post(self):
        calls=[]
        def opener(req, timeout=25):
            calls.append(req.get_method())
            return Reply(device(False,did="another-id"))
        with self.assertRaisesRegex(ValueError,"device_id_mismatch"):
            execute("tskey-api-TEST",opener)
        self.assertEqual(calls,["GET"])

    def test_missing_bool_refused(self):
        with self.assertRaisesRegex(ValueError,"key_expiry_status_missing"):
            check_device({"name":EXPECTED_NAME,"id":DEVICE_ID,
                          "keyExpiryDisabled":"false"})

    def test_already_disabled_no_post(self):
        calls=[]
        def opener(req, timeout=25):
            calls.append(req.get_method())
            return Reply(device(True))
        report=execute("tskey-api-TEST",opener)
        self.assertEqual(calls,["GET","GET"])
        self.assertEqual(report["status"],"already_disabled_verified")
        self.assertFalse(report["postIssued"])

    def test_post_once_when_verification_still_false(self):
        calls=[]
        sequence=[Reply(device(False)),Reply({}),Reply(device(False))]
        def opener(req,timeout=25):
            calls.append(req.get_method())
            return sequence.pop(0)
        report=execute("tskey-api-TEST",opener)
        self.assertEqual(report["status"],"post_not_reflected")
        self.assertEqual(calls,["GET","POST","GET"])

    def test_invalid_key_never_sent(self):
        with self.assertRaisesRegex(ValueError,"missing_api_credential"):
            make_headers("tskey-auth-WRONG")

if __name__=="__main__":
    unittest.main()
