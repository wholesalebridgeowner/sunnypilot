"""Steering losses: each falling edge of steering-on while moving, labelled by the evidence around it."""
import unittest
from types import SimpleNamespace as ns

import synth
from device_params import blind_spot_enabled
from measure_steering_loss import CAUSES, SteeringLoss
from messages import drive_ready_data
from pipeline import Pipeline, PipelineConfig
from samples import event_payload
from schema_v2 import DATA
from synth import SEC

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
