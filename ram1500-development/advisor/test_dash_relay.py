import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from dash_relay import DashboardRelay
from test_agent import AgentFixture


def car(speed=9., cruise=12.):
    return NS(canValid=True, vEgo=speed, cruiseState=NS(enabled=True, speed=cruise))


@unittest.skipUnless(hasattr(os, "geteuid") and Path("/proc/self/fd").is_dir(), "Linux Unix-domain IPC")
class DashboardRelayTests(AgentFixture):
    def setUp(self):
        super().setUp()
        group = tempfile.TemporaryDirectory()
        self.addCleanup(group.cleanup)
        self.folder = Path(group.name)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(self.sock.close)
        self.sock.bind(str(self.folder / "advisor.sock"))
        self.sock.setblocking(False)
        self.relay = DashboardRelay(self.folder)
        self.agent = self.make_agent(dash_relay=self.relay)
        self.params.values["IsOnroad"] = True
        self.agent.tick()
        self.assertEqual(self.read()["fields"], {})

    def read(self):
        return json.loads(self.sock.recv(8192))

    def tick(self, messages, delta=0.5):
        self.mono.t += delta
        stamp = round(self.mono.t * 1e9) - 1000
        for stream, (body, valid) in messages.items():
            self.socks[stream].queue.append((stamp, body, valid))
        self.agent.tick()
        return stamp // 1000

    def test_agent_sends_only_allowlisted_fresh_fields_and_no_new_readers(self):
        initial = len(self.all_socks)
        lateral = NS(which=lambda: "torqueState", torqueState=NS(active=True, saturated=False))
        when = self.tick({"carState": (car(), True), "carControl": (NS(latActive=True), True),
                          "controlsState": (NS(lateralControlState=lateral), True),
                          "drivingModelData": (NS(meta=NS(laneChangeState="preLaneChange")), True)})
        expected = {"carState":[when, 9., 12.], "carControl":[when, True], "controlsState":[when, False],
                    "drivingModelData":[when, "preLaneChange"]}
        self.assertEqual(self.read(), {"v":1, "source":"advisor", "at_us":when + 1, "fields":expected})
        self.assertEqual(len(self.all_socks), initial)
        self.tick({}, .1)
        with self.assertRaises(BlockingIOError):
            self.read()
        self.tick({}, .41)
        self.assertEqual(self.read()["fields"], {})
        self.assertNotIn("advisor.sock", str(self.uplink.events))

    def test_failed_decode_old_schema_or_invalid_sample_cannot_survive_a_drain(self):
        self.tick({"carState":(car(), True)})
        self.assertIn("carState", self.read()["fields"])
        self.broken_decode.add("carControl")
        self.socks["drivingModelData"].queue.append((round((self.mono.t+.45)*1e9), NS(meta=NS(laneChangeState="off"))))
        self.tick({"carState":(car(), False), "carControl":(NS(latActive=True), True)})
        self.assertEqual(self.read()["fields"], {})
        self.tick({"carState":(car(float("nan")),True), "drivingModelData":(NS(meta=NS(laneChangeState="bad")),True)})
        self.assertEqual(self.read()["fields"], {})
        self.tick({"carState":(car(0,255/3.6),True)})
        self.assertIsNone(self.read()["fields"]["carState"][2])

    def test_original_stamps_future_age_and_drive_boundary(self):
        now=101.
        end=101_000_000_000
        lane=NS(meta=NS(laneChangeState="off"))
        samples={"carControl":(end+1,NS(latActive=True)), "carState":(end-100_000_001,car()),
                 "drivingModelData":(end-499_999_999,lane)}
        self.relay.send(samples,now,100.)
        self.assertEqual(self.read()["fields"], {"drivingModelData":[100500000,"off"]})
        self.relay.send(samples,now,100.6)
        self.assertEqual(self.read()["fields"], {})

    def test_no_receiver_wrong_directory_and_backpressure_are_optional(self):
        self.folder.chmod(0o755)
        self.tick({"carControl":(NS(latActive=True),True)})
        with self.assertRaises(BlockingIOError):
            self.read()
        self.folder.chmod(0o700)
        # Fill the local socket's unread queue; every additional send returns at once.
        for _ in range(40):
            self.relay.send({},self.mono(),self.mono())
        (self.folder/"advisor.sock").unlink()
        with patch("dash_relay.DashboardRelay._encode", side_effect=AssertionError("no receiver")):
            self.relay.send({},self.mono(),self.mono())
        self.assertEqual(self.agent.tick_errors,0)

    def test_socket_symlink_and_parent_symlink_are_rejected(self):
        target=self.folder/"advisor.sock"
        actual=self.folder/"actual"
        target.rename(actual)
        target.symlink_to(actual)
        self.relay.send({},self.mono(),self.mono())
        target.unlink()
        actual.rename(target)
        alias=self.folder/"alias"
        alias.symlink_to(self.folder,target_is_directory=True)
        DashboardRelay(alias).send({},self.mono(),self.mono())
        with self.assertRaises(BlockingIOError):
            self.read()

    def test_relay_exception_does_not_disable_the_agent_or_discard_its_observations(self):
        with patch.object(self.relay, "send", side_effect=RuntimeError("display error")) as broken:
            self.tick({"carState": (car(9), True)})
            self.tick({"carState": (car(11), True)})
        self.assertEqual(broken.call_count, 1)
        self.assertTrue(self.agent.listening)
        self.assertEqual(self.agent.tick_errors, 0)
        self.assertEqual(self.agent.latest_state["carState"][1].vEgo, 11)
