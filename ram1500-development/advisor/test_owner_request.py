"""Exercise the private operator entry, exact phone confirmation, and device polling offline."""
import contextlib
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import owner_request
from approval import ApprovalError, verify_approval
from service_app import AdvisorService, dispatch
from service_store import S3Store
from settings_guard import GuardError
from test_service import KEY, URL, ServiceFixture
from wire import sign_request


CHANGE = {"NeuralNetworkLateralControl": False, "AutoLaneChangeTimer": 2}
OWNER_POLL = "/v1/owner/approvals"
ADVISOR_POLL = "/v1/approvals"


class OwnerRequestTests(ServiceFixture):
    def make_request(self, change=None):
        sid, sent = self.service.request_owner_change(CHANGE if change is None else change)
        self.assertIs(sent, True)
        return self.service.store.get_json(f"suggestions/{sid}.json")

    def poll(self, path=OWNER_POLL):
        status, _, body = dispatch(self.service, "GET", path, sign_request(KEY, "GET", path, b""), b"")
        self.assertEqual(status, 200)
        return json.loads(body)["approvals"]

    def tap(self, record):
        return dispatch(self.service, "POST", "/a/" + record["approveToken"], {}, b"")[0]

    def test_service_only_signs_a_confirmed_complete_request_once(self):
        record = self.make_request()
        self.assertEqual((record["reason"], record["mode"]), ("owner_request", "owner"))
        self.assertIsNone(record["approval"])
        self.assertEqual(self.poll(), [])
        [notice] = self.notifier.sent
        self.assertEqual(notice["actions"], [("Approve", URL + "/a/" + record["approveToken"])])
        self.assertIn("AutoLaneChangeTimer=2", notice["message"])
        self.assertIn("NeuralNetworkLateralControl=false", notice["message"])
        self.assertEqual(dispatch(self.service, "GET", "/a/" + record["approveToken"], {}, b"")[0], 404)
        self.assertEqual(self.tap(record), 200)
        self.service = AdvisorService(self.service.store, self.notifier, KEY, URL, clock=self.clock)
        self.assertEqual(self.tap(record), 409)
        [approval] = self.poll()
        verified = verify_approval(KEY, approval, now=self.clock(), used_nonces=set())
        self.assertEqual((verified["suggestion_id"], verified["mode"], verified["change"]),
                         (record["id"], "owner", CHANGE))
        with self.assertRaisesRegex(ApprovalError, "bad_signature"):
            verify_approval(KEY, {**approval, "mode": "nightly"}, now=self.clock(), used_nonces=set())
        with self.assertRaisesRegex(ApprovalError, "already_used"):
            verify_approval(KEY, approval, now=self.clock(), used_nonces={approval["nonce"]})
        self.assertEqual(self.poll(), [approval])  # delivery repeats the same nonce until the result arrives
        for unsupported in ("refused:not_parked", "refused:unrecognized_status", "pending"):
            self.event("apply_result", data={"suggestionId": record["id"], "status": unsupported})
            self.assertEqual(self.poll(), [approval])
        self.event("apply_result", data={"suggestionId": record["id"], "status": "applied"})
        self.assertEqual(self.poll(), [])
        self.event("apply_result", data={"suggestionId": record["id"], "status": "pending"})
        self.assertEqual(self.tap(record), 409)
        self.assertEqual(len([n for n in self.notifier.sent if n["actions"]]), 1)

    def test_token_will_not_approve_a_different_change_or_origin(self):
        for field, altered in (("change", {"AutoLaneChangeTimer": 3}), ("created", 10002.0),
                               ("reason", "advisor"), ("mode", "nightly")):
            with self.subTest(field=field):
                record = self.make_request()
                self.service._save_suggestion({**record, field: altered})
                with patch("service_app.SUGGESTIONS_BUILT_IN", True):
                    self.assertEqual(self.tap(record), 404)
        self.assertEqual(self.poll(), [])

    def test_policy_is_rechecked_when_the_owner_taps(self):
        record = self.make_request()
        with patch("service_app.validate_owner_change", side_effect=GuardError("setting no longer allowed")):
            self.assertEqual(self.tap(record), 400)
        self.assertIsNone(self.service._suggestion(record["id"])["approval"])
        self.assertEqual(self.poll(), [])

    def test_poll_channels_and_authorities_are_separate(self):
        self.assertEqual(dispatch(self.service, "GET", OWNER_POLL, {}, b"")[0], 401)
        self.assertEqual(dispatch(self.service, "GET", OWNER_POLL,
                                  sign_request(KEY, "GET", ADVISOR_POLL, b""), b"")[0], 401)
        self.assertEqual(dispatch(self.service, "POST", OWNER_POLL,
                                  sign_request(KEY, "POST", OWNER_POLL, b""), b"")[0], 405)
        record = self.make_request()
        self.assertEqual(self.tap(record), 200)
        self.assertEqual(self.poll(ADVISOR_POLL), [])
        with patch("service_app.SUGGESTIONS_BUILT_IN", True):
            advisor = self.service._create_suggestion(mode="nightly", change={"NeuralNetworkLateralControl": True},
                                                      reason="advice", drive_id=None)
            self.assertEqual(self.tap(advisor), 200)
            self.assertEqual([r["mode"] for r in self.poll(ADVISOR_POLL)], ["nightly"])
            self.assertEqual([r["mode"] for r in self.poll()], ["owner"])
            self.assertEqual([r["mode"] for r in self.poll(ADVISOR_POLL)], ["nightly"])
            for mode, reason in (("owner", "anything"), ("nightly", "owner_request")):
                with self.assertRaisesRegex(ValueError, "reserved owner"):
                    self.service._create_suggestion(mode=mode, reason=reason, change=CHANGE, drive_id=None)

    def test_event_fields_cannot_create_or_release_an_owner_request(self):
        payload = {"mode": "owner", "reason": "owner_request", "status": "approved", "change": CHANGE}
        for kind in ("owner_request", "suggestion", "summary", "apply_result"):
            self.event(kind, data=payload)
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.notifier.sent, [])
        record = self.make_request()
        self.event("apply_result", data={"suggestionId": record["id"], "status": "approved"})
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.service._suggestion(record["id"])["status"], "pending")

    def test_expired_button_wont_sign_and_old_grant_reconciles_without_a_new_signature(self):
        late = self.make_request()
        self.clock.t += 43201
        self.assertEqual(self.tap(late), 410)
        clicked = self.make_request()
        self.assertEqual(self.tap(clicked), 200)
        [approval] = self.poll()
        self.clock.t += 43201
        self.assertEqual(self.poll(), [approval])
        self.assertEqual(self.service._suggestion(clicked["id"])["status"], "approved")
        with self.assertRaisesRegex(ApprovalError, "expired"):
            verify_approval(KEY, approval, now=self.clock(), used_nonces=set())
        self.event("apply_result", data={"suggestionId": clicked["id"], "status": "refused:expired"})
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.service._suggestion(clicked["id"])["status"], "refused:expired")

    def test_lost_applied_ack_can_arrive_after_approval_expiry(self):
        record = self.make_request()
        self.assertEqual(self.tap(record), 200)
        [original] = self.poll()
        self.clock.t += 86400
        self.service = AdvisorService(self.service.store, self.notifier, KEY, URL, clock=self.clock)
        self.assertEqual(self.poll(), [original])
        self.event("apply_result", data={"suggestionId": record["id"], "status": "applied"})
        self.assertEqual(self.service._suggestion(record["id"])["status"], "applied")
        self.assertEqual(self.poll(), [])

    def test_pending_index_write_retries_without_losing_or_releasing_an_approval(self):
        record = self.make_request()
        with patch.object(self.service.store, "put_if_absent", side_effect=OSError("store unavailable")):
            with self.assertRaises(OSError):
                self.tap(record)
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.tap(record), 200)
        self.assertEqual(len(self.poll()), 1)

    def test_concurrent_requests_survive_stale_legacy_state_and_shared_token_races(self):
        second_service = AdvisorService(self.service.store, self.notifier, KEY, URL, clock=self.clock)
        first, second, third = self.make_request(), self.make_request(), self.make_request()
        stale_state = self.service._state()
        for records in ((first, second), (third, third)):
            barrier = Barrier(2, timeout=5)
            real_put = self.service.store.put_if_absent
            def overlapping_put(key, value):
                barrier.wait()
                return real_put(key, value)
            with patch.object(self.service.store, "put_if_absent", side_effect=overlapping_put):
                with ThreadPoolExecutor(max_workers=2) as workers:
                    a = workers.submit(self.service.approve, records[0]["approveToken"])
                    b = workers.submit(second_service.approve, records[1]["approveToken"])
                    statuses = sorted((a.result()[0], b.result()[0]))
            self.assertEqual(statuses, [200, 409] if records[0] is records[1] else [200, 200])
        self.service.store.put_json("state.json", stale_state)
        self.event("summary", data={"n": 1})
        with patch("service_app.SUGGESTIONS_BUILT_IN", True):
            self.assertEqual(self.poll(ADVISOR_POLL), [])
        self.assertEqual({g["suggestion_id"] for g in self.poll()}, {r["id"] for r in (first, second, third)})
        self.assertEqual(len(self.service.store.list_keys("owner-grants/")), 3)
        self.event("apply_result", data={"suggestionId": second["id"], "status": "applied"})
        self.assertEqual({g["suggestion_id"] for g in self.poll()}, {first["id"], third["id"]})
        self.assertEqual(len(self.service.store.list_keys("owner-delivery/")), 2)

    def test_interrupted_ack_cleanup_preserves_final_receipt_and_retires_marker(self):
        record = self.make_request()
        self.assertEqual(self.tap(record), 200)
        with patch.object(self.service.store, "delete", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.event("apply_result", data={"suggestionId": record["id"], "status": "applied"})
        self.assertEqual(self.service._suggestion(record["id"])["status"], "applied")
        self.assertEqual(len(self.service.store.list_keys("owner-delivery/")), 1)
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.service.store.list_keys("owner-delivery/"), [])

    def test_later_choice_for_the_same_setting_is_delivered_last_despite_key_order(self):
        records = sorted((self.make_request({"AutoLaneChangeTimer": 1}), self.make_request({"AutoLaneChangeTimer": 4})),
                         key=lambda r: r["id"], reverse=True)
        for record in records:
            self.assertEqual(self.tap(record), 200)
            self.clock.t += 1
        self.assertEqual([g["change"] for g in self.poll()], [r["change"] for r in records])

    def test_s3_queue_lists_all_pages_and_creates_immutable_grant(self):
        from unittest.mock import Mock

        class ClientError(Exception):
            def __init__(self, status):
                self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}

        client = Mock()
        client.exceptions = SimpleNamespace(ClientError=ClientError)
        paginator = client.get_paginator.return_value
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "advisor/owner-delivery/a.json"}]}, {},
            {"Contents": [{"Key": "advisor/owner-delivery/b.json"}]},
        ]
        store = S3Store(client, "bucket")
        self.assertEqual(list(store.list_keys("owner-delivery/")), ["owner-delivery/a.json", "owner-delivery/b.json"])
        client.get_paginator.assert_called_once_with("list_objects_v2")
        paginator.paginate.assert_called_once_with(Bucket="bucket", Prefix="advisor/owner-delivery/")
        self.assertTrue(store.put_if_absent("owner-grants/a.json", {"nonce": "original"}))
        call = client.put_object.call_args.kwargs
        self.assertEqual((call["IfNoneMatch"], call["Key"], json.loads(call["Body"])),
                         ("*", "advisor/owner-grants/a.json", {"nonce": "original"}))
        client.put_object.side_effect = ClientError(412)
        self.assertFalse(store.put_if_absent("owner-grants/a.json", {}))
        client.put_object.side_effect = [ClientError(409), ClientError(412)]
        self.assertFalse(store.put_if_absent("owner-grants/a.json", {}))
        client.put_object.side_effect = ClientError(503)
        with self.assertRaises(ClientError):
            store.put_if_absent("owner-grants/a.json", {})
        store.delete("owner-delivery/a.json")
        client.delete_object.assert_called_once_with(Bucket="bucket", Key="advisor/owner-delivery/a.json")

    def test_invalid_values_and_excluded_or_unknown_keys_cannot_issue_any_button(self):
        for change in ({}, {"AutoLaneChangeTimer": 6}, {"AutoLaneChangeTimer": True},
                       {"AutoLaneChangeTimer": "2"}, {"DisableUpdates": False},
                       {"Unknown": False}, {"NeuralNetworkLateralControl": True, "Unknown": False}):
            with self.subTest(keys=list(change)), self.assertRaises(GuardError):
                self.service.request_owner_change(change)
        self.assertEqual(self.service.store.objects, {})
        self.assertEqual(self.notifier.sent, [])

    def test_owner_button_requires_a_secure_action_url(self):
        self.service.public_url = "http://advisor.example"
        with self.assertRaisesRegex(GuardError, "HTTPS"):
            self.service.request_owner_change(CHANGE)
        self.assertEqual(self.service.store.objects, {})
        self.assertEqual(self.notifier.sent, [])

    def test_cli_parses_multiple_fields_before_connecting(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = owner_request.main(["AutoLaneChangeTimer=2", "NeuralNetworkLateralControl=false"],
                                      connect=lambda: self.service)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertNotIn(self.notifier.sent[0]["actions"][0][1], output.getvalue())
        for args in (["missing"], ["AutoLaneChangeTimer=2", "AutoLaneChangeTimer=3"], ["DisableUpdates=true"],
                     ["AutoLaneChangeTimer=6"], ["=3"], ["NeuralNetworkLateralControl="]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                owner_request.main(args, connect=lambda: self.fail("connected before validation"))

    def test_cli_discloses_unconfirmed_delivery_without_exposing_credentials(self):
        self.notifier.send = lambda *a, **kw: False
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = owner_request.main(["AutoLaneChangeTimer=2"], connect=lambda: self.service)
        self.assertEqual(code, 1)
        self.assertIn("unconfirmed", output.getvalue())
        for record in self.service.store.objects.values():
            if "approveToken" in record:
                self.assertNotIn(record["approveToken"], output.getvalue())
        self.assertEqual(self.poll(), [])
