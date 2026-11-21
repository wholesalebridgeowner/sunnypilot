"""The dormant suggestion and apply path, with its two locks patched open.

The path stays in the repository for a future build and cannot be reached on
this one (test_unreachable.py). So that it does not rot, its rules are still
tested here. Every test in this file runs with
`service_app.SUGGESTIONS_BUILT_IN` and `applier.APPLY_ENABLED` replaced by
True inside the test's own process, and that is the only way in: no
constructor argument, environment variable, file, request field or
command-line flag reads or sets either constant.
"""
import unittest
from unittest import mock

import test_agent
import test_applier
import test_end_to_end
import test_service
import test_uplink
from applier import Applier, VehicleState
from approval import verify_approval
from settings_guard import trial_values
from test_agent import BASE, LEARNED, LIVE

KEY = b"k" * 32
EFFECTIVE = {"result": "effective", "helperSha": "ba9876543210", "probeMs": 12}    # a build with the upstream fix


class LocksOpen:
    """Mix-in, listed before the fixture: opens both locks for the length of one test."""

    def setUp(self):
        for target in ("service_app.SUGGESTIONS_BUILT_IN", "applier.APPLY_ENABLED"):
            patcher = mock.patch(target, True)
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()


class DormantApplierTests(LocksOpen, test_applier.ApplierFixture):
    """Applier enforces every design rule before writing a setting."""

    def test_live_trial_applies_in_park_and_journals_previous(self):
        self.assertEqual(self.apply(self.approval()), "applied")
        self.assertIs(self.params.values["LagdToggle"], False)
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.1)
        self.assertEqual(self.applier.journal.load()["previous"], {"LagdToggle": True, "LagdToggleDelay": 0.2})

    def test_refused_unless_parked(self):
        for state in (VehicleState(True, 0.5, False), VehicleState(False, 0.0, False),
                      VehicleState(True, 0.0, True)):
            with self.subTest(state=state):
                self.assertEqual(self.apply(self.approval(), state=state), "refused:not_parked")
        self.assertEqual(self.params.writes, [])

    def test_bad_signature_changes_nothing(self):
        approval = self.approval()
        approval["sig"] = "0" * 64
        self.assertEqual(self.apply(approval), "refused:bad_signature")
        self.assertEqual(self.params.writes, [])

    def test_out_of_range_refused_even_when_signed(self):
        bad = {"LagdToggle": False, "LagdToggleDelay": 0.4}
        self.assertEqual(self.apply(self.approval(change=bad)), "refused:delay_too_far_from_learned")

    def test_same_approval_cannot_be_used_twice(self):
        approval = self.approval()
        self.assertEqual(self.apply(approval), "applied")
        self.applier.restore(onroad=False)
        self.assertEqual(self.apply(approval), "refused:already_used")

    def test_one_trial_at_a_time(self):
        self.apply(self.approval())
        self.assertEqual(self.apply(self.approval()), "refused:trial_already_active")

    def test_at_most_two_live_trials_per_drive(self):
        for _ in range(2):
            self.assertEqual(self.apply(self.approval()), "applied")
            self.applier.restore(onroad=False)
        self.assertEqual(self.apply(self.approval()), "refused:live_trial_limit")
        self.applier.new_drive()
        self.assertEqual(self.apply(self.approval()), "applied")

    def test_restore_puts_previous_values_back(self):
        self.apply(self.approval())
        self.assertEqual(self.applier.restore(onroad=False), "restored")
        self.assertIs(self.params.values["LagdToggle"], True)
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.assertFalse(self.applier.journal.is_open())

    def test_restore_waits_while_driving(self):
        self.apply(self.approval())
        self.assertEqual(self.applier.restore(onroad=True), "deferred_until_drive_end")
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.1)

    def test_owner_change_wins(self):
        self.apply(self.approval())
        self.params.values["LagdToggle"] = True
        self.assertEqual(self.applier.check_owner_override(), "trial_ended_owner_changed")
        self.assertEqual(self.applier.restore(onroad=False), "nothing_to_restore")
        self.assertIs(self.params.values["LagdToggle"], True)

    def test_restore_after_agent_restart(self):
        self.apply(self.approval())
        fresh = Applier(self.params, type(self.applier.journal)(self.applier.journal.path),
                        type(self.applier.nonces)(self.applier.nonces.path), KEY)
        self.assertEqual(fresh.restore(onroad=False), "restored")
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)

    def test_nightly_change_is_kept_not_journaled(self):
        approval = self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly")
        self.assertEqual(self.apply(approval), "applied")
        self.assertIs(self.params.values["NeuralNetworkLateralControl"], True)
        self.assertFalse(self.applier.journal.is_open())


class BuildGateTests(LocksOpen, test_applier.ApplierFixture):
    """Lock 3: a change of the delay settings is refused unless the delay probe says a fixed delay steers."""

    GATED = "refused:delay_inert_on_this_build"

    def nothing_happened(self):
        self.assertEqual(self.params.writes, [])
        self.assertFalse(self.applier.journal.is_open())
        self.assertEqual(self.applier.nonces.used(), set())            # the approval is not used up

    def test_an_approved_delay_change_is_refused_on_this_build(self):
        self.assertEqual(self.apply(self.approval(), probe="inert"), self.GATED)
        self.nothing_happened()

    def test_only_effective_opens_the_gate(self):
        for probe in ("inert", "unknown", "not_run", None, "", "Effective", True, {"result": "effective"}):
            with self.subTest(probe=probe):
                self.assertEqual(self.apply(self.approval(), probe=probe), self.GATED)
        self.nothing_happened()
        self.assertEqual(self.apply(self.approval(), probe="effective"), "applied")

    def test_without_a_probe_result_the_gate_is_closed(self):
        result = self.applier.apply(self.approval(), test_applier.PARKED, now=1010.0, learned_delay_s=0.25,
                                    base_actuator_delay_s=0.1)
        self.assertEqual(result, self.GATED)
        self.nothing_happened()

    def test_any_change_that_names_a_delay_setting_is_gated(self):
        for change in ({"LagdToggle": True}, {"LagdToggle": False, "LagdToggleDelay": 0.1},
                       {"LagdToggleDelay": 0.1}, {"NeuralNetworkLateralControl": True, "LagdToggle": True}):
            with self.subTest(change=change):
                self.assertEqual(self.apply(self.approval(change=change, mode="nightly"), probe="inert"), self.GATED)
        self.nothing_happened()

    def test_a_change_without_a_delay_setting_is_not_gated(self):
        approval = self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly")
        self.assertEqual(self.apply(approval, probe="inert"), "applied")
        self.assertIs(self.params.values["NeuralNetworkLateralControl"], True)

    def test_the_gate_comes_after_the_approval_check_and_before_the_guard(self):
        forged = self.approval()
        forged["sig"] = "0" * 64
        self.assertEqual(self.apply(forged, probe="inert"), "refused:bad_signature")
        out_of_range = self.approval(change={"LagdToggle": False, "LagdToggleDelay": 0.4})
        self.assertEqual(self.apply(out_of_range, probe="inert"), self.GATED)
        self.assertEqual(self.apply(out_of_range, probe="effective"), "refused:delay_too_far_from_learned")
        moving = VehicleState(in_park=False, speed_ms=20.0, engaged=True)
        self.assertEqual(self.apply(self.approval(), state=moving, probe="inert"), self.GATED)
        self.nothing_happened()


class DormantAgentTests(LocksOpen, test_agent.AgentFixture):
    """The agent's side of the apply path, as it would run on a build where a fixed delay steers."""

    def setUp(self):
        super().setUp()
        self.probe_result = dict(EFFECTIVE)       # what the probe finds at the start of a drive
        self.agent.probe = dict(EFFECTIVE)        # ... and found on an earlier drive of this boot

    def moving(self, i=0, t_ns=0):
        return {"carState": dict(v=20.0)}

    def test_start_restores_open_trial_when_offroad(self):
        self.assertEqual(self.applier.apply(self.approval(), self.agent.vehicle_state(), now=self.wall.t,
                                            learned_delay_s=LEARNED, base_actuator_delay_s=BASE,
                                            delay_probe="effective"), "applied")
        fresh = self.make_agent(applier=Applier(self.params, self.applier.journal, self.applier.nonces, KEY))
        fresh.start()
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.assertEqual(self.uplink.events[-1]["data"], {"restore": "restored"})

    def test_approval_waits_for_park_then_applies(self):
        self.drive()
        self.play(self.ticks(1, change=self.moving))
        self.uplink.incoming.append(self.approval())
        self.play(self.ticks(1, change=self.moving))
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.mono.t += 2
        self.play(self.ticks(1, change=test_agent.parked))
        self.assertEqual(self.params.values["LagdToggleDelay"], LIVE["LagdToggleDelay"])
        self.assertIs(self.params.values["LagdToggle"], False)
        self.assertEqual(self.uplink.events[-1]["type"], "apply_result")
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "applied")

    def test_the_learned_delay_comes_from_the_learner_not_from_the_cache(self):
        # The cache holds a stale total. Were it taken as the learned delay, the approved trial would be refused
        # as too far from it; with the learner's own value the trial is one step away, as approved.
        self.params.values["LagdValueCache"] = 0.55
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "applied")

    def test_without_a_trusted_learned_delay_a_delay_change_is_refused(self):
        self.params.values["LiveDelay"] = test_agent.stored_delay(LEARNED, status="unestimated")
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "refused:invalid_delay_inputs")
        self.assertEqual(self.params.writes, [])

    def test_unparked_approval_dropped_at_expiry(self):
        self.drive()
        self.play(self.ticks(1, change=self.moving))
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.wall.t += 601
        self.mono.t += 2
        self.play(self.ticks(1, change=self.moving))
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "refused:expired")
        self.assertEqual(self.agent.pending, {})

    def test_offroad_counts_as_parked_for_nightly(self):
        self.uplink.incoming.append(self.approval(change={"NeuralNetworkLateralControl": True}, mode="nightly"))
        self.agent.tick()
        self.assertIs(self.params.values["NeuralNetworkLateralControl"], True)

    def test_owner_override_ends_trial(self):
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.assertIs(self.params.values["LagdToggle"], False)
        self.params.values["LagdToggle"] = True
        self.agent.tick()
        self.assertIn("trial_ended", self.uplink.types())

    def test_drive_end_restores_trial(self):
        self.uplink.incoming.append(self.approval())
        self.agent.tick()
        self.drive()
        self.assertIs(self.uplink.data("drive_start")[0]["settings"]["lagdToggle"], False)
        self.end()
        self.assertEqual(self.params.values["LagdToggleDelay"], 0.2)
        self.assertEqual(self.uplink.events[-1]["type"], "drive_end")
        self.assertEqual(self.uplink.events[-1]["data"]["restore"], "restored")

    def test_an_approved_delay_change_is_refused_while_the_probe_reads_inert(self):
        for probe in ({"result": "inert", "helperSha": "0123456789ab", "probeMs": 3}, None):
            with self.subTest(probe=probe):
                self.agent.probe = probe
                self.uplink.incoming.append(self.approval())
                self.agent.tick()
                self.mono.t += 2
                self.assertEqual(self.uplink.events[-1]["data"]["status"], "refused:delay_inert_on_this_build")
        self.assertEqual(self.params.writes, [])
        self.assertFalse(self.applier.journal.is_open())

    def test_the_gate_reads_the_probe_of_the_newest_drive(self):
        self.probe_result = {"result": "inert", "helperSha": "0123456789ab", "probeMs": 3}
        self.drive()                               # this drive's probe replaces what an earlier drive found
        self.uplink.incoming.append(self.approval())
        self.play(self.ticks(1, change=test_agent.parked))
        self.assertEqual(self.uplink.events[-1]["data"]["status"], "refused:delay_inert_on_this_build")


class DormantUplinkTests(LocksOpen, test_uplink.UplinkFixture):
    def test_approvals_collected_once(self):
        self.server.approvals = [{"nonce": "n" * 16}]
        self.uplink.run_once()
        self.assertEqual(self.uplink.approvals(), [{"nonce": "n" * 16}])
        self.assertEqual(self.uplink.approvals(), [])

    def test_requests_use_expected_paths(self):
        self.uplink.send({"id": "a"})
        self.uplink.run_once()
        self.assertEqual(self.server.paths, ["/v1/events", "/v1/approvals"])


class DormantServiceTests(LocksOpen, test_service.ServiceFixture):
    def test_weaving_creates_one_live_suggestion_with_sound(self):
        self.start()
        self.weave_minutes(7)
        suggestions = [n for n in self.notifier.sent if n["actions"]]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["priority"], 4)
        self.assertEqual([label for label, _ in suggestions[0]["actions"]], ["Approve", "Decline"])
        self.assertIn("Park", suggestions[0]["message"])

    def test_approve_signs_the_guarded_change_once(self):
        self.start()
        self.weave_minutes(5)
        token = self.token("Approve")
        self.assertEqual(self.service.approve(token)[0], 200)
        approvals = self.service.approvals_for_comma()
        self.assertEqual(len(approvals), 1)
        fields = verify_approval(KEY, approvals[0], now=self.clock.t, used_nonces=set())
        self.assertEqual(fields["change"], trial_values(0.25, 0.1, -1))
        self.assertEqual(fields["mode"], "live")
        self.assertEqual(self.service.approve(token)[0], 409)

    def test_decline_blocks_the_same_reason_for_this_drive(self):
        self.start()
        self.weave_minutes(5)
        self.assertEqual(self.service.decline(self.token("Decline"))[0], 200)
        self.weave_minutes(10)
        self.assertEqual(len([n for n in self.notifier.sent if n["actions"]]), 1)
        self.assertEqual(self.service.approvals_for_comma(), [])

    def test_live_suggestions_can_be_switched_off(self):
        self.service.live_enabled = False
        self.start()
        self.weave_minutes(7)
        self.assertEqual([n for n in self.notifier.sent if n["actions"]], [])

    def test_no_live_suggestion_without_learned_delay(self):
        self.start(learned=None)
        self.weave_minutes(7)
        self.assertEqual([n for n in self.notifier.sent if n["actions"]], [])

    def test_apply_result_records_trial_and_confirms(self):
        self.start()
        self.weave_minutes(5)
        self.service.approve(self.token("Approve"))
        sid = self.service.approvals_for_comma()[0]["suggestion_id"]
        self.event("apply_result", data={"suggestionId": sid, "status": "applied"})
        self.assertEqual(self.service.approvals_for_comma(), [])
        self.assertIn("Applied", self.notifier.sent[-1]["title"])
        self.assertEqual(self.service.store.get_json("drives/d1.json")["trial"]["reason"], "weaving_on_straight_road")

    def test_approvals_expire_for_the_comma(self):
        self.start()
        self.weave_minutes(5)
        self.service.approve(self.token("Approve"))
        self.clock.t += 601
        self.assertEqual(self.service.approvals_for_comma(), [])

    def test_nightly_report_an_hour_after_last_drive(self):
        self.start()
        self.event("summary", data=test_service.minute(steerWarningEpisodes=1))
        for _ in range(9):
            self.event("summary", data=test_service.minute())
        self.event("drive_end")
        self.clock.t += 3700
        self.service.run_nightly()
        report = self.notifier.sent[-1]
        self.assertIn("Nightly", report["title"])
        self.assertEqual([label for label, _ in report["actions"]], ["Approve", "Decline"])
        self.service.approve(self.token("Approve"))
        fields = verify_approval(KEY, self.service.approvals_for_comma()[0], now=self.clock.t + 11 * 3600,
                                 used_nonces=set())
        self.assertEqual(fields["change"], {"NeuralNetworkLateralControl": True})

    def test_nightly_report_carries_the_braking_advice(self):
        self.start()
        stop = {"leadPresent": True, "leadDistanceM": 9.0, "speedMs": 20.0}
        for _ in range(3):
            self.event("summary", data=test_service.minute(hardBrakes=[stop]))
        self.event("drive_end")
        self.clock.t += 3700
        self.service.run_nightly()
        self.assertIn("following-distance", self.notifier.sent[-1]["message"])

    def test_nightly_suggestions_can_be_switched_off(self):
        self.service.nightly_suggestions = False
        self.start()
        self.event("summary", data=test_service.minute(steerWarningEpisodes=1))
        for _ in range(9):
            self.event("summary", data=test_service.minute())
        self.event("drive_end")
        self.clock.t += 3700
        self.service.run_nightly()
        report = self.notifier.sent[-1]
        self.assertIn("Nightly", report["title"])
        self.assertEqual(report["actions"], [])
        self.assertIn("No change suggested", report["message"])
        self.assertEqual(self.service.approvals_for_comma(), [])


class DormantEndToEndTests(LocksOpen, unittest.TestCase):
    def test_approved_trial_to_parked_apply_and_restore(self):
        loop = test_end_to_end.Loop(self)
        loop.probe = dict(EFFECTIVE)               # a build on which a fixed delay steers
        loop.weaving_drive(ticks=700)
        drive_id = loop.stored("drives/")[0]["driveId"]
        expected = trial_values(LEARNED, BASE, -1)                   # from a full-precision learned delay
        record = loop.service._create_suggestion(mode="live", change=expected, reason="weaving_on_straight_road",
                                                 drive_id=drive_id)
        self.assertEqual(loop.service.approve(record["approveToken"])[0], 200)

        loop.uplink.run_once()           # agent receives the approval while still driving
        loop.play(1, test_end_to_end.weaving)
        self.assertIs(loop.params.values["LagdToggle"], True)        # not applied while moving

        loop.park()
        self.assertIs(loop.params.values["LagdToggle"], False)
        self.assertEqual(loop.params.values["LagdToggleDelay"], expected["LagdToggleDelay"])
        loop.uplink.run_once()
        self.assertEqual(loop.notifier.sent[-1]["title"], "Ram advisor: Applied")

        loop.end_drive()
        self.assertIs(loop.params.values["LagdToggle"], True)
        self.assertEqual(loop.params.values["LagdToggleDelay"], 0.2)
        drives = loop.stored("drives/")
        self.assertEqual(len(drives), 1)
        self.assertTrue(drives[0]["ended"])
        self.assertEqual(drives[0]["trial"]["reason"], "weaving_on_straight_road")
        self.assertEqual(drives[0]["restore"], "restored")
        self.assertIn(("GET", "/v1/approvals"), loop.requests)

    def test_the_same_trial_is_refused_while_the_probe_reads_inert(self):
        loop = test_end_to_end.Loop(self)            # the probe of this build: inert
        loop.weaving_drive(ticks=700)
        record = loop.service._create_suggestion(mode="live", change=trial_values(LEARNED, BASE, -1),
                                                 reason="weaving_on_straight_road",
                                                 drive_id=loop.stored("drives/")[0]["driveId"])
        self.assertEqual(loop.service.approve(record["approveToken"])[0], 200)
        loop.uplink.run_once()
        loop.park()
        loop.uplink.run_once()
        self.assertEqual(loop.params.writes, [])
        self.assertFalse(loop.applier.journal.is_open())
        result = [event for event in loop.sent if event["type"] == "apply_result"]
        self.assertEqual([event["data"]["status"] for event in result], ["refused:delay_inert_on_this_build"])
        self.assertEqual(loop.notifier.sent[-1]["title"], "Ram advisor: Not applied")


if __name__ == "__main__":
    unittest.main()
