"""The agent on the Comma, with fake settings, sockets, uplink and clocks.

What a drive sends: drive_start at once, drive_ready once this drive's sockets exist, one summary per minute
and one record per event from the pipeline, a settings_change when a watched setting moves, and drive_end.
Every drive gets fresh sockets, a fresh slot check and fresh listening state.
"""
import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as ns
from unittest import mock

import agent as agent_module
import measure_set
import messages
import schema_v2
import synth
from agent import Agent
from applier import Applier
from approval import sign_approval
from journal import Journal, NonceLedger
from measure_swings import WheelSwings
from pipeline import STREAMS
from synth import MS, SEC
from test_measure_set import BLOCKS, drive as eventful_drive, road
from test_settings_snapshot import BUNDLE
from uplink import Uplink

KEY = b"k" * 32
T0 = 1_000 * SEC
LEARNED, BASE = 0.3456789012345678, 0.2        # invented, with the precision a learned delay really has
LIVE = {"LagdToggle": False, "LagdToggleDelay": 0.096}      # one step below the learned delay
BEFORE = {"LagdToggle": True, "LagdToggleDelay": 0.2}
PROBE = {"result": "inert", "helperSha": "0123456789ab", "probeMs": 12}
SETTINGS = {"lagdToggle": True, "lagdToggleDelayS": 0.2, "nnlc": False, "laneChangeTimer": 0, "build": "0123456789ab"}
WALL = 1_795_000_000.0             # what the wall clock reads, in case anything sent took it from there


class FakeParams:
    """The agent's view of the settings: a value, or None when nothing is stored. A key in `broken` raises."""

    def __init__(self, values):
        self.values = dict(values)
        self.writes = []
        self.reads = []
        self.broken = set()

    def get(self, key):
        self.reads.append(key)
        if key in self.broken:
            raise OSError("unreadable setting")
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))

    def onroad(self):
        return self.values.get("IsOnroad") is True


class FakeUplink:
    def __init__(self):
        self.events = []
        self.incoming = []
        self.asked = 0
        self.backlog = 0            # what queue_len() says
        self.lost = 0               # what take_dropped() hands over next
        self.onroad = []            # every set_onroad call

    def send(self, event):
        self.events.append(event)

    def approvals(self):
        self.asked += 1
        out, self.incoming = self.incoming, []
        return out

    def queue_len(self):
        return self.backlog

    def take_dropped(self):
        lost, self.lost = self.lost, 0
        return lost

    def set_onroad(self, onroad):
        self.onroad.append(onroad)

    def types(self):
        return [e["type"] for e in self.events]

    def of(self, kind):
        return [e for e in self.events if e["type"] == kind]

    def data(self, kind):
        return [e["data"] for e in self.events if e["type"] == kind]


class FakeSock:
    def __init__(self):
        self.queue = []
        self.closed = False
        self.reads = 0

    def receive(self, non_blocking=True):
        self.reads += 1
        return self.queue.pop(0) if self.queue else None

    def close(self):
        self.closed = True


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def parked(i=0, t_ns=0):
    """What differs from the standard tick while the truck stands in Park with nothing switched on."""
    return {"carState": dict(v=0.0, gear="park"),
            "carControl": ns(latActive=False, enabled=False, actuators=ns(torque=0.0))}


def stored_delay(delay=LEARNED, status="estimated", blocks=30):
    """What the fake decoder turns into the learner's persisted message."""
    return json.dumps({"delay": delay, "status": status, "blocks": blocks}).encode()


class AgentFixture(unittest.TestCase):
    """An Agent over fake settings, sockets, uplink and clocks."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.params = FakeParams({"IsOnroad": False, **BEFORE, "LagdValueCache": 0.4,
                                  "NeuralNetworkLateralControl": False, "AutoLaneChangeTimer": 0,
                                  "ModelManager_ActiveBundle": dict(BUNDLE),
                                  "GitCommit": "0123456789abcdef0123456789abcdef01234567",
                                  "CarParams": str(BASE).encode(), "LiveDelay": stored_delay(),
                                  "CarParamsSP": b"RAM_1500_5TH_GEN"})
        self.applier = Applier(self.params, Journal(Path(folder.name) / "trial.json"),
                               NonceLedger(Path(folder.name) / "nonces.json"), KEY)
        self.uplink = FakeUplink()
        self.socks = {}             # the sockets of the newest drive
        self.all_socks = []         # every socket ever made
        self.decoded = []
        self.broken_decode = set()
        self.mono = Clock(100.0)
        self.wall = Clock(1000.0)
        self.slots_ok = True
        self.slot_checks = 0
        self.cpu_s, self.rss_mb, self.resource_reads = 0.0, 20.0, 0
        self.probe_result = dict(PROBE)
        self.probe_runs = 0
        self.errors = []
        self.agent = self.make_agent()
        self.count = 0
        self.log_ns = T0            # log time of the next tick made by `ticks`

    def make_agent(self, **overrides):
        kwargs = dict(params=self.params, applier=self.applier, uplink=self.uplink, socket_factory=self.make_sock,
                      decode=self.decode, slot_check=self.slot_check,
                      car_params_decode=lambda raw: ns(steerActuatorDelay=float(raw.decode())),
                      car_params_sp_decode=self.decode_sp, live_delay_decode=self.decode_delay,
                      delay_probe=self.run_probe, wall_clock=self.wall, monotonic=self.mono,
                      resource_probe=self.resources, subscribe_delay_s=0.0, on_tick_error=self.errors.append)
        kwargs.update(overrides)
        return Agent(**kwargs)

    # --- the fakes the agent is built on ---------------------------------------
    def make_sock(self, service):
        self.socks[service] = FakeSock()
        self.all_socks.append((service, self.socks[service]))
        return self.socks[service]

    def decode(self, service, raw):
        self.decoded.append(service)
        if service in self.broken_decode:
            raise ValueError("cannot decode")
        return raw

    def slot_check(self):
        self.slot_checks += 1
        return self.slots_ok

    def resources(self):
        self.resource_reads += 1
        return self.cpu_s, self.rss_mb

    def run_probe(self):
        self.probe_runs += 1
        return dict(self.probe_result)

    @staticmethod
    def decode_delay(raw):
        fields = json.loads(raw)
        return synth.live_delay(delay=fields["delay"], status=fields["status"], blocks=fields["blocks"],
                                estimate=fields["delay"], std=0.01)

    @staticmethod
    def decode_sp(raw):
        return ns(neuralNetworkLateralControl=ns(model=ns(name=raw.decode()), fuzzyFingerprint=False))

    # --- driving ------------------------------------------------------------------
    def approval(self, change=LIVE, mode="live"):
        self.count += 1
        return sign_approval(KEY, suggestion_id=f"s{self.count}", mode=mode, change=change,
                             issued_at=self.wall.t, nonce=f"{self.count:016d}")

    def drive(self):
        """The truck goes onroad; with no wait before listening, this first tick also sets up the sockets."""
        self.params.values["IsOnroad"] = True
        self.agent.tick()

    def end(self):
        self.params.values["IsOnroad"] = False
        self.agent.tick()

    def feed(self, batch):
        for stream, message in batch.items():
            self.socks[stream].queue.append(message)

    def play(self, batches, period_s=0.1):
        """One agent tick per batch, the batch's messages waiting on the sockets."""
        for batch in batches:
            self.feed(batch)
            self.agent.tick()
            self.mono.t = round(self.mono.t + period_s, 6)

    def ticks(self, n, period_ms=100.0, change=None):
        """Batches of a straight at 25 m/s that carry on in log time from where the last ones ended."""
        times = synth.ticks(n, period_ms, self.log_ns)
        self.log_ns = times[-1] + int(round(period_ms * MS))
        return synth.drive(times, change)

    def summaries(self):
        return self.uplink.data("summary")

    def leave_trial_open(self):
        """The state an earlier version left behind: trial values in force and the journal open."""
        self.applier.journal.open_trial(suggestion_id="s0", written=dict(LIVE), previous=dict(BEFORE))
        self.params.values.update(LIVE)


class StartTests(AgentFixture):
    def test_agent_start_carries_the_restore_result_and_belongs_to_no_drive(self):
        self.agent.start()
        (event,) = self.uplink.events
        self.assertEqual(event, {"v": 2, "type": "agent_start", "driveId": None, "id": f"{self.agent.boot_id}-1",
                                 "data": {"restore": "nothing_to_restore"}})
        self.assertRegex(self.agent.boot_id, "^[0-9a-f]{12}$")

    def test_start_restores_a_trial_left_open_when_offroad(self):
        self.leave_trial_open()
        self.agent.start()
        self.assertEqual({key: self.params.values[key] for key in BEFORE}, BEFORE)
        self.assertEqual(sorted(self.params.writes), [("LagdToggle", True), ("LagdToggleDelay", 0.2)])
        self.assertFalse(self.applier.journal.is_open())
        self.assertEqual(self.uplink.events[-1]["data"], {"restore": "restored"})
        for _ in range(3):
            self.agent.tick()
        self.assertEqual(self.uplink.types(), ["agent_start"])       # no trial_ended, no apply_result
        self.assertEqual(len(self.params.writes), 2)                  # written once
        self.drive()
        self.end()
        self.assertEqual(self.uplink.data("drive_end")[0]["restore"], "nothing_to_restore")
        self.assertEqual(self.uplink.of("trial_ended") + self.uplink.of("apply_result"), [])

    def test_an_agent_started_in_mid_drive_restores_at_drive_end(self):
        self.leave_trial_open()
        self.params.values["IsOnroad"] = True
        self.agent.start()
        self.assertEqual(self.uplink.events[-1]["data"], {"restore": "deferred_until_drive_end"})
        self.agent.tick()
        start = self.uplink.data("drive_start")[0]
        self.assertEqual((start["resumed"], start["settings"]["lagdToggle"], start["settings"]["lagdToggleDelayS"]),
                         (True, False, 0.096))
        self.assertEqual(self.params.writes, [])                      # not while driving
        self.end()
        self.assertEqual({key: self.params.values[key] for key in BEFORE}, BEFORE)
        end = self.uplink.data("drive_end")[0]
        self.assertEqual((end["restore"], end["settings"]["lagdToggle"], end["settings"]["lagdToggleDelayS"]),
                         ("restored", True, 0.2))
        # The settings are not what drive_start said any more, and the drive says so.
        self.assertEqual(self.uplink.data("settings_change"),
                         [{"settings": end["settings"], "changed": ["lagdToggle", "lagdToggleDelayS"]}])
        self.assertEqual(self.uplink.types()[-2:], ["settings_change", "drive_end"])
        self.assertEqual(self.uplink.of("trial_ended") + self.uplink.of("apply_result"), [])


class DriveStartTests(AgentFixture):
    def test_drive_start_is_sent_at_once_with_the_settings_and_the_persisted_delay(self):
        self.agent.subscribe_delay_s = 30.0
        self.agent.tick()                                              # the agent starts at boot, before any drive
        self.assertEqual(self.uplink.events, [])
        self.drive()
        (event,) = self.uplink.events
        self.assertEqual((event["v"], event["type"]), (2, "drive_start"))
        self.assertRegex(event["driveId"], "^[0-9a-f]{32}$")
        data = event["data"]
        self.assertEqual(set(data), {"resumed", "settings", "delay", "agent"})
        self.assertIs(data["resumed"], False)
        self.assertEqual({k: v for k, v in data["settings"].items() if k != "modelBundleId"}, SETTINGS)
        self.assertRegex(data["settings"]["modelBundleId"], "^[0-9a-f]{12}$")
        self.assertEqual(data["delay"], {"learnedS": 0.3457, "estimateS": 0.3457, "estimateStdS": 0.01,
                                         "validBlocks": 30, "status": "estimated", "cacheS": 0.4,
                                         "baseActuatorS": 0.2, "outOfBand": False})

    def test_the_agent_says_what_it_is(self):
        self.drive()
        about = self.uplink.data("drive_start")[0]["agent"]
        self.assertEqual(about, {"version": agent_module.AGENT_VERSION, "applyEnabled": False,
                                 "measures": [m.name for m in measure_set.MEASURES]})
        self.assertRegex(about["version"], r"^[0-9]+(\.[0-9]+)*$")

    def test_the_learned_delay_is_never_read_from_the_cache(self):
        # The cache holds a stale 0.40; the learner's own persisted message says 0.3457, estimated.
        self.params.values.update(LagdValueCache=0.40, LiveDelay=stored_delay(0.3457))
        self.drive()
        delay = self.uplink.data("drive_start")[0]["delay"]
        self.assertEqual((delay["learnedS"], delay["cacheS"]), (0.3457, 0.4))

    def test_no_trusted_delay_without_an_estimate(self):
        for stored, expected in ((stored_delay(0.4, "unestimated", 2), (None, "unestimated", False)),
                                 (stored_delay(0.40), (0.4, "estimated", True)),
                                 (b"not a message", (None, None, False)), (None, (None, None, False))):
            with self.subTest(stored=stored):
                self.setUp()
                self.params.values["LiveDelay"] = stored
                self.drive()
                delay = self.uplink.data("drive_start")[0]["delay"]
                self.assertEqual((delay["learnedS"], delay["status"], delay["outOfBand"]), expected)
                if expected[1] is None:
                    self.assertEqual([delay[k] for k in ("learnedS", "estimateS", "estimateStdS", "validBlocks",
                                                         "status")], [None] * 5)
                    self.assertEqual((delay["cacheS"], delay["baseActuatorS"]), (0.4, 0.2))

    def test_a_setting_read_that_raises_gives_null_never_a_default(self):
        self.params.broken = {"NeuralNetworkLateralControl", "LagdValueCache"}
        self.drive()
        data = self.uplink.data("drive_start")[0]
        self.assertIsNone(data["settings"]["nnlc"])
        self.assertIsNone(data["delay"]["cacheS"])
        self.assertEqual(data["settings"]["lagdToggle"], True)

    def test_resumed_is_true_only_when_the_truck_was_onroad_at_the_agents_first_tick(self):
        self.params.values["IsOnroad"] = True
        self.agent.tick()
        self.end()
        self.drive()
        self.assertEqual([d["resumed"] for d in self.uplink.data("drive_start")], [True, False])
        fresh = self.make_agent()
        self.params.values["IsOnroad"] = False
        fresh.tick()
        self.params.values["IsOnroad"] = True
        fresh.tick()
        self.assertIs(self.uplink.data("drive_start")[-1]["resumed"], False)

    def test_the_uplink_is_told_when_a_drive_starts_and_ends(self):
        self.drive()
        self.assertEqual(self.uplink.onroad, [True])
        self.end()
        self.assertEqual(self.uplink.onroad, [True, False])

    def test_every_event_of_a_drive_carries_its_drive_id_and_a_rising_number(self):
        self.agent.start()
        self.drive()
        self.play(self.ticks(700))
        self.end()
        self.drive()
        self.end()
        ids = [e["id"] for e in self.uplink.events]
        self.assertEqual(ids, [f"{self.agent.boot_id}-{i}" for i in range(1, len(ids) + 1)])
        first, second = (d["driveId"] for d in self.uplink.of("drive_start"))
        self.assertNotEqual(first, second)
        drive_ids = [e["driveId"] for e in self.uplink.events]
        self.assertEqual(drive_ids[0], None)
        cut = self.uplink.types().index("drive_end") + 1
        self.assertEqual(set(drive_ids[1:cut]), {first})
        self.assertEqual(set(drive_ids[cut:]), {second})


class ListeningTests(AgentFixture):
    def test_waits_thirty_seconds_then_checks_the_slots_and_makes_eight_new_sockets(self):
        self.assertEqual(agent_module.SUBSCRIBE_DELAY_S, 30.0)
        agent = self.agent = self.make_agent(subscribe_delay_s=agent_module.SUBSCRIBE_DELAY_S)
        self.drive()
        for _ in range(299):
            self.mono.t = round(self.mono.t + 0.1, 6)
            agent.tick()
        self.assertIsNone(agent.socks)
        self.assertEqual((self.slot_checks, self.probe_runs, self.uplink.types()), (0, 0, ["drive_start"]))
        self.mono.t = round(self.mono.t + 0.1, 6)
        agent.tick()
        self.assertEqual(set(self.socks), set(STREAMS))
        self.assertEqual(len(STREAMS), 8)
        self.assertIn("liveDelay", STREAMS)
        self.assertEqual((self.slot_checks, self.probe_runs), (1, 1))
        for _ in range(50):
            self.mono.t = round(self.mono.t + 0.1, 6)
            agent.tick()
        self.assertEqual((self.slot_checks, self.probe_runs, len(self.all_socks)), (1, 1, 8))

    def test_drive_ready_follows_the_sockets_with_the_probe_and_the_matched_model(self):
        self.drive()
        self.assertEqual(self.uplink.types(), ["drive_start", "drive_ready"])
        self.assertEqual(self.uplink.data("drive_ready")[0],
                         {"delayProbe": PROBE, "nnlcModel": "ram", "nnlcFuzzy": False})
        self.assertEqual(self.uplink.events[1]["driveId"], self.uplink.events[0]["driveId"])

    def test_the_matched_model_token(self):
        for stored, token in ((b"MOCK", "mock"), (b"HYUNDAI_IONIQ_5", "other"), (None, None)):
            with self.subTest(stored=stored):
                self.setUp()
                self.params.values["CarParamsSP"] = stored
                self.drive()
                ready = self.uplink.data("drive_ready")[0]
                self.assertEqual((ready["nnlcModel"], ready["nnlcFuzzy"]), (token, None if token is None else False))

    def test_the_probe_runs_at_every_drive_start_and_never_at_agent_start(self):
        self.agent.start()
        for _ in range(5):
            self.agent.tick()
        self.assertEqual(self.probe_runs, 0)
        for expected in (1, 2, 3):
            self.drive()
            self.assertEqual(self.probe_runs, expected)
            self.end()
        self.assertEqual(len(self.uplink.of("drive_ready")), 3)

    def test_an_agent_without_a_probe_reports_unknown(self):
        self.agent = self.make_agent(delay_probe=None)
        self.drive()
        self.assertEqual(self.uplink.data("drive_ready")[0]["delayProbe"],
                         {"result": "unknown", "helperSha": None, "probeMs": 0})

    def test_full_slots_disable_listening_for_this_drive_only(self):
        self.slots_ok = False
        self.drive()
        self.assertIsNone(self.agent.socks)
        self.assertEqual(self.all_socks, [])
        self.assertEqual(self.uplink.types(), ["drive_start", "listening_disabled"])
        self.assertEqual(self.uplink.data("listening_disabled")[0],
                         {"reason": "subscriber_slots_full", "cpuPct": None, "rssMb": None})
        for _ in range(20):
            self.mono.t += 1.0
            self.agent.tick()
        self.assertEqual((self.slot_checks, self.probe_runs), (1, 0))  # asked once, not on every tick
        self.end()
        end = self.uplink.data("drive_end")[0]
        self.assertEqual((end["listening"], end["windows"], end["delayProbe"]), (False, 0, None))
        self.assertEqual(end["totals"], {"windowS": 0.0, "unobservedS": 0.0, "steeringS": 0.0, "movingS": 0.0})
        self.assertEqual(self.uplink.types(), ["drive_start", "listening_disabled", "drive_end"])
        self.slots_ok = True
        self.drive()                                                   # the next drive listens again
        self.assertEqual(set(self.socks), set(STREAMS))
        self.assertEqual(self.uplink.types()[-2:], ["drive_start", "drive_ready"])

    def test_decodes_only_the_newest_message_of_each_stream(self):
        self.drive()
        for batch in self.ticks(5):
            self.feed(batch)
        self.agent.tick()
        self.assertEqual(sorted(self.decoded), sorted(synth.EVERY_TICK))
        self.assertTrue(all(sock.queue == [] for sock in self.socks.values()))     # and every socket was emptied

    def test_a_message_that_cannot_be_decoded_is_that_stream_missing_for_the_tick(self):
        self.drive()
        self.broken_decode = {"carOutput"}
        self.play(self.ticks(700))
        first = self.summaries()[0]
        self.assertEqual(first["latOnS"], 60.0)
        self.assertEqual(first["health"]["staleS"], {"co": 60.0})
        self.assertEqual(first["health"]["tickErrors"], 0)
        self.assertEqual(self.errors, [])

    def test_a_socket_that_cannot_be_made_is_tried_again(self):
        calls = []

        def factory(service):
            calls.append(service)
            if len(calls) == 3:
                raise OSError("no slot")
            return self.make_sock(service)
        self.agent = self.make_agent(socket_factory=factory)
        self.params.values["IsOnroad"] = True
        self.assertEqual(self.agent.step(), agent_module.TICK_S + agent_module.ERROR_BACKOFF_S)
        self.assertIsNone(self.agent.socks)
        self.assertEqual(self.uplink.types(), ["drive_start"])        # no drive_ready for sockets that do not exist
        self.assertEqual(self.agent.step(), agent_module.TICK_S)
        self.assertEqual(set(self.agent.socks), set(STREAMS))
        self.assertEqual(self.uplink.types(), ["drive_start", "drive_ready"])


class SummaryTests(AgentFixture):
    def test_one_summary_per_minute_from_the_pipeline(self):
        self.drive()
        self.play(self.ticks(605))
        (summary,) = self.summaries()
        self.assertEqual((summary["w"], summary["n"], summary["windowS"], summary["tMonoS"]), (0, 600, 60.0, 1000.0))
        self.assertEqual((summary["latOnS"], summary["steeringS"], summary["movingS"]), (60.0, 60.0, 60.0))
        self.assertEqual(self.uplink.types(), ["drive_start", "drive_ready", "summary"])

    def test_the_summary_has_no_field_of_the_first_version(self):
        self.drive()
        self.play(self.ticks(605))
        old = {"engagedMinutes", "straightMinutes", "weaveReversals", "weavePerStraightMinute", "curveSamples",
               "curveRelError", "torqueLimitFraction", "steerWarningEpisodes", "laneChanges", "overrideEpisodes",
               "hardBrakes", "speedMeanMs", "samples", "startT", "trialActive", "trialsThisDrive"}
        self.assertFalse(old & set(self.summaries()[0]))
        start = self.uplink.data("drive_start")[0]
        self.assertFalse({"learnedDelayS", "baseActuatorDelayS"} & set(start))

    def test_control_message_a_few_ms_newer_than_its_car_state_still_counts(self):
        # Regression for the first test drive: steering read as half on, because carControl of the same cycle
        # is stamped a few ms after carState.
        self.drive()
        batches = self.ticks(605)
        for batch in batches:
            t_ns = batch["carState"][0]
            batch["carControl"] = (t_ns + 5 * MS, batch["carControl"][1])
            batch["controlsState"] = (t_ns + 6 * MS, batch["controlsState"][1])
        self.play(batches)
        self.assertEqual(self.summaries()[0]["latOnS"], 60.0)

    def test_full_rate_streams_drained_at_a_loop_a_little_slower_than_ten_hz(self):
        # carState at 100 Hz, carControl stamped 5 ms after it and on for a known 40 of 60 s; the agent ticks
        # every 0.1 s plus 0.5 to 3 ms, so the tick walks through the 10 ms cycle.
        self.drive()
        rng = random.Random(7)
        events = []
        for i in range(6200):
            t_ns = T0 + i * 10 * MS
            on = 10.0 <= i / 100.0 < 50.0
            events.append((t_ns, "carState", synth.car_state()))
            events.append((t_ns + 5 * MS, "carControl", synth.car_control(lat=on)))
            events.append((t_ns + 6 * MS, "controlsState", synth.controls_state()))
            if i % 100 == 0:
                events.append((t_ns + 1 * MS, "onroadEvents", synth.alerts()))
        events.sort(key=lambda e: e[0])
        tick_ns, i = T0, 0
        while tick_ns < T0 + 62 * SEC:
            while i < len(events) and events[i][0] <= tick_ns:
                t_ns, stream, msg = events[i]
                self.socks[stream].queue.append((t_ns, msg))
                i += 1
            self.agent.tick()
            step_ns = 100 * MS + rng.randrange(500_000, 3_000_000)
            tick_ns += step_ns
            self.mono.t += step_ns / 1e9
        self.end()
        out = self.summaries()
        lat_on = sum(s["latOnS"] for s in out)
        self.assertAlmostEqual(lat_on, 40.0, delta=0.4)                # within 99 to 101% of the truth
        self.assertAlmostEqual(sum(s["steeringS"] for s in out), 40.0, delta=0.4)
        self.assertTrue(580 <= out[0]["n"] <= 598)                     # slower than 10 Hz, by the work of a tick
        self.assertEqual(out[0]["health"]["gapN"], 0)

    def test_ten_minutes_at_a_loop_period_of_103_ms(self):
        self.drive()
        self.play(self.ticks(5900, period_ms=103.0), period_s=0.103)
        out = self.summaries()
        self.assertEqual(len(out), 10)
        for s in out:
            self.assertIn(s["n"], (582, 583))
            self.assertAlmostEqual(s["windowS"], 60.0, delta=0.11)
            self.assertAlmostEqual(s["latOnS"], 60.0, delta=0.11)
            self.assertEqual((s["unobservedS"], s["health"]["gapN"], s["health"]["tickErrors"]), (0.0, 0, 0))

    def test_the_learners_messages_reach_the_delay_block(self):
        self.drive()
        self.play(self.ticks(700, change=lambda i, t: {"liveDelay": dict(delay=0.35 + i / 1e6)} if i % 3 == 0 else None))
        first = self.summaries()[0]
        self.assertEqual(first["delay"], {"valueS": 0.3506, "minS": 0.35, "maxS": 0.3506, "status": "estimated",
                                          "blocks": 30})

    def test_records_of_the_pipeline_are_sent_as_they_come(self):
        self.drive()
        self.play(eventful_drive(900, stop_at_s=20.0, lane_change_at_s=30.0, limit_at_s=50.0))
        self.end()
        types = self.uplink.types()
        self.assertEqual(types[:2], ["drive_start", "drive_ready"])
        self.assertEqual(types[-1], "drive_end")
        self.assertEqual({t: types.count(t) for t in ("brake", "lane_change", "limit_event")},
                         {"brake": 1, "lane_change": 1, "limit_event": 1})
        self.assertEqual(sum(s["brakeEpisodes"] for s in self.summaries()), 1)

    def test_a_summary_over_the_size_cap_loses_blocks_in_the_stated_order(self):
        self.drive()
        with mock.patch.object(messages, "SUMMARY_CAP_BYTES", 900):
            self.play(road(1300))
        events = self.uplink.of("summary")
        full = events[1]
        self.assertEqual(full["data"]["trimmed"], ["lag", "curve", "ay", "swings", "lane"])
        self.assertLessEqual(len(messages.encode(full)), 900)
        self.assertFalse(set(BLOCKS) & set(full["data"]))
        self.assertEqual(full["data"]["steeringS"], 60.0)              # the owner-facing numbers are never trimmed
        self.assertIn("cpuPct", full["data"]["health"])

    def test_at_the_real_cap_nothing_is_trimmed(self):
        self.drive()
        self.play(road(1300, live_delay=True))
        for event in self.uplink.of("summary"):
            self.assertNotIn("trimmed", event["data"])
            self.assertLess(len(messages.encode(event)), 1700)
        self.assertTrue(set(BLOCKS) | {"delay"} <= set(self.summaries()[1]))


class DriveEndTests(AgentFixture):
    def test_the_last_partial_window_then_drive_end(self):
        self.drive()
        self.play(self.ticks(700, change=lambda i, t: {"liveDelay": dict(delay=0.3501, blocks=41)} if i % 3 == 0 else None))
        self.params.values["LagdValueCache"] = 0.3501
        self.end()
        self.assertEqual(self.uplink.types(), ["drive_start", "drive_ready", "summary", "summary", "drive_end"])
        last = self.summaries()[-1]
        self.assertEqual((last["w"], last["n"], last["windowS"]), (1, 100, 10.0))
        end = self.uplink.data("drive_end")[0]
        self.assertEqual(set(end), {"windows", "listening", "restore", "failed", "totals", "settings", "delay",
                                    "delayProbe", "truncatedLaneChanges"})
        self.assertEqual((end["windows"], end["listening"], end["restore"], end["failed"],
                          end["truncatedLaneChanges"]), (2, True, "nothing_to_restore", [], 0))
        self.assertEqual(end["totals"], {"windowS": 70.0, "unobservedS": 0.0, "steeringS": 70.0, "movingS": 70.0})
        self.assertEqual(end["settings"], self.uplink.data("drive_start")[0]["settings"])
        self.assertEqual(end["delayProbe"], PROBE)
        # The delay record is built from the newest message of the learner, not from the persisted one.
        self.assertEqual(end["delay"], {"learnedS": 0.3501, "estimateS": 0.33, "estimateStdS": 0.01,
                                        "validBlocks": 41, "status": "estimated", "cacheS": 0.3501,
                                        "baseActuatorS": 0.2, "outOfBand": False})

    def test_a_drive_without_a_message_from_the_learner_has_no_delay_at_its_end(self):
        self.drive()
        self.play(self.ticks(100))
        self.end()
        delay = self.uplink.data("drive_end")[0]["delay"]
        self.assertEqual([delay[k] for k in ("learnedS", "estimateS", "status")], [None, None, None])
        self.assertEqual((delay["cacheS"], delay["baseActuatorS"], delay["outOfBand"]), (0.4, 0.2, False))

    def test_the_sockets_are_closed_and_nothing_of_the_drive_is_kept(self):
        self.drive()
        self.play(self.ticks(100))
        socks = dict(self.socks)
        self.end()
        self.assertTrue(all(sock.closed for sock in socks.values()))
        self.assertIsNone(self.agent.socks)
        self.assertIsNone(self.agent.drive_id)
        self.assertIsNone(self.agent.pipeline)
        self.assertIsNone(self.agent.health)
        self.assertIs(self.agent.cpu_breach, False)
        self.assertIs(self.agent.listening, True)

    def test_the_drive_ends_during_a_stop_and_a_lane_change(self):
        self.drive()
        self.play(eventful_drive(215, stop_at_s=19.0, lane_change_at_s=20.0))
        self.end()
        self.assertEqual(self.uplink.types()[2:], ["brake", "summary", "drive_end"])
        self.assertEqual(self.summaries()[-1]["lcTruncated"], 1)
        self.assertEqual(self.uplink.data("drive_end")[0]["truncatedLaneChanges"], 1)

    def test_an_offroad_tick_reads_nothing_but_the_onroad_flag(self):
        self.agent.start()
        before = (list(self.params.reads), self.resource_reads)
        for _ in range(50):
            self.agent.tick()
        self.assertEqual((self.params.reads, self.resource_reads), before)


class TwoDrivesTests(AgentFixture):
    """Every drive of one boot gets fresh sockets, a fresh slot check and fresh listening state."""

    def test_a_second_drive_after_twenty_minutes_parked(self):
        self.drive()
        self.play(self.ticks(700))
        first_socks = dict(self.socks)
        self.end()
        left_at = self.log_ns
        first_socks["carState"].queue.append((left_at, synth.car_state()))     # left unread on the old socket
        self.mono.t += 1200.0
        self.log_ns += 1200 * SEC
        start2 = self.log_ns
        self.drive()
        self.assertEqual((len(self.all_socks), self.slot_checks, self.probe_runs), (16, 2, 2))
        self.assertTrue(all(self.socks[s] is not first_socks[s] for s in STREAMS))
        self.play(self.ticks(700))
        self.end()
        self.assertEqual(first_socks["carState"].queue, [(left_at, mock.ANY)])     # never read
        self.assertEqual([s["w"] for s in self.summaries()], [0, 1, 0, 1])
        second = self.summaries()[2]
        self.assertEqual(second["tMonoS"], start2 / 1e9)               # its grid starts inside drive 2
        self.assertEqual((second["unobservedS"], second["health"]["gapN"], second["n"]), (0.0, 0, 600))
        ends = self.uplink.data("drive_end")
        self.assertEqual([e["windows"] for e in ends], [2, 2])
        self.assertEqual([e["totals"]["windowS"] for e in ends], [70.0, 70.0])
        self.assertEqual(self.uplink.types().count("drive_ready"), 2)

    def test_a_message_from_before_the_drive_on_a_new_socket_is_not_measured(self):
        # The learner's stream has no freshness limit, so only the start of the drive keeps an old message out.
        self.drive()
        self.socks["liveDelay"].queue.append((T0 - 30 * SEC, synth.live_delay(0.2)))
        self.play(self.ticks(300))
        self.end()
        (summary,) = self.summaries()
        self.assertNotIn("delay", summary)
        self.assertIsNone(self.uplink.data("drive_end")[0]["delay"]["learnedS"])

    def test_listening_is_on_again_after_a_self_limit_in_the_drive_before(self):
        self.drive()
        self.rss_mb = 200.0
        self.play(self.ticks(250))
        self.assertIs(self.agent.listening, False)
        self.assertIn("listening_disabled", self.uplink.types())
        self.end()
        self.rss_mb = 20.0
        self.mono.t += 1200.0
        self.log_ns += 1200 * SEC
        self.drive()
        self.assertIs(self.agent.listening, True)
        self.play(self.ticks(700))
        self.end()
        second = [e for e in self.uplink.events if e["driveId"] == self.uplink.of("drive_start")[1]["driveId"]]
        self.assertEqual([e["type"] for e in second], ["drive_start", "drive_ready", "summary", "summary", "drive_end"])
        self.assertEqual(second[2]["data"]["w"], 0)
        self.assertIs(second[-1]["data"]["listening"], True)


class SettingsWatchTests(AgentFixture):
    def test_the_lane_change_timer_flips_in_window_three(self):
        self.drive()
        batches = self.ticks(3100)
        self.play(batches[:1900])
        self.params.values["AutoLaneChangeTimer"] = 2
        self.play(batches[1900:])
        self.end()
        (change,) = self.uplink.of("settings_change")
        self.assertEqual(change["data"]["changed"], ["laneChangeTimer"])
        self.assertEqual({k: v for k, v in change["data"]["settings"].items() if k != "modelBundleId"},
                         {**SETTINGS, "laneChangeTimer": 2})
        self.assertEqual(change["data"]["settings"]["modelBundleId"],
                         self.uplink.data("drive_start")[0]["settings"]["modelBundleId"])
        where = self.uplink.events.index(change)
        before = self.uplink.events[where - 1]
        self.assertEqual((before["type"], before["data"]["w"]), ("summary", 3))    # seen when window 3 closed
        self.assertEqual(self.uplink.data("drive_end")[0]["settings"]["laneChangeTimer"], 2)

    def test_the_five_watched_settings_are_read_once_per_window(self):
        self.drive()
        del self.params.reads[:]
        self.play(self.ticks(1205))
        watched = ["LagdToggle", "LagdToggleDelay", "NeuralNetworkLateralControl", "AutoLaneChangeTimer", "GitCommit"]
        self.assertEqual(sorted(self.params.reads), sorted(watched * 2))       # two windows closed

    def test_a_setting_changed_and_changed_back_inside_one_window_is_not_seen(self):
        self.drive()
        batches = self.ticks(700)
        self.play(batches[:200])
        self.params.values["LagdToggle"] = False
        self.play(batches[200:400])
        self.params.values["LagdToggle"] = True
        self.play(batches[400:])
        self.end()
        self.assertEqual(self.uplink.of("settings_change"), [])

    def test_each_change_is_reported_once_against_the_last_snapshot_sent(self):
        self.drive()
        batches = self.ticks(2500)
        self.play(batches[:100])
        self.params.values.update(NeuralNetworkLateralControl=True, LagdToggleDelay=0.25)
        self.play(batches[100:1300])
        self.params.values.update(NeuralNetworkLateralControl=False)
        self.play(batches[1300:])
        self.end()
        self.assertEqual([c["changed"] for c in self.uplink.data("settings_change")],
                         [["lagdToggleDelayS", "nnlc"], ["nnlc"]])

    def test_a_setting_that_cannot_be_read_any_more_is_a_change_to_null(self):
        self.drive()
        batches = self.ticks(700)
        self.play(batches[:100])
        self.params.broken = {"AutoLaneChangeTimer"}
        self.play(batches[100:])
        (change,) = self.uplink.data("settings_change")
        self.assertEqual((change["changed"], change["settings"]["laneChangeTimer"]), (["laneChangeTimer"], None))

    def test_the_model_bundle_is_compared_at_drive_end(self):
        self.drive()
        self.play(self.ticks(100))
        self.params.values["ModelManager_ActiveBundle"] = {**BUNDLE, "ref": "ffff0000"}
        self.play(self.ticks(700))                                      # a window closes: the bundle is not read
        self.assertEqual(self.uplink.of("settings_change"), [])
        self.end()
        (change,) = self.uplink.data("settings_change")
        self.assertEqual(change["changed"], ["modelBundleId"])
        self.assertEqual(self.uplink.types()[-2:], ["settings_change", "drive_end"])
        self.assertEqual(self.uplink.data("drive_end")[0]["settings"], change["settings"])

    def test_a_changed_download_status_is_no_change(self):
        self.drive()
        self.play(self.ticks(100))
        self.params.values["ModelManager_ActiveBundle"] = dict(reversed(list({**BUNDLE, "status": "downloading"}.items())))
        self.end()
        self.assertEqual(self.uplink.of("settings_change"), [])
        self.assertEqual(self.uplink.data("drive_end")[0]["settings"], self.uplink.data("drive_start")[0]["settings"])


class HealthTests(AgentFixture):
    def test_the_resource_probe_is_read_once_per_ten_seconds(self):
        self.drive()
        self.play(self.ticks(599))
        self.assertEqual(self.resource_reads, 6)                       # in 60 s: 6, not 600
        self.assertEqual(agent_module.HEALTH_WINDOW_S, 10.0)

    def test_cpu_memory_queue_and_drops_of_the_window(self):
        self.agent = self.make_agent(resource_probe=lambda: self.resources_at(0.031))
        self.rss_mb = 21.26
        self.uplink.backlog, self.uplink.lost = 4, 2
        self.drive()
        self.play(self.ticks(1205))
        first, second = self.summaries()
        self.assertEqual(first["health"], {"gapN": 0, "modelGapN": 0, "staleS": {}, "staleSteerS": {},
                                           "cpuPct": 3.1, "cpuPeakPct": 3.1, "rssMb": 21.3, "queue": 4,
                                           "dropped": 2, "tickErrors": 0})
        self.assertEqual((second["health"]["dropped"], second["health"]["queue"]), (0, 4))     # since the last summary

    def resources_at(self, share):
        """CPU time that grows by `share` of the clock; a step of extra work can be added through `self.cpu_s`."""
        self.resource_reads += 1
        return self.cpu_s + share * (self.mono.t - 100.0), self.rss_mb

    def test_the_peak_is_the_highest_ten_second_reading_of_the_window(self):
        self.agent = self.make_agent(resource_probe=lambda: self.resources_at(0.02))
        self.drive()
        batches = self.ticks(1205)
        self.play(batches[:250])
        self.cpu_s += 0.9                                              # nine percent more in one ten-second stretch
        self.play(batches[250:])
        first, second = self.summaries()
        self.assertEqual((first["health"]["cpuPeakPct"], second["health"]["cpuPeakPct"]), (11.0, 2.0))
        self.assertAlmostEqual(first["health"]["cpuPct"], 2.0 + 9.0 / 5, delta=0.05)      # five readings in window 0
        self.assertEqual(second["health"]["cpuPct"], 2.0)

    def test_a_window_too_short_for_a_reading_says_so_with_null(self):
        self.drive()
        self.play(self.ticks(50))
        self.end()
        health = self.summaries()[0]["health"]
        self.assertEqual((health["cpuPct"], health["cpuPeakPct"], health["rssMb"]), (None, None, 20.0))

    def test_an_agent_without_a_resource_probe_still_reports_the_rest(self):
        self.agent = self.make_agent(resource_probe=None)
        self.drive()
        self.play(self.ticks(605))
        health = self.summaries()[0]["health"]
        self.assertEqual((health["cpuPct"], health["cpuPeakPct"], health["rssMb"], health["queue"]),
                         (None, None, None, 0))

    def test_two_ten_second_windows_above_fifteen_percent_stop_the_listening(self):
        self.assertEqual((agent_module.CPU_LIMIT_PCT, agent_module.RSS_LIMIT_MB), (15.0, 150.0))
        self.agent = self.make_agent(resource_probe=lambda: self.resources_at(0.2))
        self.drive()
        batches = self.ticks(400)
        self.play(batches[:150])
        self.assertIs(self.agent.listening, True)                      # one window over the limit is not enough
        self.play(batches[150:250])
        self.assertIs(self.agent.listening, False)
        socks = dict(self.socks)
        self.play(batches[250:])
        # What was measured so far is sent at once (the partial window), then the reason.
        self.assertEqual(self.uplink.types(), ["drive_start", "drive_ready", "summary", "listening_disabled"])
        self.assertEqual(self.uplink.data("listening_disabled")[0],
                         {"reason": "self_limit", "cpuPct": 20.0, "rssMb": 20.0})
        self.assertAlmostEqual(self.summaries()[0]["windowS"], 20.0, delta=0.21)
        self.assertTrue(all(sock.closed for sock in socks.values()))
        # Ticks 201 to 399 came after the limit: nothing was read from the sockets any more.
        self.assertTrue(all(len(sock.queue) == 199 for name, sock in socks.items() if name in synth.EVERY_TICK))
        reads = self.resource_reads
        self.end()
        self.assertEqual(self.resource_reads, reads)                   # no more readings once listening is off
        self.assertEqual(self.uplink.types()[-1], "drive_end")
        end = self.uplink.data("drive_end")[0]
        self.assertEqual((end["listening"], end["windows"], end["delayProbe"]), (False, 1, PROBE))
        self.assertEqual(len(self.summaries()), 1)                     # the window is not sent a second time

    def test_one_window_over_the_limit_between_two_under_it_does_nothing(self):
        self.agent = self.make_agent(resource_probe=lambda: self.resources_at(0.05))
        self.drive()
        batches = self.ticks(700)
        for start in range(0, 700, 100):
            if start in (100, 300, 500):
                self.cpu_s += 2.0                                      # every other ten seconds: 25 percent
            self.play(batches[start:start + 100])
        self.assertIs(self.agent.listening, True)
        self.assertEqual(self.summaries()[0]["health"]["cpuPeakPct"], 25.0)

    def test_memory_over_the_limit_stops_the_listening_at_once(self):
        self.drive()
        self.play(self.ticks(50))
        self.rss_mb = 151.0
        self.play(self.ticks(100))
        self.assertIs(self.agent.listening, False)
        self.assertEqual(self.uplink.data("listening_disabled")[0], {"reason": "self_limit", "cpuPct": 0.0,
                                                                     "rssMb": 151.0})

    def test_a_real_queue_of_three_with_five_events_sent_while_offline(self):
        def offline(method, url, headers, body, timeout):
            raise OSError("offline")
        real = Uplink("https://example.test", KEY, opener=offline, max_queue=3)
        self.agent = self.make_agent(uplink=real)
        self.drive()                                                   # drive_start and drive_ready: two events
        for i in range(3):
            real.send({"id": f"extra-{i}"})                            # five in all, into a queue of three
        self.play(self.ticks(1205))
        newest = [e for e in real.queue if e.get("type") == "summary"]
        self.assertEqual(newest[-2]["data"]["health"]["dropped"], 2)   # the first summary: two were lost before it
        self.assertEqual(newest[-2]["data"]["health"]["queue"], 3)
        self.assertEqual(newest[-1]["data"]["health"]["dropped"], 1)   # then the first summary itself pushed one out


class TickErrorTests(AgentFixture):
    def test_step_is_a_tick_and_says_how_long_to_sleep(self):
        self.assertEqual((agent_module.TICK_S, agent_module.ERROR_BACKOFF_S), (0.1, 1.0))
        self.assertEqual(self.agent.step(), 0.1)
        self.assertEqual((self.agent.tick_errors, self.errors), (0, []))

    def test_a_tick_that_raises_is_counted_reported_and_followed_by_a_longer_sleep(self):
        self.params.onroad = mock.Mock(side_effect=[OSError("settings unreadable"), False])
        self.assertEqual(self.agent.step(), 1.1)
        self.assertEqual(self.agent.tick_errors, 1)
        self.assertEqual([type(e) for e in self.errors], [OSError])
        self.assertEqual(self.agent.step(), 0.1)
        self.assertEqual(self.agent.tick_errors, 1)

    def test_a_failing_error_report_does_not_stop_the_agent(self):
        def report(error):
            raise OSError("disk full")
        self.agent = self.make_agent(on_tick_error=report)
        self.params.onroad = mock.Mock(side_effect=OSError("settings unreadable"))
        self.assertEqual(self.agent.step(), 1.1)

    def test_a_tick_that_raises_outside_the_measures_makes_one_gap_sample(self):
        self.drive()
        # Tick 150 raises in the middle of reading the sockets; the agent then sleeps 1.1 s instead of 0.1 s.
        times = synth.ticks(151, 100, T0) + synth.ticks(149, 100, T0 + 16_100 * MS)
        for i, batch in enumerate(synth.drive(times)):
            self.feed(batch)
            if i == 150:
                self.socks["radarState"].receive = mock.Mock(side_effect=OSError("socket"))
            wait = self.agent.step()
            if i == 150:
                del self.socks["radarState"].receive
                self.assertEqual(wait, 1.1)
            self.mono.t = round(self.mono.t + wait, 6)
        self.end()
        (summary,) = self.summaries()
        self.assertEqual((summary["health"]["tickErrors"], summary["health"]["gapN"]), (1, 1))
        self.assertAlmostEqual(summary["unobservedS"], 1.2 - 0.25, delta=0.11)
        self.assertAlmostEqual(summary["tickMaxS"], 1.2, delta=0.11)
        self.assertEqual(self.agent.tick_errors, 1)
        self.assertEqual(len(self.errors), 1)

    def test_the_count_is_per_window(self):
        self.drive()
        batches = self.ticks(1300)
        self.play(batches[:100])
        self.params.onroad = mock.Mock(side_effect=[OSError("once"), OSError("twice")])
        self.agent.step()
        self.agent.step()
        del self.params.onroad
        self.play(batches[100:])
        self.assertEqual([s["health"]["tickErrors"] for s in self.summaries()], [2, 0])

    def test_a_failing_measure_is_not_a_tick_error(self):
        class Broken(WheelSwings):
            calls = 0

            def on_sample(self, ctx, g):
                type(self).calls += 1
                if type(self).calls >= 100:
                    raise ZeroDivisionError("a repeatable fault in one core")
                super().on_sample(ctx, g)

        def with_measures(measures):
            self.setUp()
            with mock.patch.object(measure_set, "MEASURES", measures):
                self.drive()
            self.play(road(1300, stop_at_s=70.0))
            self.end()
            return self.summaries(), self.uplink.data("drive_end")[0], self.uplink.data("brake")
        clean, clean_end, clean_brakes = with_measures(measure_set.MEASURES)
        swapped = tuple(Broken if m is WheelSwings else m for m in measure_set.MEASURES)
        faulty, faulty_end, faulty_brakes = with_measures(swapped)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.agent.tick_errors, 0)
        self.assertEqual([s["health"]["tickErrors"] for s in faulty], [0, 0, 0])
        self.assertEqual([s["health"].get("failed") for s in faulty], [["swings"]] * 3)
        self.assertEqual((faulty_end["failed"], clean_end["failed"]), (["swings"], []))
        self.assertTrue(all("swings" not in s for s in faulty))
        self.assertEqual([s["steeringS"] for s in faulty], [s["steeringS"] for s in clean])
        self.assertEqual(faulty_brakes, clean_brakes)
        self.assertEqual(len(clean_brakes), 1)
        self.assertEqual(faulty_end["totals"], clean_end["totals"])


class ApplyLockTests(AgentFixture):
    """As built, the agent looks at no approval and writes no setting."""

    def test_signed_approval_is_never_looked_at(self):
        # Parked with the ignition off, and an approval signed with the real key is waiting.
        self.uplink.incoming.append(self.approval())
        self.uplink.incoming.append(self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly"))
        for _ in range(3):
            self.agent.tick()
            self.mono.t += 2
        self.assertEqual(self.uplink.asked, 0)
        self.assertEqual(self.agent.pending, {})
        self.assertEqual(self.params.writes, [])
        self.assertEqual(self.uplink.types(), [])                    # no apply_result either
        self.assertFalse(self.applier.journal.is_open())

    def test_signed_approval_is_ignored_in_park_during_a_drive(self):
        self.drive()
        self.uplink.incoming.append(self.approval())
        self.play(self.ticks(5, change=parked))
        self.mono.t += 2
        self.agent.tick()
        self.assertEqual(self.params.writes, [])
        self.assertNotIn("apply_result", self.uplink.types())

    def test_the_trial_journal_is_not_checked_on_every_tick(self):
        self.applier.check_owner_override = mock.Mock(return_value=None)
        self.applier.journal.load = mock.Mock(return_value=None)
        self.agent.start()
        self.drive()
        self.play(self.ticks(300))
        self.end()
        self.assertEqual(self.applier.check_owner_override.call_count, 0)
        self.assertEqual(self.applier.journal.load.call_count, 2)     # restore at agent start and at drive end

    def test_stale_vehicle_state_is_not_parked(self):
        self.drive()
        self.play(self.ticks(1, change=parked))
        self.assertTrue(self.agent.vehicle_state().parked())
        self.mono.t += 5
        self.assertFalse(self.agent.vehicle_state().parked())


class PrivacyTests(AgentFixture):
    """Everything the agent sends, over drives that produce every message type, checked against the schema."""

    def everything(self):
        events = []

        def collect():
            events.extend(self.uplink.events)

        # A drive with a stop, a lane change, a limit event, a settings change and the learner's messages.
        self.wall.t = WALL
        self.agent.start()
        self.drive()
        batches = eventful_drive(900, stop_at_s=20.0, lane_change_at_s=30.0, limit_at_s=50.0)
        self.play(batches[:300])
        self.params.values["AutoLaneChangeTimer"] = 2
        self.play(batches[300:])
        self.end()
        collect()
        # A drive on which every block has something to say, ended by the memory limit; one setting unreadable.
        self.setUp()
        self.wall.t = WALL
        self.params.broken = {"GitCommit"}
        self.params.values["LiveDelay"] = None
        self.leave_trial_open()
        self.agent.start()
        self.drive()
        batches = road(1400, stop_at_s=70.0, live_delay=True)
        self.play(batches[:1250])
        self.rss_mb = 200.0
        self.play(batches[1250:])
        self.end()
        collect()
        # A drive that was never listened to, by an agent started in mid-drive after the owner changed a setting.
        self.setUp()
        self.wall.t = WALL
        self.leave_trial_open()
        self.params.values.update(LagdToggle=True, IsOnroad=True)
        self.slots_ok = False
        self.agent.start()
        self.agent.tick()
        self.end()
        collect()
        return events

    def test_every_message_holds_only_what_the_schema_allows(self):
        events = self.everything()
        self.assertEqual({e["type"] for e in events}, set(schema_v2.DATA))     # every message type was sent
        for event in events:
            with self.subTest(type=event["type"], id=event["id"]):
                self.assertEqual(schema_v2.problems(event), [])
        restores = {e["data"]["restore"] for e in events if e["type"] in ("agent_start", "drive_end")}
        self.assertEqual(restores, {"nothing_to_restore", "restored", "deferred_until_drive_end",
                                    "owner_changed:LagdToggle"})

    def test_no_number_could_be_a_clock_time_and_every_number_is_finite(self):
        def numbers(value):
            if isinstance(value, dict):
                for inner in value.values():
                    yield from numbers(inner)
            elif isinstance(value, list):
                for inner in value:
                    yield from numbers(inner)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                yield value
        for event in self.everything():
            for value in numbers(event):
                self.assertTrue(math.isfinite(value) and abs(value) < 1e9, (event["type"], value))
            json.dumps(event, allow_nan=False)

    def test_the_checker_sees_what_does_not_belong(self):
        self.drive()
        self.play(self.ticks(605))
        good = self.uplink.of("summary")[0]
        self.assertEqual(schema_v2.problems(good), [])

        def changed(**data):
            return dict(good, data={**good["data"], **data})
        for bad in (changed(route="home to work"), changed(startT=1_795_000_000.0), changed(vMean="fast"),
                    changed(w=float("nan")), changed(health={"note": "x"}), changed(lcStarts=1.5),
                    dict(good, driveId="drive on tuesday"), dict(good, id="1795000000"), dict(good, v=1),
                    dict(good, type="note"), dict(good, extra=1), changed(trimmed=["delay"])):
            self.assertNotEqual(schema_v2.problems(bad), [], bad)


if __name__ == "__main__":
    unittest.main()
