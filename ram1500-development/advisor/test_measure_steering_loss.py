"""Steering losses: each falling edge of steering-on while moving, labelled by the evidence around it."""
import unittest
from types import SimpleNamespace as ns

import synth
from device_params import blind_spot_enabled
from measure_steering_loss import CAUSES, SteeringLoss
from messages import drive_ready_data
from pipeline import Pipeline, PipelineConfig
from replay_report import replay_messages
from samples import SKIPPED_BUTTONS, event_payload
from schema_v2 import DATA, problems
from synth import MS, SEC

T0 = 1_000 * SEC


def run(extra, n=200, v=25.0):
    """steering_loss records of a 20 s synthetic drive at 10 Hz. `extra(t_s)` gives the stream changes."""
    def change(i, t_ns):
        t_s = (t_ns - T0) / SEC
        out = dict(extra(t_s) or {})
        out["carState"] = dict(out.get("carState") or {}, v=v)
        return out
    p = Pipeline(PipelineConfig(measures=(SteeringLoss,)))
    messages = synth.run(p, synth.drive(synth.ticks(n, 100.0, T0), change))
    return [data for kind, data in messages if kind == "steering_loss"]


def lat_until(t_off, **car):
    """Steering on until `t_off` s, off after; carState extras from `car(t_s)` keyword functions."""
    def extra(t_s):
        cs = {key: f(t_s) for key, f in car.items()}
        return {"carState": cs, "carControl": {"lat": t_s < t_off}}
    return extra


class TestSteeringLoss(unittest.TestCase):
    def test_brake_press_is_the_cause(self):
        recs = run(lat_until(10.0, brake=lambda t: t >= 9.95, cruise=lambda t: t < 10.1))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["cause"], "brake")
        self.assertIn("cruise_off", recs[0]["evidence"])
        self.assertFalse(recs[0]["windowTruncated"])

    def test_gas_when_no_brake(self):
        recs = run(lat_until(10.0, gas=lambda t: 9.8 <= t < 10.5, cruise=lambda t: True))
        self.assertEqual([r["cause"] for r in recs], ["gas"])

    def test_main_switch_off_is_the_cause_ahead_of_gas(self):
        # MADS steering-only with the gas held: cruise availability falls on the steering edge.
        recs = run(lat_until(10.0, gas=lambda t: True, available=lambda t: t < 10.0))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["cause"], "main_off")
        self.assertEqual(recs[0]["evidence"], ["gas", "main_off"])

    def test_brake_still_comes_before_main_off(self):
        recs = run(lat_until(10.0, brake=lambda t: t >= 9.95, gas=lambda t: True, available=lambda t: t < 10.0))
        self.assertEqual(recs[0]["cause"], "brake")
        self.assertEqual(set(recs[0]["evidence"]), {"brake", "gas", "main_off"})

    def test_main_off_with_cruise_engaged_names_the_switch(self):
        recs = run(lat_until(10.0, cruise=lambda t: t < 10.0, available=lambda t: t < 10.0))
        self.assertEqual(recs[0]["cause"], "main_off")
        self.assertEqual(set(recs[0]["evidence"]), {"main_off", "cruise_off"})

    def test_a_fall_inside_the_after_window_counts(self):
        recs = run(lat_until(10.0, gas=lambda t: True, available=lambda t: t < 10.2))
        self.assertEqual(recs[0]["cause"], "main_off")

    def test_a_fall_outside_the_window_is_ignored(self):
        for last_on in (9.5, 10.5):            # availability fell 0.5 s before or 0.5 s after the edge
            with self.subTest(last_on=last_on):
                recs = run(lat_until(10.0, gas=lambda t: True, available=lambda t: t < last_on))
                self.assertEqual(recs[0]["cause"], "gas")
                self.assertNotIn("main_off", recs[0]["evidence"])

    def test_no_fall_without_an_observed_on_then_off(self):
        cases = {"never on": lambda t: False,
                 "on then unknown": lambda t: True if t < 10.0 else None,
                 "unknown between on and off": lambda t: True if t < 9.9 else (None if t < 10.0 else False),
                 "coming back on": lambda t: t >= 10.0}
        for name, available in cases.items():
            with self.subTest(name):
                recs = run(lat_until(10.0, gas=lambda t: True, available=available))
                self.assertEqual(recs[0]["cause"], "gas")

    def test_a_reading_without_valid_can_data_is_not_used(self):
        recs = run(lat_until(10.0, gas=lambda t: True, available=lambda t: t < 10.0,
                             can_valid=lambda t: t < 10.0))
        self.assertEqual(recs[0]["cause"], "gas")

    def test_a_fall_across_a_data_gap_is_not_used(self):
        def drive(t):
            if 9.5 <= t < 9.9:
                return {"skip": ("carState",)}            # 0.5 s without carState: a gap Sample at 9.9 s
            return {"carControl": {"lat": t < 10.0}, "carState": {"gas": True, "available": t < 9.9}}
        recs = run(drive)
        self.assertEqual([r["cause"] for r in recs], ["gas"])

    @staticmethod
    def press_at(t_press, kind="lkas", down=True):
        return lambda t: ((kind, down),) if abs(t - t_press) < 0.05 else None

    def test_lkas_button_is_the_cause_ahead_of_gas(self):
        recs = run(lat_until(10.0, buttons=self.press_at(10.0)))
        self.assertEqual([(r["cause"], r["evidence"]) for r in recs], [("lkas_button", ["lkas_button"])])
        recs = run(lat_until(10.0, gas=lambda t: True, buttons=self.press_at(9.9)))
        self.assertEqual([(r["cause"], r["evidence"]) for r in recs], [("lkas_button", ["gas", "lkas_button"])])

    def test_a_release_alone_counts(self):
        recs = run(lat_until(10.0, buttons=self.press_at(10.2, down=False)))
        self.assertEqual(recs[0]["cause"], "lkas_button")

    def test_brake_and_main_off_come_before_lkas_button(self):
        recs = run(lat_until(10.0, brake=lambda t: t >= 9.95, buttons=self.press_at(10.0)))
        self.assertEqual((recs[0]["cause"], recs[0]["evidence"]), ("brake", ["brake", "lkas_button"]))
        recs = run(lat_until(10.0, available=lambda t: t < 10.0, buttons=self.press_at(10.0)))
        self.assertEqual((recs[0]["cause"], recs[0]["evidence"]), ("main_off", ["lkas_button", "main_off"]))

    def test_a_press_outside_the_window_is_ignored(self):
        for t_press in (9.5, 10.5):
            with self.subTest(t_press=t_press):
                self.assertEqual(run(lat_until(10.0, buttons=self.press_at(t_press)))[0]["cause"], "none")

    def test_other_buttons_and_invalid_can_data_do_not_count(self):
        for kind in ("cancel", "mainCruise", "accelCruise", "setCruise"):
            with self.subTest(kind=kind):
                self.assertEqual(run(lat_until(10.0, buttons=self.press_at(10.0, kind)))[0]["cause"], "none")
        recs = run(lat_until(10.0, buttons=self.press_at(10.0), can_valid=lambda t: abs(t - 10.0) >= 0.05))
        self.assertEqual(recs[0]["cause"], "none")

    def test_a_press_from_a_carstate_the_live_tick_skipped_counts(self):
        # The agent passes the button types of the carState messages its tick skipped under SKIPPED_BUTTONS.
        batches = synth.drive(synth.ticks(200, 100.0, T0), lambda i, t: {"carControl": {"lat": i < 100}})
        batches[100][SKIPPED_BUTTONS] = (batches[100]["carState"][0], frozenset({"lkas"}))
        p = Pipeline(PipelineConfig(measures=(SteeringLoss,)))
        recs = [data for kind, data in synth.run(p, batches) if kind == "steering_loss"]
        self.assertEqual([(r["cause"], r["evidence"]) for r in recs], [("lkas_button", ["lkas_button"])])

    def test_cruise_off_only_counts_when_cruise_was_on(self):
        recs = run(lat_until(10.0, cruise=lambda t: False))  # MADS steering without cruise: no cruise_off
        self.assertEqual([r["cause"] for r in recs], ["none"])

    def test_gear_fault_blinker_order(self):
        recs = run(lat_until(10.0, gear=lambda t: "drive" if t < 10.0 else "park", fault=lambda t: t >= 10.0))
        self.assertEqual(recs[0]["cause"], "gear")
        self.assertEqual(set(recs[0]["evidence"]), {"gear", "fault"})
        recs = run(lat_until(10.0, left=lambda t: 9.0 <= t < 11.0))
        self.assertEqual(recs[0]["cause"], "blinker")

    def test_evidence_outside_window_is_ignored(self):
        recs = run(lat_until(10.0, brake=lambda t: 9.0 <= t < 9.5))   # brake ended 0.5 s before the edge
        self.assertEqual([r["cause"] for r in recs], ["none"])

    def test_slow_losses_are_not_counted(self):
        self.assertEqual(run(lat_until(10.0, brake=lambda t: t >= 9.95), v=4.0), [])

    def test_steering_staying_on_gives_nothing(self):
        self.assertEqual(run(lambda t_s: {"carControl": {"lat": True}}), [])

    def test_control_outage_does_not_create_an_edge_and_tracking_resumes(self):
        for resume_active in (False, True):
            with self.subTest(resume_active=resume_active):
                def drive(t):
                    if 5 <= t < 6:
                        return {"skip": ("carControl",)}
                    active = t < 5 or (t < 9 and resume_active) or 9 <= t < 10
                    return {"carControl": {"lat": active}, "carState": {"brake": t >= 10}}
                self.assertEqual([r["cause"] for r in run(drive)], ["brake"])

    def test_gap_in_control_timestamps_can_end_between_two_car_state_ticks(self):
        # At 5.5 s the 5.0 s control is still fresh. At 5.6 s a new False arrives.
        def drive(t):
            if 5 < t < 5.6:
                return {"skip": ("carControl",)}
            return {"carControl": {"lat": t <= 5}}
        self.assertEqual(run(drive), [])

    def test_invalid_control_delivered_on_an_intervening_tick_breaks_the_edge(self):
        p = Pipeline(PipelineConfig(measures=(SteeringLoss,)))
        batches = synth.drive(synth.ticks(25), lambda i, t: {"carControl": {"lat": i < 10}})
        invalid = ns(logMonoTime=T0 + 950_000_000, valid=False, carControl=synth.car_control(lat=False))
        batches.insert(10, {"carControl": (invalid.logMonoTime, event_payload(invalid, "carControl"))})
        self.assertEqual([d for k, d in synth.run(p, batches) if k == "steering_loss"], [])

    def test_missing_control_after_an_observed_fall_preserves_brake_evidence(self):
        def drive(t):
            return {"carControl": {"lat": t < 5}, "skip": ("carControl",) if t > 5 else (),
                    "carState": {"brake": 5.1 <= t <= 5.2}}
        self.assertEqual([r["cause"] for r in run(drive)], ["brake"])

    def test_saturation_warning_is_never_a_loss(self):
        recs = run(lambda t_s: {"carControl": {"lat": True},
                                "alerts": ["steerSaturated"] if 5.0 <= t_s < 7.0 else []})
        self.assertEqual(recs, [])

    def test_loss_near_drive_end_is_sent_truncated(self):
        recs = run(lat_until(19.85, brake=lambda t: t >= 19.8))
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["windowTruncated"])
        self.assertEqual(recs[0]["cause"], "brake")

    def test_missing_control_is_unknown_not_a_loss(self):
        # Review of 3098b5f (Codex, 2026-11-21): one second without carControl made a false loss.
        recs = run(lambda t_s: {"skip": ("carControl",)} if 8.0 <= t_s < 9.0 else {"carControl": {"lat": True}})
        self.assertEqual(recs, [])

    def test_after_unknown_an_observed_true_is_needed_before_a_loss(self):
        def extra(t_s):
            if 8.0 <= t_s < 9.0:
                return {"skip": ("carControl",)}
            if 9.0 <= t_s < 12.0:
                return {"carControl": {"lat": True}}
            return {"carControl": {"lat": t_s < 8.0}, "carState": {"brake": t_s >= 12.0}}
        recs = run(extra)
        self.assertEqual([r["cause"] for r in recs], ["brake"])         # only the observed True -> False at 12 s

    def test_record_matches_schema(self):
        rule = DATA["steering_loss"]
        self.assertEqual(set(rule), {"cause", "evidence", "vMs", "windowTruncated"})
        self.assertEqual(set(rule["cause"][1]), set(CAUSES))


class TestRoute401MainSwitch(unittest.TestCase):
    """Replay-shaped copy of the route 401, segment 13 loss (decoded rlog, 2026-11-25). carState and carControl
    at 100 Hz, carState stamped 5.3 ms before each carControl. 21.5 m/s, gas down throughout, brake up, cruise
    not engaged (MADS steering only). cruiseState.available fell 5.3 ms before latActive went off; it came back
    at +1474.6 ms and latActive at about +1480 ms. Times are relative; the signal values are the log's."""

    EDGE = T0 + 20 * SEC                       # carControl latActive goes False here

    def events(self):
        out = []
        for k in range(-1000, 2000):          # -10 s .. +20 s around the edge
            t_control = self.EDGE + k * 10 * MS
            out.append(("carControl", t_control, synth.car_control(lat=not 0 <= k < 148)))
            t_state = t_control - 5_300_000
            available = not -5_300_000 <= t_state - self.EDGE < 1_474_600_000
            out.append(("carState", t_state, synth.car_state(v=21.5, gas=True, available=available)))
        return out

    EXPECTED = {"cause": "main_off", "evidence": ["gas", "main_off"], "vMs": 21.5, "windowTruncated": False}

    def test_replay_path_names_the_main_switch(self):
        out = [m for m in replay_messages(self.events()) if m["type"] == "steering_loss"]
        self.assertEqual([m["data"] for m in out], [self.EXPECTED])
        self.assertEqual(problems(out[0]), [])

    def test_live_agent_path_names_the_main_switch(self):
        # The agent drains at 10 Hz and keeps the newest message of each stream per tick.
        ticks = synth.ticks(300, 100.0, self.EDGE - 10 * SEC + 7 * MS)
        batches = synth.tick_batches([(t, stream, msg) for stream, t, msg in self.events()], ticks)
        p = Pipeline(PipelineConfig(measures=(SteeringLoss,)))
        recs = [data for kind, data in synth.run(p, batches) if kind == "steering_loss"]
        self.assertEqual(recs, [self.EXPECTED])


def lkas_case(edge_ns, *, v, press_ms, release_ms, steer_pressed_ms=(), cruise=True, cs_offset_ms=-6.3,
              span_s=(-10, 20)):
    """carState and carControl at 100 Hz around a steering edge at `edge_ns` (carControl latActive goes False
    there and stays off). carState is stamped `cs_offset_ms` from each carControl. The LKAS press and release
    are each in the one carState nearest their time; `steer_pressed_ms` are (from, to) intervals of hand steering.
    Returns (stream, t_ns, message) events, the shape a log decoder hands the replay tool."""
    out = []
    first = int(round(cs_offset_ms * MS))
    for k in range(span_s[0] * 100, span_s[1] * 100):
        t_control = edge_ns + k * 10 * MS
        out.append(("carControl", t_control, synth.car_control(lat=k < 0)))
        rel_ms = (k * 10 * MS + first) / MS
        buttons = None
        if abs(rel_ms - press_ms) < 5:
            buttons = (("lkas", True),)
        elif abs(rel_ms - release_ms) < 5:
            buttons = (("lkas", False),)
        pressed = any(a <= rel_ms < b for a, b in steer_pressed_ms)
        out.append(("carState", t_control + first, synth.car_state(v=v, cruise=cruise, available=True,
                                                                     pressed=pressed, buttons=buttons)))
    return out


# Route 402 (2026-11-25), decoded rlog, times relative to latActive going False. Segment 12: 20.07 m/s, cruise
# on, availability on throughout, no pedal; LKAS press in the carState at -6.3 ms, release at +73.8 ms; hand
# steering blips end about 1 s before.
ROUTE402_SEG12 = dict(v=20.07, press_ms=-6.3, release_ms=73.8,
                      steer_pressed_ms=((-2267.0, -2196.1), (-2066.7, -1755.2), (-1276.7, -996.1)))
ROUTE402_SEG12_RECORD = {"cause": "lkas_button", "evidence": ["lkas_button"], "vMs": 20.1, "windowTruncated": False}
# Segment 63: 22.81 m/s, cruise on, availability on, no pedal; LKAS press in the carState at -5.3 ms, release at
# +144.4 ms; hand steering on and off in the 3 s before, the last touch 30 ms long ending 275 ms before the edge
# (shorter than the driver gate's minimum, so no driver evidence; the real replay agrees).
ROUTE402_SEG63 = dict(v=22.81, press_ms=-5.3, release_ms=144.4, cs_offset_ms=-5.3,
                      steer_pressed_ms=((-3000.0, -2186.0), (-2175.0, -2146.2), (-2135.0, -2095.5),
                                        (-1845.6, -1386.4), (-1355.8, -1315.5), (-1295.0, -1205.8),
                                        (-1194.3, -1154.6), (-1115.0, -1104.3), (-1065.4, -1026.3),
                                        (-1014.7, -994.8), (-305.3, -275.1)))
ROUTE402_SEG63_RECORD = {"cause": "lkas_button", "evidence": ["lkas_button"], "vMs": 22.8, "windowTruncated": False}


class TestRoute402LkasButton(unittest.TestCase):
    """The two route 402 losses the advisor sent as cause none, evidence []. Replay path here; the live agent
    path (a tick that skips the carState with the press) is in test_agent.py."""

    def test_segment_12_and_63_replay(self):
        for case, record in ((ROUTE402_SEG12, ROUTE402_SEG12_RECORD), (ROUTE402_SEG63, ROUTE402_SEG63_RECORD)):
            with self.subTest(vMs=record["vMs"]):
                out = [m for m in replay_messages(lkas_case(T0 + 20 * SEC, **case)) if m["type"] == "steering_loss"]
                self.assertEqual([m["data"] for m in out], [record])
                self.assertEqual(problems(out[0]), [])


class TestBlindSpotFlag(unittest.TestCase):
    class Params:
        def __init__(self, store):
            self.store = store

        def get(self, key):
            return self.store.get(key)

    def test_reads_current_car_params_only(self):
        decode = lambda raw: ns(enableBsm=raw == b"on")
        self.assertIs(blind_spot_enabled(self.Params({"CarParams": b"on"}), decode), True)
        self.assertIs(blind_spot_enabled(self.Params({"CarParams": b"off"}), decode), False)
        # the persistent copy can be from an earlier drive: never used
        self.assertIsNone(blind_spot_enabled(self.Params({"CarParamsPersistent": b"on"}), decode))

    def test_unreadable_gives_none(self):
        def broken(raw):
            raise ValueError("bad")
        self.assertIsNone(blind_spot_enabled(self.Params({"CarParams": b"x"}), broken))
        self.assertIsNone(blind_spot_enabled(self.Params({"CarParams": b"x"}), lambda raw: ns(enableBsm=1)))

    def test_drive_ready_carries_it(self):
        data = drive_ready_data(probe={}, nnlc_model="ram", nnlc_fuzzy=False, blind_spot=False)
        self.assertIs(data["blindSpot"], False)
        self.assertIn("blindSpot", DATA["drive_ready"])


if __name__ == "__main__":
    unittest.main()
