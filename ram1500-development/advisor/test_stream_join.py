"""StreamJoiner: one Sample per new carState, joined with the newest fresh message of each other stream."""
import unittest
from types import SimpleNamespace as ns

from samples import ModelMsg, Sample, StreamJoiner, event_payload

MS = 1_000_000
SEC = 1_000_000_000
T0 = 5_000 * SEC


def car_state(**over):
    fields = dict(vEgo=20.0, steeringAngleDeg=1.5, steeringTorque=12.0, steeringTorqueEps=-30.0,
                  steeringPressed=False, brakePressed=False, cruiseState=ns(enabled=True),
                  gearShifter="drive", leftBlinker=False, rightBlinker=False)
    fields.update(over)
    return ns(**fields)


def model(state="off", direction="none", left_y=-1.8, right_y=1.7, left_prob=0.95, right_prob=0.9):
    return ns(meta=ns(laneChangeState=state, laneChangeDirection=direction),
              laneLineMeta=ns(leftY=left_y, rightY=right_y, leftProb=left_prob, rightProb=right_prob))


class SampleFieldsTests(unittest.TestCase):
    def test_original_curvature_stamp_requires_valid_can_and_envelopes(self):
        for car_ok, curvature_ok, can_ok in ((True, True, True), (True, True, None), (True, True, "yes"),
                                            (True, True, False), (False, True, True), (True, False, True)):
            with self.subTest(car=car_ok, controls=curvature_ok, can=can_ok):
                join = StreamJoiner()
                cur = ns(valid=curvature_ok, controlsState=ns(curvature=.01))
                car = ns(valid=car_ok, carState=car_state(canValid=can_ok))
                join.offer("controlsState", T0, event_payload(cur, "controlsState"))
                s = join.sample(T0 + 5 * MS, event_payload(car, "carState"))
                self.assertEqual(s.actual_curvature, .01)  # original raw measure remains readable
                self.assertEqual(s.curvature_observation_ns, T0 if car_ok and curvature_ok and can_ok is True else None)

    def test_all_fields_from_fresh_messages(self):
        j = StreamJoiner()
        j.offer("carControl", T0, ns(latActive=True, actuators=ns(torque=-0.4)))
        j.offer("controlsState", T0, ns(desiredCurvature=0.01, curvature=0.009))
        j.offer("carOutput", T0, ns(actuatorsOutput=ns(torqueOutputCan=-345.0)))
        j.offer("radarState", T0, ns(leadOne=ns(status=True, dRel=31.5)))
        j.offer("drivingModelData", T0, model(state="laneChangeStarting", direction="left"))
        j.offer("onroadEvents", T0, [ns(name="steerSaturated"), ns(name="belowSteerSpeed")])
        s = j.sample(T0 + 50 * MS, car_state(steeringPressed=True, brakePressed=True, leftBlinker=True))
        self.assertIsInstance(s, Sample)
        self.assertEqual(s.t, (T0 + 50 * MS) / 1e9)
        self.assertEqual((s.v_ego, s.steering_angle_deg, s.steering_torque, s.steering_torque_eps),
                         (20.0, 1.5, 12.0, -30.0))
        self.assertTrue(s.steering_pressed and s.brake_pressed and s.cruise_enabled and s.gear_drive and s.blinker)
        self.assertIs(s.lat_active, True)
        self.assertEqual(s.actuators_torque, -0.4)
        self.assertEqual((s.desired_curvature, s.actual_curvature), (0.01, 0.009))
        self.assertEqual(s.torque_output, -345.0)
        self.assertTrue(s.lead_present)
        self.assertEqual(s.lead_distance_m, 31.5)
        self.assertEqual(s.lane_change_state, 2)
        self.assertEqual(s.alerts, frozenset({"steerSaturated", "belowSteerSpeed"}))
        self.assertEqual(s.missing, ())

    def test_nothing_else_received_means_everything_missing(self):
        s = StreamJoiner().sample(T0, car_state(gearShifter="park", cruiseState=ns(enabled=False)))
        self.assertIsNone(s.lat_active)
        self.assertIsNone(s.actuators_torque)
        self.assertIsNone(s.desired_curvature)
        self.assertIsNone(s.actual_curvature)
        self.assertIsNone(s.torque_output)
        self.assertFalse(s.lead_present)
        self.assertIsNone(s.lead_distance_m)
        self.assertIsNone(s.lane_change_state)
        self.assertIsNone(s.alerts)
        self.assertEqual(s.missing, ("cc", "ctl", "co", "dmd", "rs", "ev"))
        self.assertFalse(s.gear_drive or s.cruise_enabled or s.blinker or s.steering_pressed or s.brake_pressed)

    def test_lat_active_false_is_not_missing(self):
        j = StreamJoiner()
        j.offer("carControl", T0, ns(latActive=False, actuators=ns(torque=0.0)))
        s = j.sample(T0, car_state())
        self.assertIs(s.lat_active, False)
        self.assertNotIn("cc", s.missing)

    def test_lane_change_states_and_unknown(self):
        for name, code in (("off", 0), ("preLaneChange", 1), ("laneChangeStarting", 2),
                           ("laneChangeFinishing", 3), ("somethingNew", None)):
            with self.subTest(name=name):
                j = StreamJoiner()
                j.offer("drivingModelData", T0, model(state=name))
                self.assertEqual(j.sample(T0, car_state()).lane_change_state, code)

    def test_right_blinker_and_no_lead(self):
        j = StreamJoiner()
        j.offer("radarState", T0, ns(leadOne=ns(status=False, dRel=0.0)))
        s = j.sample(T0, car_state(rightBlinker=True))
        self.assertTrue(s.blinker)
        self.assertFalse(s.lead_present)
        self.assertIsNone(s.lead_distance_m)
        self.assertNotIn("rs", s.missing)

    def test_empty_alert_list_is_known_and_empty(self):
        j = StreamJoiner()
        j.offer("onroadEvents", T0, [])
        self.assertEqual(j.sample(T0, car_state()).alerts, frozenset())

    def test_non_finite_values_are_unknown(self):
        j = StreamJoiner()
        j.offer("controlsState", T0, ns(desiredCurvature=float("nan"), curvature=0.001))
        j.offer("carOutput", T0, ns(actuatorsOutput=ns(torqueOutputCan=float("inf"))))
        s = j.sample(T0, car_state())
        self.assertIsNone(s.desired_curvature)
        self.assertEqual(s.actual_curvature, 0.001)
        self.assertIsNone(s.torque_output)

    def test_a_wheel_angle_that_is_not_a_finite_number_is_unknown(self):
        # Never 0.0: that would read as a wheel moved to the middle and back.
        for angle in (float("nan"), float("inf"), None, "1.5"):
            with self.subTest(angle=angle):
                self.assertIsNone(StreamJoiner().sample(T0, car_state(steeringAngleDeg=angle)).steering_angle_deg)
        self.assertEqual(StreamJoiner().sample(T0, car_state(steeringAngleDeg=0.0)).steering_angle_deg, 0.0)


class ValueTypeTests(unittest.TestCase):
    """Numbers and flags come as plain Python values (decoded messages) or as
    numpy scalars (an input adapter that hands over table rows as they are)."""

    def test_numpy_scalars_read_like_plain_values(self):
        import numpy as np
        j = StreamJoiner()
        j.offer("carControl", T0, ns(latActive=np.bool_(True), actuators=ns(torque=np.float32(-0.5))))
        j.offer("controlsState", T0, ns(desiredCurvature=np.float64(0.01), curvature=np.float32(0.25)))
        j.offer("carOutput", T0, ns(actuatorsOutput=ns(torqueOutputCan=np.int64(-345))))
        j.offer("radarState", T0, ns(leadOne=ns(status=np.bool_(True), dRel=np.float32(31.5))))
        j.offer("drivingModelData", T0, ns(meta=ns(laneChangeState=np.int64(2), laneChangeDirection=np.int8(1)),
                                           laneLineMeta=ns(leftY=np.float32(-1.5), rightY=np.float64(1.75),
                                                           leftProb=np.float32(0.5), rightProb=np.float32(0.75))))
        s = j.sample(T0, car_state(vEgo=np.float32(20.0), steeringAngleDeg=np.float32(1.5),
                                   steeringTorque=np.int16(12), steeringTorqueEps=np.float64(-30.0),
                                   steeringPressed=np.bool_(True), brakePressed=np.bool_(True),
                                   cruiseState=ns(enabled=np.bool_(True)),
                                   leftBlinker=np.bool_(False), rightBlinker=np.bool_(True)))
        self.assertIsNotNone(s)
        self.assertEqual((s.v_ego, s.steering_angle_deg, s.steering_torque, s.steering_torque_eps),
                         (20.0, 1.5, 12.0, -30.0))
        for value in (s.v_ego, s.steering_torque, s.actuators_torque, s.torque_output, s.lead_distance_m):
            self.assertIs(type(value), float)
        self.assertIs(s.steering_pressed, True)
        self.assertIs(s.brake_pressed, True)
        self.assertIs(s.cruise_enabled, True)
        self.assertIs(s.blinker, True)
        self.assertIs(s.lat_active, True)
        self.assertEqual((s.actuators_torque, s.desired_curvature, s.actual_curvature, s.torque_output),
                         (-0.5, 0.01, 0.25, -345.0))
        self.assertIs(s.lead_present, True)
        self.assertEqual(s.lead_distance_m, 31.5)
        self.assertEqual(s.lane_change_state, 2)
        (m,) = j.models_after(T0)
        self.assertEqual((m.left_y, m.right_y, m.left_prob, m.right_prob), (-1.5, 1.75, 0.5, 0.75))
        self.assertEqual((m.lane_change_state, m.lane_change_direction), (2, 1))

    def test_numpy_false_and_non_finite_numbers(self):
        import numpy as np
        j = StreamJoiner()
        j.offer("carControl", T0, ns(latActive=np.bool_(False), actuators=ns(torque=np.float32("nan"))))
        j.offer("radarState", T0, ns(leadOne=ns(status=np.bool_(False), dRel=np.float32(5.0))))
        s = j.sample(T0, car_state(steeringPressed=np.bool_(False)))
        self.assertIs(s.lat_active, False)                   # present and false, not missing
        self.assertNotIn("cc", s.missing)
        self.assertIsNone(s.actuators_torque)
        self.assertIs(s.steering_pressed, False)
        self.assertIs(s.lead_present, False)
        self.assertIsNone(StreamJoiner().sample(T0, car_state(vEgo=np.float32("nan"))))
        self.assertIsNone(StreamJoiner().sample(T0, car_state(vEgo=np.float64("inf"))))

    def test_nothing_else_counts_as_a_number_or_a_flag(self):
        import numpy as np
        for speed in (True, np.bool_(True), "20.0", None, [20.0], 2 + 0j):
            with self.subTest(speed=speed):
                self.assertIsNone(StreamJoiner().sample(T0, car_state(vEgo=speed)))
        for flag in (1, 1.0, "true", np.int64(1), [True], None):
            with self.subTest(flag=flag):
                j = StreamJoiner()
                j.offer("carControl", T0, ns(latActive=flag))
                s = j.sample(T0, car_state(steeringPressed=flag, leftBlinker=flag))
                self.assertIsNone(s.lat_active)
                self.assertIs(s.steering_pressed, False)
                self.assertIs(s.blinker, False)

    def test_plain_integers_are_numbers(self):
        s = StreamJoiner().sample(T0, car_state(vEgo=20, steeringTorque=-7))
        self.assertEqual((s.v_ego, s.steering_torque), (20.0, -7.0))
        self.assertIs(type(s.v_ego), float)


class ListReader:
    """Stands in for a decoded list as a message decoder hands it over: it can be
    iterated and indexed, and it is neither a list nor a tuple."""

    def __init__(self, items):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


class AlertListFormTests(unittest.TestCase):
    NAMES = frozenset({"belowSteerSpeed", "steerSaturated"})

    def sample(self, body):
        j = StreamJoiner()
        j.offer("onroadEvents", T0, body)
        return j.sample(T0, car_state())

    def test_every_accepted_form_gives_the_names(self):
        events = [ns(name="belowSteerSpeed"), ns(name="steerSaturated")]
        forms = {"list": events, "tuple": tuple(events), "list reader": ListReader(events),
                 "message holding a list": ns(onroadEvents=events),
                 "message holding a list reader": ns(onroadEvents=ListReader(events)),
                 "dict holding a list": {"onroadEvents": events},
                 "list of dicts": [{"name": "belowSteerSpeed"}, {"name": "steerSaturated"}]}
        for form, body in forms.items():
            with self.subTest(form=form):
                s = self.sample(body)
                self.assertEqual(s.alerts, self.NAMES)
                self.assertNotIn("ev", s.missing)

    def test_empty_list_in_any_form_is_known_and_empty(self):
        for body in ([], (), ListReader([]), ns(onroadEvents=[]), ns(onroadEvents=ListReader([]))):
            with self.subTest(body=body):
                s = self.sample(body)
                self.assertEqual(s.alerts, frozenset())
                self.assertNotIn("ev", s.missing)

    def test_form_that_is_not_understood_is_missing_never_empty(self):
        for body in (None, 7, "belowSteerSpeed", b"belowSteerSpeed", {"other": []}, ns(other=[]), object(),
                     [ns(other="belowSteerSpeed")], ListReader([None]), ns(onroadEvents=7),
                     "", b"", {}, ns(onroadEvents="")):                 # empty, but not an alert list
            with self.subTest(body=body):
                s = self.sample(body)
                self.assertIsNone(s.alerts)
                self.assertIn("ev", s.missing)

    def test_list_is_read_once_when_it_arrives(self):
        j = StreamJoiner()
        j.offer("onroadEvents", T0, iter([ns(name="belowSteerSpeed")]))      # can be read only once
        first = j.sample(T0, car_state())
        second = j.sample(T0 + 100 * MS, car_state())
        self.assertEqual(first.alerts, frozenset({"belowSteerSpeed"}))
        self.assertEqual(second.alerts, frozenset({"belowSteerSpeed"}))      # the held list is still known

    def test_newer_list_replaces_the_names(self):
        j = StreamJoiner()
        j.offer("onroadEvents", T0, ListReader([ns(name="belowSteerSpeed")]))
        j.offer("onroadEvents", T0 + 100 * MS, ListReader([]))
        j.offer("onroadEvents", T0 + 50 * MS, ListReader([ns(name="steerSaturated")]))   # out of order: ignored
        self.assertEqual(j.sample(T0 + 100 * MS, car_state()).alerts, frozenset())


class TimeBaseTests(unittest.TestCase):
    def test_first_interval_then_spacing_then_gap(self):
        j = StreamJoiner()
        first = j.sample(T0, car_state())
        second = j.sample(T0 + 103 * MS, car_state())
        edge = j.sample(T0 + 353 * MS, car_state())          # exactly 0.25 s later: not a gap
        late = j.sample(T0 + 353 * MS + 1_300 * MS, car_state())
        self.assertEqual((first.dt, first.gap, first.raw_dt), (0.10, False, 0.10))
        self.assertAlmostEqual(second.dt, 0.103, places=9)
        self.assertFalse(second.gap)
        self.assertEqual((edge.dt, edge.gap), (0.25, False))
        self.assertEqual((late.dt, late.gap), (0.25, True))
        self.assertAlmostEqual(late.raw_dt, 1.3, places=9)

    def test_car_state_without_a_finite_speed_gives_no_sample(self):
        j = StreamJoiner()
        j.sample(T0, car_state())
        self.assertIsNone(j.sample(T0 + 100 * MS, car_state(vEgo=float("nan"))))
        self.assertIsNone(j.sample(T0 + 200 * MS, car_state(vEgo=None)))
        after = j.sample(T0 + 300 * MS, car_state())
        self.assertTrue(after.gap)                           # the time without a speed is unobserved
        self.assertAlmostEqual(after.raw_dt, 0.3, places=9)

    def test_same_or_older_car_state_gives_no_second_sample(self):
        j = StreamJoiner()
        self.assertIsNotNone(j.sample(T0, car_state()))
        self.assertIsNone(j.sample(T0, car_state()))
        self.assertIsNone(j.sample(T0 - 10 * MS, car_state()))


class FreshnessTests(unittest.TestCase):
    def lat(self, offset_ns, **kwargs):
        j = StreamJoiner(**kwargs)
        j.offer("carControl", T0 + offset_ns, ns(latActive=True, actuators=ns(torque=0.0)))
        return j.sample(T0, car_state()).lat_active

    def test_message_slightly_newer_than_car_state_is_used(self):
        self.assertIs(self.lat(5 * MS), True)
        self.assertIs(self.lat(100 * MS), True)

    def test_message_far_in_the_future_is_missing(self):
        self.assertIsNone(self.lat(101 * MS))

    def test_message_up_to_half_a_second_old_is_used(self):
        self.assertIs(self.lat(-500 * MS), True)
        self.assertIsNone(self.lat(-501 * MS))

    def test_newer_message_replaces_and_older_is_ignored(self):
        j = StreamJoiner()
        j.offer("carControl", T0, ns(latActive=True))
        j.offer("carControl", T0 - 50 * MS, ns(latActive=False))     # out of order: ignored
        self.assertIs(j.sample(T0, car_state()).lat_active, True)

    def test_alert_list_is_trusted_for_five_seconds(self):
        j = StreamJoiner()
        j.offer("onroadEvents", T0, [ns(name="belowSteerSpeed")])
        self.assertEqual(j.sample(T0 + 5 * SEC, car_state()).alerts, frozenset({"belowSteerSpeed"}))
        late = j.sample(T0 + 5 * SEC + 100 * MS, car_state())
        self.assertIsNone(late.alerts)
        self.assertIn("ev", late.missing)

    def test_alert_list_slightly_newer_than_car_state_is_used(self):
        for offset_ms, expect in ((50, frozenset({"belowSteerSpeed"})), (100, frozenset({"belowSteerSpeed"})),
                                  (101, None)):
            with self.subTest(offset_ms=offset_ms):
                j = StreamJoiner()
                j.offer("onroadEvents", T0 + offset_ms * MS, [ns(name="belowSteerSpeed")])
                self.assertEqual(j.sample(T0, car_state()).alerts, expect)

    def test_model_and_lead_limits_follow_the_log_kind(self):
        for kwargs, expect_state, expect_lead in (({}, None, False),
                                                  ({"fresh_model_s": 1.25, "fresh_lead_s": 0.75}, 0, True)):
            with self.subTest(kwargs=kwargs):
                j = StreamJoiner(**kwargs)
                j.offer("drivingModelData", T0 - 1_200 * MS, model())
                j.offer("radarState", T0 - 700 * MS, ns(leadOne=ns(status=True, dRel=40.0)))
                s = j.sample(T0, car_state())
                self.assertEqual(s.lane_change_state, expect_state)
                self.assertEqual(s.lead_present, expect_lead)

    def test_forget_before_drops_what_came_before_the_drive(self):
        j = StreamJoiner()
        j.offer("onroadEvents", T0 - 3 * SEC, [ns(name="belowSteerSpeed")])
        j.offer("carControl", T0 - 200 * MS, ns(latActive=True))
        j.forget_before(T0 - 1 * SEC)
        s = j.sample(T0, car_state())
        self.assertIsNone(s.alerts)
        self.assertIs(s.lat_active, True)


class ModelMessageTests(unittest.TestCase):
    """models_after is called once per batch, with the time of the batch's Sample or None."""

    def test_each_new_message_is_handed_out_once_after_a_sample_at_or_after_it(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0, model(state="preLaneChange", direction="right"))
        j.sample(T0 + 20 * MS, car_state())
        (m,) = j.models_after(T0 + 20 * MS)
        self.assertIsInstance(m, ModelMsg)
        self.assertEqual(m.t_m, T0 / 1e9)
        self.assertEqual((m.left_y, m.right_y, m.left_prob, m.right_prob), (-1.8, 1.7, 0.95, 0.9))
        self.assertEqual((m.lane_change_state, m.lane_change_direction), (1, 2))
        self.assertEqual(j.models_after(T0 + 20 * MS), [])
        j.offer("drivingModelData", T0, model())               # the same message offered again
        self.assertEqual(j.models_after(T0 + 120 * MS), [])

    def test_message_newer_than_the_sample_is_held_for_the_next_one(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0 + 8 * MS, model())
        self.assertEqual(j.models_after(T0), [])
        self.assertEqual([m.t_m for m in j.models_after(T0 + 100 * MS)], [(T0 + 8 * MS) / 1e9])

    def test_held_message_and_the_one_of_the_next_batch_both_go_out_in_order(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0 + 8 * MS, model())
        self.assertEqual(j.models_after(T0), [])
        j.offer("drivingModelData", T0 + 58 * MS, model())      # next batch: older than its carState
        got = j.models_after(T0 + 100 * MS)
        self.assertEqual([m.t_m for m in got], [(T0 + 8 * MS) / 1e9, (T0 + 58 * MS) / 1e9])
        self.assertEqual(got[1].spacing, 0.05)

    def test_held_message_goes_out_while_the_new_one_is_held_in_turn(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0 + 8 * MS, model())
        self.assertEqual(j.models_after(T0), [])
        j.offer("drivingModelData", T0 + 108 * MS, model())     # newer than this batch's carState again
        self.assertEqual([m.t_m for m in j.models_after(T0 + 100 * MS)], [(T0 + 8 * MS) / 1e9])
        self.assertEqual([m.t_m for m in j.models_after(T0 + 200 * MS)], [(T0 + 108 * MS) / 1e9])

    def test_a_newer_message_replaces_the_held_one_when_no_sample_came_between(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0 + 8 * MS, model())
        self.assertEqual(j.models_after(T0), [])
        j.offer("drivingModelData", T0 + 58 * MS, model())
        self.assertEqual(j.models_after(None), [])              # a batch without a Sample
        self.assertEqual([m.t_m for m in j.models_after(T0 + 100 * MS)], [(T0 + 58 * MS) / 1e9])
        self.assertEqual(j.models_after(T0 + 200 * MS), [])

    def test_message_waits_through_batches_without_a_sample(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0, model())
        self.assertEqual(j.models_after(None), [])
        self.assertEqual(j.models_after(None), [])
        self.assertEqual(len(j.models_after(T0 + 300 * MS)), 1)

    def test_interval_is_time_since_the_previous_handed_out_message_capped(self):
        j = StreamJoiner()
        got = []
        for offset in (0, 500 * MS, 1_000 * MS, 3_000 * MS):
            j.offer("drivingModelData", T0 + offset, model())
            got.extend(j.models_after(T0 + offset))
        self.assertEqual([m.dt_m for m in got], [0.0, 0.5, 0.5, 1.0])
        self.assertEqual([m.spacing for m in got], [None, 0.5, 0.5, 2.0])

    def test_direction_codes(self):
        for name, code in (("none", 0), ("left", 1), ("right", 2), ("other", None)):
            with self.subTest(name=name):
                j = StreamJoiner()
                j.offer("drivingModelData", T0, model(direction=name))
                self.assertEqual(j.models_after(T0)[0].lane_change_direction, code)

    def test_forget_before_also_drops_a_waiting_model_message(self):
        j = StreamJoiner()
        j.offer("drivingModelData", T0 - 3 * SEC, model())
        j.forget_before(T0 - 1 * SEC)
        self.assertEqual(j.models_after(T0), [])

    def test_forget_before_also_drops_a_held_model_message(self):
        for age_ms, handed_out in ((3_000, 0), (500, 1)):
            with self.subTest(age_ms=age_ms):
                j = StreamJoiner()
                j.offer("drivingModelData", T0 - age_ms * MS, model())
                self.assertEqual(j.models_after(None), [])          # a batch without a Sample: the message is held
                j.forget_before(T0 - 1 * SEC)
                self.assertEqual(len(j.models_after(T0)), handed_out)


class ModelStreamSpeedTests(unittest.TestCase):
    def run_stream(self, spacings_ms):
        j = StreamJoiner()
        t = T0
        out = []
        j.offer("drivingModelData", t, model())
        out.extend(j.models_after(t))
        for spacing in spacings_ms:
            t += spacing * MS
            j.offer("drivingModelData", t, model())
            out.extend(j.models_after(t))
        return out

    def test_single_message_counts_as_fast(self):
        first = self.run_stream([])[0]
        self.assertFalse(first.slow)
        self.assertFalse(first.gap)

    def test_ten_and_twenty_hz_are_fast_and_two_hz_is_slow(self):
        self.assertFalse(any(m.slow for m in self.run_stream([50] * 30)))
        self.assertFalse(any(m.slow for m in self.run_stream([104] * 30)))
        slow = self.run_stream([500] * 30)
        self.assertTrue(all(m.slow for m in slow[1:]))
        self.assertFalse(any(m.gap for m in slow))

    def test_gap_limit_follows_the_stream_speed(self):
        fast = self.run_stream([100] * 10 + [400] + [100] * 3)
        self.assertEqual([m.gap for m in fast].count(True), 1)
        self.assertTrue(fast[11].gap)
        self.assertFalse(fast[11].slow)
        slow = self.run_stream([500] * 10 + [1_200, 1_300] + [500] * 3)
        self.assertEqual([i for i, m in enumerate(slow) if m.gap], [12])

    def test_median_of_an_even_number_of_spacings_is_the_upper_middle(self):
        # spacings 0.1 and 0.3: the upper middle (0.3) makes the stream slow
        self.assertTrue(self.run_stream([100, 300])[2].slow)
        # 0.1, 0.1, 0.3: the middle is 0.1
        self.assertFalse(self.run_stream([100, 300, 100])[3].slow)

    def test_only_the_last_twenty_spacings_count(self):
        msgs = self.run_stream([500] * 20 + [100] * 11)
        self.assertTrue(msgs[30].slow)       # 10 of the last 20 spacings are still 0.5 s: upper middle 0.5
        self.assertFalse(msgs[31].slow)      # 11 of 20 are 0.1 s


if __name__ == "__main__":
    unittest.main()
