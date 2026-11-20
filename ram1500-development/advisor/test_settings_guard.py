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


if __name__ == "__main__":
    unittest.main()
