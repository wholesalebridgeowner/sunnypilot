"""Nightly report lines (report_body.py)."""
import math
import unittest

from report_body import report_lines

CLOSE = "No changes are suggested. This report only measures."
ADVICE = ("following-distance", "Suggestion", "Trial", "neural-network", "Pull over", "Park", "should")


def drive(window_s=3600.0, steering_s=3000.0, bsm=True, warn=(0,), events=(), failed=(), listening=True, end=True):
    windows = math.ceil(window_s / 60)
    summaries = [{"w": i, "windowS": min(60, window_s - i * 60), "unobservedS": 0, "warnEvents": warn[i] if i < len(warn) else 0}
                 for i in range(windows)]
    d = {"driveId": "x", "start": {"agent": {"measures": ["steeringLoss", "laneChange", "brake", "limit"]}},
         "ready": {"blindSpot": bsm}, "summaries": summaries,
         "events": [{"type": t, "data": data} for t, data in events]}
    if end:
        d["ended"] = True
        d["end"] = {"windows": windows, "totals": {"windowS": window_s, "steeringS": steering_s, "unobservedS": 0},
                    "truncatedLaneChanges": 0, "failed": list(failed), "listening": listening}
    return d


class ReportBodyTests(unittest.TestCase):
    def test_drive_records_without_an_end_are_reported(self):
        lines = report_lines([{}, {}, {}], CLOSE)
        self.assertEqual((lines[0], lines[-1]), ("3 drives recorded.", CLOSE))
        recording = next(line for line in lines if line.startswith("Recording:"))
        self.assertTrue(recording.startswith("Recording: partial"))
        self.assertIn("3 drives without an end record", recording)

    def test_a_full_night(self):
        d1 = drive(window_s=5400, steering_s=4800, warn=(2, 1), events=[
            ("steering_loss", {"cause": "brake"}), ("steering_loss", {"cause": "brake"}),
            ("steering_loss", {"cause": "cruise_off"}),
            ("lane_change", {"outcome": "completed", "peakLatAccel": 0.8, "curveStart": False}),
            ("lane_change", {"outcome": "completed", "peakLatAccel": -1.3, "curveStart": False}),
            ("lane_change", {"outcome": "cancelled", "peakLatAccel": 2.0}),
            ("brake", {"peakDrop1s": 3.4, "who": "driver"}), ("brake", {"peakDrop1s": 2.1, "who": "cruise"})])
        d2 = drive(window_s=1500, steering_s=600, bsm=False, warn=(1,))
        lines = report_lines([d1, d2], CLOSE)
        self.assertEqual(lines, [
            "2 drives, 1 h 55 min observed, Comma steering active for 1 h 30 min.",
            "Comma blind-spot input at startup: enabled on 1 drive, disabled on 1 drive.",
            "Steering-limit warning episodes recorded: 4.",
            "Comma steering switched off at about 11 mph or faster: 3 recorded. Main nearby signal: 2 with brake pedal down, 1 with cruise switching off.",
            "Lane changes: 3 recorded (2 completed, 1 cancelled). Completed changes that started in a curve: 0. Peak change in sideways acceleration for 2 measured starts outside a curve: median 1.1, highest 1.3 m/s².",
            "Hard braking: 1 recorded (1 with brake pedal down).",
            "Recording: all expected summary windows received.",
            CLOSE])

    def test_jerk_medians_use_paired_completed_events_only(self):
        def event(r, m, outcome="completed"):
            return ("lane_change", {"requestJerk10Hz": r, "motionJerk10Hz": m, "outcome": outcome})
        lines = report_lines([drive(events=[event(1, 4), event(3, 2), event(9, None),
                                             event(200, 300, "cancelled")])], CLOSE)
        line = next(line for line in lines if "10 Hz" in line)
        self.assertEqual(line, "Lane-change abruptness (peak jerk, 10 Hz estimate) on 2 of 3 lane changes: "
                               "steering request 2.00, truck motion 3.00 m/s³. Before RamPilot: 4.63 and 2.34.")

    def test_quiet_drive(self):
        self.assertEqual(report_lines([drive(window_s=20, steering_s=0)], CLOSE), [
            "1 drive, under 1 min observed, Comma steering active for 0 min.", "Comma blind-spot input at startup: enabled on 1 drive.",
            "Steering-limit warning episodes recorded: 0.", "Comma steering switched off at about 11 mph or faster: none recorded.",
            "Lane changes: none recorded.", "Hard braking: none recorded.", "Recording: all expected summary windows received.", CLOSE])

    def test_recording_gaps_are_named(self):
        lines = report_lines([drive(failed=("lane",), listening=False), drive(end=False)], CLOSE)
        recording = next(line for line in lines if line.startswith("Recording:"))
        for detail in ("Recording: partial", "listening off", "lane", "1 drive without an end record"):
            self.assertIn(detail, recording)
        self.assertEqual(lines[0], "2 drives recorded.")          # one drive has no totals

    def test_ended_with_one_of_three_windows_and_unobserved_time(self):
        row = drive(window_s=180, steering_s=150, bsm=False)
        row["summaries"] = [{"w": 0, "warnEvents": 1, "windowS": 60, "unobservedS": 0}]
        row["end"]["totals"]["unobservedS"] = 60
        row["end"]["truncatedLaneChanges"] = 1
        lines = report_lines([row], CLOSE)
        recording = next(line for line in lines if line.startswith("Recording:"))
        self.assertTrue(recording.startswith("Recording: partial"), recording)
        self.assertIn("ended drive", recording)
        self.assertIn("window", recording)
        self.assertIn("unobserved", recording)
        self.assertNotIn("without an end", recording)
        for negative in ("Comma steering switched off at about 11 mph or faster: none recorded.",
                         "Lane changes: none recorded.", "Hard braking: none recorded."):
            self.assertNotIn(negative, lines)

    def test_duplicate_index_cannot_hide_a_missing_window(self):
        row = drive(window_s=180, steering_s=150)
        row["summaries"][1]["w"] = 0
        lines = report_lines([row], CLOSE)
        self.assertTrue(any(line.startswith("Recording: partial") and "window" in line for line in lines), lines)

    def test_unobserved_time_alone_is_partial_even_with_every_window(self):
        row = drive(window_s=120, steering_s=90)
        row["summaries"][1]["unobservedS"] = row["end"]["totals"]["unobservedS"] = 1.5
        lines = report_lines([row], CLOSE)
        recording = next(line for line in lines if line.startswith("Recording:"))
        self.assertTrue(recording.startswith("Recording: partial") and "unobserved" in recording, recording)
        self.assertNotIn("missing summary windows", recording)

    def test_missing_or_contradictory_window_duration_does_not_certify_coverage(self):
        for kind in ("absent_duration", "inconsistent_duration", "unknown_listening"):
            with self.subTest(kind=kind):
                row = drive(window_s=120, steering_s=90)
                if kind == "absent_duration":
                    del row["summaries"][1]["windowS"]
                elif kind == "inconsistent_duration":
                    for summary in row["summaries"]:
                        summary["windowS"] = 5
                else:
                    del row["end"]["listening"]
                self.assertTrue(any(line.startswith("Recording: partial") for line in report_lines([row], CLOSE)))

    def test_window_duration_rounding_does_not_make_a_complete_drive_partial(self):
        row = drive(window_s=120, steering_s=90)
        row["end"]["totals"]["windowS"] = 120.1
        self.assertIn("Recording: all expected summary windows received.", report_lines([row], CLOSE))

    def test_v1_summary_counts_are_distinct_from_v2_events_and_wheel_touches(self):
        old = {"ended": True, "summaries": [{"steerWarningEpisodes": 2, "laneChanges": 3, "overrideEpisodes": 7,
                                              "hardBrakes": [{"speedMs": 12.0}, {"speedMs": 18.0}]}]}
        lines = report_lines([old], CLOSE)
        for metric, value in (("Steering-limit warning episodes recorded", 2), ("Lane changes", 3), ("Hard braking", 2)):
            self.assertTrue(any(line.startswith(f"{metric}: {value}") for line in lines), lines)
        self.assertFalse(any(line.startswith("Comma steering switched off") for line in lines), lines)
        recording = next(line for line in lines if line.startswith("Recording:"))
        self.assertTrue(recording.startswith("Recording: partial"), recording)
        self.assertNotIn("without an end", recording)
        self.assertIn("ended drive", recording)

        # Detailed v2 data take precedence for that drive. Older drives on the same night still contribute.
        new = drive(events=[("lane_change", {"outcome": "completed"}), ("brake", {"peakDrop1s": 3.6, "who": "driver"})])
        new["summaries"][0].update(laneChanges=17, hardBrakes=[{}] * 8)
        mixed = report_lines([old, new], CLOSE)
        self.assertTrue(any(line.startswith("Lane changes: 4") for line in mixed), mixed)
        self.assertTrue(any(line.startswith("Hard braking: 3") for line in mixed), mixed)

        old["events"] = [{"type": "lane_change", "data": {"outcome": "completed"}}]
        partial_detail = report_lines([old], CLOSE)
        self.assertTrue(any(line.startswith("Lane changes: 1") for line in partial_detail), partial_detail)
        self.assertTrue(any(line.startswith("Hard braking: 2") for line in partial_detail), partial_detail)

    def test_unknown_blind_spot_is_not_reported_as_on_or_off(self):
        lines = report_lines([drive(bsm=True), drive(bsm=None)], CLOSE)
        self.assertIn("Comma blind-spot input at startup: enabled on 1 drive, unknown on 1 drive.", lines)
        self.assertIn("Comma blind-spot input at startup: unknown on 1 drive.", report_lines([drive(bsm=None)], CLOSE))

    def test_curve_and_unknown_starts_keep_their_counts_outside_the_straight_peak(self):
        def change(peak, **extra):
            return ("lane_change", {"outcome": "completed", "peakLatAccel": peak, **extra})
        current = drive(events=[change(.7, curveStart=False), change(2.5, curveStart=True),
                                change(1.9, curveStart=None), change(None, curveStart=False)])
        legacy = drive(events=[change(3.1)])
        line = next(l for l in report_lines([current, legacy], CLOSE) if l.startswith("Lane changes:"))
        self.assertIn("5 recorded (5 completed, 0 cancelled)", line)
        self.assertIn("started in a curve: 1", line)
        self.assertIn("unknown for 2 completed changes", line)
        self.assertIn("the one measured start outside a curve: 0.7 m/s²", line)
        line = next(l for l in report_lines([legacy], CLOSE) if l.startswith("Lane changes:"))
        self.assertIn("unknown for 1 completed change.", line)
        self.assertNotIn("Peak change", line)

    def test_short_observation_and_cruise_context(self):
        event = ("brake", {"peakDrop1s": 3.2, "who": "cruise"})
        lines = report_lines([drive(window_s=50, steering_s=45, events=[event])], CLOSE)
        self.assertEqual(lines[0], "1 drive, under 1 min observed, Comma steering active for under 1 min.")
        self.assertIn("Hard braking: 1 recorded (1 with cruise active and no pedal press).", lines)
        self.assertEqual(report_lines([drive(window_s=0, steering_s=0)], CLOSE)[0], "1 drive, 0 min observed.")

    def test_other_braking_context(self):
        event = ("brake", {"peakDrop1s": 3.2, "who": "other"})
        lines = report_lines([drive(events=[event])], CLOSE)
        self.assertIn("Hard braking: 1 recorded (1 with no brake pedal press and cruise off for most of the episode).", lines)

    def test_main_switch_loss_wording(self):
        lines = report_lines([drive(events=[("steering_loss", {"cause": "main_off"}),
                                            ("steering_loss", {"cause": "brake"})])], CLOSE)
        self.assertIn("Comma steering switched off at about 11 mph or faster: 2 recorded. Main nearby signal: "
                      "1 with brake pedal down, 1 with cruise main switch turned off.", lines)

    def test_lane_keep_button_is_the_drivers_choice_not_a_loss(self):
        # Mark, 2026-11-25: "I use the lane keep button so I have cruise but still steer".
        lines = report_lines([drive(events=[("steering_loss", {"cause": "lkas_button"}),
                                            ("steering_loss", {"cause": "brake"}),
                                            ("steering_loss", {"cause": "lkas_button"})])], CLOSE)
        self.assertIn("Comma steering switched off at about 11 mph or faster: 1 recorded. Main nearby signal: "
                      "1 with brake pedal down.", lines)
        self.assertIn("Driver turned Comma steering off with the lane-keep button: 2.", lines)
        only = report_lines([drive(events=[("steering_loss", {"cause": "lkas_button"})])], CLOSE)
        self.assertIn("Comma steering switched off at about 11 mph or faster: none recorded.", only)
        self.assertIn("Driver turned Comma steering off with the lane-keep button: 1.", only)
        self.assertFalse(any("lane-keep" in line for line in report_lines([drive()], CLOSE)))

    def test_no_advice_words(self):
        d = drive(events=[("steering_loss", {"cause": c}) for c in
                          ("brake", "main_off", "lkas_button", "gas", "cruise_off", "gear", "fault", "blinker",
                           "driver", "none", None)])
        text = "\n".join(report_lines([d], CLOSE))
        for word in ADVICE:
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
