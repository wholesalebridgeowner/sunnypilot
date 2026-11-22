"""Owner-only device validation, parked-state evidence, recovery and transport."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as ns
from unittest.mock import patch

from approval import APPROVAL_TTL_S, sign_approval
from journal import Journal
from owner_apply import OwnerApplier
from owner_receiver import OwnerReceiver
from schema_v2 import problems
from service_app import dispatch
from test_agent import FakeParams, FakeSock
from test_end_to_end import KEY, Loop


class Params(FakeParams):
    stored = FakeParams.get

    def remove(self, key):
        self.values.pop(key, None)


class OwnerDeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.params = Params({"RainbowMode": False})
        self.writer = self.new_writer()
        self.sequence = 0
        self.change = {"RainbowMode": True, "AutoLaneChangeTimer": 2}
        self.approval = self.sign(self.change)

    def new_writer(self):
        return OwnerApplier(self.params, self.root / "owner", KEY, Journal(self.root / "trial"))

    def sign(self, change, mode="owner", nonce=None):
        self.sequence += 1
        nonce = nonce or f"{self.sequence:032x}"
        return sign_approval(KEY, suggestion_id="0000000000000025", mode=mode, change=change,
                             issued_at=1000.0, nonce=nonce)

    def apply(self, item=None, safe=True, now=1010):
        return self.writer.apply(self.approval if item is None else item,
                                 safe if callable(safe) else lambda: safe, now=now)

    def test_transaction_retains_absent_previous_value_and_receipt(self):
        self.assertEqual(self.apply(), "applied")
        receipt, = (self.root / "owner").glob("*.json")
        self.assertEqual(json.loads(receipt.read_text())["previous"], {"AutoLaneChangeTimer": None, "RainbowMode": False})
        self.params.values["AutoLaneChangeTimer"] = 5
        self.assertEqual(self.apply(), "applied")
        self.assertEqual(len(self.params.writes), 2)
        self.assertEqual(self.params.values["AutoLaneChangeTimer"], 5)
        self.assertEqual(self.apply(now=1000 + APPROVAL_TTL_S["owner"] + 1), "applied")
        self.assertEqual(len(self.params.writes), 2)

    def test_device_native_setting_lock_refuses_the_whole_group(self):
        self.params.values["RecordFrontLock"] = True
        approval = self.sign({"RainbowMode": True, "RecordFront": True})
        self.assertEqual(self.apply(approval), "refused:native_setting_locked")
        self.params.values["RecordFrontLock"] = False
        self.assertEqual(self.apply(approval), "refused:native_setting_locked")
        self.assertEqual(self.params.writes, [])

    def test_guards_precede_any_writes(self):
        cases = [
            (dict(self.approval, change={"AutoLaneChangeTimer": 5}), "bad_signature"),
            (self.sign(self.change, mode="nightly"), "owner_approval_required"),
            (self.sign({"IsOnroad": False}), "invalid_owner_change"),
            (self.sign({"AutoLaneChangeTimer": 6}), "invalid_owner_change"),
            (self.sign({"RainbowMode": "1"}), "invalid_owner_change"),
            (self.sign({"RainbowMode": True, "NoSuchKey": 0}), "invalid_owner_change"),
        ]
        for approval, reason in cases:
            with self.subTest(change=approval["change"], reason=reason):
                self.assertEqual(self.apply(approval), "refused:" + reason)
        self.assertEqual(self.apply(safe=False), "refused:not_parked")
        self.assertEqual(self.apply(now=1000 + APPROVAL_TTL_S["owner"] + 1), "refused:expired")
        self.assertEqual(self.params.writes, [])

    def test_partial_write_reverts_and_does_not_retry_the_same_nonce(self):
        put = self.params.put

        def fail_on_timer(name, value):
            if name == "AutoLaneChangeTimer":
                raise OSError("injected timer write failure")
            put(name, value)

        with patch.object(self.params, "put", side_effect=fail_on_timer):
            self.assertEqual(self.apply(), "refused:interrupted_owner_change")
        self.assertEqual(self.params.values, {"RainbowMode": False})
        self.assertEqual(self.apply(), "refused:interrupted_owner_change")
        self.assertEqual(len(self.params.writes), 2)

    def test_failed_group_keeps_accelerator_disengagement_enabled_after_redelivery(self):
        self.params.values["DisengageOnAccelerator"] = False
        grant = self.sign({"DisengageOnAccelerator": True, "RainbowMode": True})
        put = self.params.put

        def fail_on_display(name, value):
            if name == "RainbowMode":
                raise OSError("injected display write failure")
            put(name, value)

        with patch.object(self.params, "put", side_effect=fail_on_display):
            self.assertEqual(self.apply(grant), "refused:partial_owner_change")
        receipt_path, = (self.root / "owner").glob("*.json")
        self.assertFalse(self.writer.pending.exists())
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["retained"], ["DisengageOnAccelerator"])
        self.assertEqual(receipt["status"], "refused:partial_owner_change")
        self.assertEqual(receipt["nonce"], grant["nonce"])
        self.assertIs(receipt["previous"]["DisengageOnAccelerator"], False)
        self.assertIs(self.params.values["DisengageOnAccelerator"], True)
        self.assertIs(self.params.values["RainbowMode"], False)

        self.writer = self.new_writer()
        self.assertEqual(self.apply(grant), "refused:partial_owner_change")
        self.assertEqual(self.params.writes, [("DisengageOnAccelerator", True)])
        self.assertIs(self.params.values["DisengageOnAccelerator"], True)
        self.assertIs(self.params.values["RainbowMode"], False)

    def test_loss_of_park_mid_group_defers_recovery_until_safe(self):
        self.assertEqual(self.apply(safe=lambda: not self.params.writes), "refused:not_parked")
        self.assertTrue(self.writer.pending.exists())
        self.assertEqual(self.params.values, {"RainbowMode": True})
        restarted = self.new_writer()
        self.assertEqual(restarted.recover(lambda: False), "refused:not_parked")
        self.assertEqual(restarted.recover(lambda: True), "refused:interrupted_owner_change")
        self.assertEqual(self.params.values, {"RainbowMode": False})

    def test_fresh_explicit_park_and_ignition_evidence(self):
        control_time = 12_000_000_000
        board_time = 82_000_000_000
        board = FakeSock()
        receiver = OwnerReceiver(self.writer, lambda: control_time, lambda: board_time, lambda: board)
        self.params.values.update(IsOnroad=True, IsOffroad=False)
        car = ns(vEgo=0.0, gearShifter="park", canValid=True)
        control = ns(enabled=False, latActive=False, longActive=False)
        agent = ns(
            params=self.params, onroad=True, listening=True, socks={"carState": object()},
            latest_state={"carState": (0, car, control_time, True), "carControl": (0, control, control_time, True)},
            decode=lambda stream, raw: raw,
        )
        self.assertTrue(receiver.safe(agent))
        control.latActive = True
        self.assertFalse(receiver.safe(agent))
        control.latActive = False
        agent.latest_state["carState"] = (0, car, control_time - 1_000_000_001, True)
        self.assertFalse(receiver.safe(agent))
        agent.latest_state["carState"] = (0, car, control_time, False)
        self.assertFalse(receiver.safe(agent))
        self.params.values.update(IsOnroad=False, IsOffroad=True)
        agent.onroad = False
        self.assertFalse(receiver.safe(agent))
        board_cases = [
            ([], False),
            ([ns(pandaType="unknown", ignitionCan=False, ignitionLine=False)], False),
            ([ns(pandaType="redPanda", ignitionCan=True, ignitionLine=False)], False),
            ([ns(pandaType="redPanda", ignitionCan=False, ignitionLine=False)], True),
        ]
        for boards, expected in board_cases:
            with self.subTest(boards=boards):
                board.queue.append((board_time, boards, True))
                self.assertEqual(receiver.safe(agent), expected)
        self.params.values["IsOffroad"] = False
        self.assertFalse(receiver.safe(agent))
        receiver._close()
        self.assertTrue(board.closed)


class OwnerRoundTrip(unittest.TestCase):
    def test_owner_tap_poll_park_write_and_result_use_real_service_and_agent(self):
        loop = Loop(self)
        with tempfile.TemporaryDirectory() as tmp:
            loop.params.values.update(IsOnroad=False, IsOffroad=True, RainbowMode=False)
            loop.params.stored = loop.params.get
            loop.params.remove = lambda name: loop.params.values.pop(name, None)
            writer = OwnerApplier(loop.params, Path(tmp) / "receipts", KEY, loop.applier.journal)
            clock = 20_000_000_000
            sock = FakeSock()
            loop.agent.owner_receiver = OwnerReceiver(writer, lambda: clock, lambda: clock, lambda: sock)

            sid, _ = loop.service.request_owner_change({"RainbowMode": True})
            record_path = f"suggestions/{sid}.json"
            record = loop.service.store.get_json(record_path)
            loop.agent.tick()
            loop.uplink.run_once()
            self.assertFalse(loop.params.values["RainbowMode"])
            status, _, _ = dispatch(loop.service, "POST", "/a/" + record["approveToken"], {}, b"")
            self.assertEqual(status, 200)
            loop.uplink.run_once()
            loop.mono.t += 2
            sock.queue.append((clock, [ns(pandaType="redPanda", ignitionCan=True, ignitionLine=False)], True))
            loop.agent.tick()
            self.assertFalse(loop.params.values["RainbowMode"])

            sock.queue.append((clock, [ns(pandaType="redPanda", ignitionCan=False, ignitionLine=False)], True))
            loop.mono.t += 2
            loop.agent.tick()
            loop.uplink.run_once()
            self.assertTrue(loop.params.values["RainbowMode"])
            self.assertEqual(loop.service.store.get_json(record_path)["status"], "applied")
            ack, = [event for event in loop.sent if event["type"] == "apply_result"]
            self.assertEqual(problems(ack), [])
            self.assertIn(("GET", "/v1/owner/approvals"), loop.requests)
            self.assertNotIn(("GET", "/v1/approvals"), loop.requests)
            self.assertTrue(sock.closed)
