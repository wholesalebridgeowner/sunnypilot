"""Live suggestion rule and nightly recommendation."""
import unittest

from rules import live_suggestion, nightly_recommendation

TRIAL_CHANGE = {"LagdToggle": False, "LagdToggleDelay": 0.1}


def minute(**overrides):
    values = {"engagedMinutes": 1.0, "straightMinutes": 1.0, "weavePerStraightMinute": 1.0,
              "curveSamples": 0, "curveRelError": None, "steerWarningEpisodes": 0,
              "laneChanges": 0, "overrideEpisodes": 0, "hardBrakes": [], "speedMeanMs": 25.0}
    values.update(overrides)
    return values


class LiveSuggestionTests(unittest.TestCase):
    def suggest(self, rows, trials=0, active=False):
        return live_suggestion(rows, trials_this_drive=trials, trial_active=active)

    def test_needs_five_engaged_minutes(self):
        self.assertIsNone(self.suggest([minute(weavePerStraightMinute=10.0)] * 4))

    def test_weaving_suggests_shorter_delay(self):
        s = self.suggest([minute(weavePerStraightMinute=10.0)] * 5)
        self.assertEqual((s["step"], s["reason"]), (-1, "weaving_on_straight_road"))

    def test_late_curves_suggest_longer_delay(self):
        rows = [minute(straightMinutes=0.0, weavePerStraightMinute=None, curveSamples=600,
                       curveRelError=0.3)] * 5
        s = self.suggest(rows)
        self.assertEqual((s["step"], s["reason"]), (1, "late_in_curves"))

    def test_conflicting_signals_suggest_nothing(self):
        rows = [minute(weavePerStraightMinute=10.0, curveSamples=600, curveRelError=0.3)] * 5
        self.assertIsNone(self.suggest(rows))

    def test_nothing_during_a_trial_or_after_two(self):
        rows = [minute(weavePerStraightMinute=10.0)] * 5
        self.assertIsNone(self.suggest(rows, active=True))
        self.assertIsNone(self.suggest(rows, trials=2))

    def test_only_the_last_ten_minutes_count(self):
        rows = [minute(weavePerStraightMinute=10.0)] * 5 + [minute()] * 10
        self.assertIsNone(self.suggest(rows))


class NightlyTests(unittest.TestCase):
    def baseline(self, warnings=0):
        return {"trial": None, "summaries": [minute(weavePerStraightMinute=10.0,
                                                    steerWarningEpisodes=warnings)] * 10}

    def trial(self, minutes=6, weave=7.0, warnings=0):
        return {"trial": {"reason": "weaving_on_straight_road", "change": TRIAL_CHANGE},
                "summaries": [minute(weavePerStraightMinute=weave, steerWarningEpisodes=warnings,
                                     speedMeanMs=26.0)] * minutes}

    def test_improved_trial_is_recommended_to_keep(self):
        report = nightly_recommendation([self.baseline(), self.trial()], nnlc_on=False)
        self.assertEqual(report["suggestion"], {"kind": "keep_trial", "change": TRIAL_CHANGE,
                                                "reason": "weaving_on_straight_road_improved"})

    def test_trial_with_more_warnings_is_not_kept(self):
        report = nightly_recommendation([self.baseline(), self.trial(warnings=1)], nnlc_on=True)
        self.assertIsNone(report["suggestion"])
        self.assertFalse(report["trials"][0]["keep"])

    def test_short_trial_is_not_judged(self):
        report = nightly_recommendation([self.baseline(), self.trial(minutes=4)], nnlc_on=True)
        self.assertEqual(report["trials"][0]["why"], "not_enough_trial_driving")
        self.assertIsNone(report["suggestion"])

    def test_frequent_warnings_suggest_neural_steering_when_off(self):
        drives = [{"trial": None, "summaries": [minute(steerWarningEpisodes=1)] + [minute()] * 9}]
        report = nightly_recommendation(drives, nnlc_on=False)
        self.assertEqual(report["suggestion"]["change"], {"NeuralNetworkLateralControl": True})
        self.assertIsNone(nightly_recommendation(drives, nnlc_on=True)["suggestion"])

    def test_braking_advice_after_close_hard_stops(self):
        brakes = [{"leadPresent": True, "leadDistanceM": 9.0, "speedMs": 20.0}]
        drives = [{"trial": None, "summaries": [minute(hardBrakes=brakes)] * 2}]
        report = nightly_recommendation(drives, nnlc_on=True)
        self.assertEqual(report["hardBrakes"], 2)
        self.assertEqual(report["closeLeadHardBrakes"], 2)
        self.assertIn("following-distance", report["brakingAdvice"])

    def test_at_most_one_suggestion(self):
        report = nightly_recommendation([self.baseline(), self.trial(), self.trial()], nnlc_on=False)
        self.assertEqual(report["suggestion"]["kind"], "keep_trial")
        self.assertEqual(len(report["trials"]), 2)


if __name__ == "__main__":
    unittest.main()
