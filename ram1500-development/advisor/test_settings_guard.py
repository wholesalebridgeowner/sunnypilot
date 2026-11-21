"""Settings guard: only allowlisted settings, only inside the fixed safe ranges."""
import unittest

from settings_guard import GuardError, trial_values, validate_change

LEARNED, BASE = 0.25, 0.1


class TrialValuesTests(unittest.TestCase):
    def test_one_step_shorter(self):
        self.assertEqual(trial_values(LEARNED, BASE, -1), {"LagdToggle": False, "LagdToggleDelay": 0.1})

    def test_two_steps_longer(self):
        self.assertEqual(trial_values(LEARNED, BASE, 2)["LagdToggleDelay"], 0.25)

    def test_only_steps_of_one_or_two_allowed(self):
        for step in (0, 3, -3, 1.5):
            with self.subTest(step=step), self.assertRaises(GuardError):
                trial_values(LEARNED, BASE, step)

    def test_total_never_below_minimum(self):
        with self.assertRaises(GuardError):
            trial_values(0.12, 0.1, -1)

    def test_total_never_above_maximum(self):
        with self.assertRaises(GuardError):
            trial_values(0.48, 0.1, 1)

    def test_software_delay_never_negative(self):
        with self.assertRaises(GuardError):
            trial_values(0.15, 0.2, -1)


class ValidateChangeTests(unittest.TestCase):
    def check(self, change, mode="live"):
        validate_change(change, mode=mode, learned_delay_s=LEARNED, base_actuator_delay_s=BASE)

    def test_unknown_key_refused(self):
        with self.assertRaises(GuardError):
            self.check({"SteerMax": 400}, mode="nightly")

    def test_live_cannot_change_neural_steering(self):
        with self.assertRaises(GuardError):
            self.check({"NeuralNetworkLateralControl": True})

    def test_nightly_can_change_neural_steering(self):
        self.check({"NeuralNetworkLateralControl": True}, mode="nightly")

    def test_nightly_can_turn_learning_back_on(self):
        self.check({"LagdToggle": True}, mode="nightly")

    def test_live_must_set_both_delay_keys(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggleDelay": 0.1})

    def test_fixed_delay_requires_learning_off(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": True, "LagdToggleDelay": 0.1})

    def test_off_grid_value_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": False, "LagdToggleDelay": 0.12})

    def test_more_than_point_one_from_learned_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": False, "LagdToggleDelay": 0.30})

    def test_non_bool_toggle_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": 0, "LagdToggleDelay": 0.1})

    def test_nan_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": False, "LagdToggleDelay": float("nan")})

    def test_unknown_mode_refused(self):
        with self.assertRaises(GuardError):
            self.check({"LagdToggle": True}, mode="remote")


class FullPrecisionLearnedDelayTests(unittest.TestCase):
    """A learned delay is a full-precision number while a trial total is rounded
    to 0.001 s, so the step and offset tests must allow for that rounding."""
    LEARNED = 0.32812345678901234      # an invented value with the precision a learned delay really has
    BASE = 0.2

    def learned_values(self):
        return [round(0.31 + 0.0001 * k, 4) for k in range(301)]     # 0.3100 .. 0.3400

    def accepts(self, change, learned):
        validate_change(change, mode="live", learned_delay_s=learned, base_actuator_delay_s=self.BASE)

    def reason(self, change, learned, base=None):
        with self.assertRaises(GuardError) as caught:
            validate_change(change, mode="live", learned_delay_s=learned,
                            base_actuator_delay_s=self.BASE if base is None else base)
        return str(caught.exception)

    def test_one_step_trials_from_a_full_precision_learned_value(self):
        longer = trial_values(self.LEARNED, self.BASE, 1)
        shorter = trial_values(self.LEARNED, self.BASE, -1)
        self.assertEqual(longer, {"LagdToggle": False, "LagdToggleDelay": 0.178})
        self.assertEqual(shorter, {"LagdToggle": False, "LagdToggleDelay": 0.078})
        self.accepts(longer, self.LEARNED)
        self.accepts(shorter, self.LEARNED)

    def test_every_learned_value_across_the_plausible_range(self):
        for learned in self.learned_values():
            with self.subTest(learned=learned):
                for step in (1, -1, 2):
                    self.accepts(trial_values(learned, self.BASE, step), learned)
                with self.assertRaises(GuardError) as caught:
                    trial_values(learned, self.BASE, -2)
                self.assertEqual(str(caught.exception), "software_delay_outside_screen_range")

    def test_two_steps_longer_needs_the_offset_allowance(self):
        change = trial_values(0.3287, self.BASE, 2)
        self.assertEqual(change["LagdToggleDelay"], 0.229)   # total 0.429: 0.1003 from the learned value
        self.accepts(change, 0.3287)

    def test_off_step_total_still_refused(self):
        change = {"LagdToggle": False, "LagdToggleDelay": self.LEARNED + 0.03 - self.BASE}
        self.assertEqual(self.reason(change, self.LEARNED), "delay_not_on_step")

    def test_just_past_the_rounding_allowance_refused(self):
        on_step = self.LEARNED + 0.05 - self.BASE
        self.accepts({"LagdToggle": False, "LagdToggleDelay": on_step + 0.0005}, self.LEARNED)
        self.assertEqual(self.reason({"LagdToggle": False, "LagdToggleDelay": on_step + 0.0006}, self.LEARNED),
                         "delay_not_on_step")

    def test_too_far_from_learned_still_refused(self):
        change = {"LagdToggle": False, "LagdToggleDelay": self.LEARNED + 0.15 - self.BASE}
        self.assertEqual(self.reason(change, self.LEARNED), "delay_too_far_from_learned")

    def test_software_delay_below_the_settings_screen_range(self):
        self.assertEqual(self.reason({"LagdToggle": False, "LagdToggleDelay": 0.03}, 0.28),
                         "software_delay_outside_screen_range")

    def test_software_delay_above_the_settings_screen_range(self):
        # Checked before the other delay tests: this value is also too far from the learned delay.
        self.assertEqual(self.reason({"LagdToggle": False, "LagdToggleDelay": 0.55}, 0.28, base=0.0),
                         "software_delay_outside_screen_range")

    def test_screen_range_ends_are_allowed(self):
        validate_change({"LagdToggle": False, "LagdToggleDelay": 0.05}, mode="live",
                        learned_delay_s=0.30, base_actuator_delay_s=0.2)
        validate_change({"LagdToggle": False, "LagdToggleDelay": 0.5}, mode="live",
                        learned_delay_s=0.45, base_actuator_delay_s=0.0)


if __name__ == "__main__":
    unittest.main()
