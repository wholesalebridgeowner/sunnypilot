"""Nightly report lines (report_body.py)."""
import unittest

from report_body import report_lines

CLOSE = "No changes are suggested. This report only measures."
ADVICE = ("following-distance", "Suggestion", "Trial", "neural-network", "Pull over", "Park", "should")


def drive(window_s=3600.0, steering_s=3000.0, bsm=True, warn=(0,), events=(), failed=(), listening=True, end=True):
    d = {"driveId": "x", "ready": {"blindSpot": bsm}, "summaries": [{"warnEvents": w} for w in warn],
         "events": [{"type": t, "data": data} for t, data in events]}
    if end:
        d["end"] = {"totals": {"windowS": window_s, "steeringS": steering_s}, "failed": list(failed), "listening": listening}
    return d


class ReportBodyTests(unittest.TestCase):
    def test_old_records_keep_the_short_form(self):
        self.assertEqual(report_lines([{}, {}, {}], CLOSE), ["3 drives recorded.", CLOSE])

    def test_a_full_night(self):
        d1 = drive(window_s=5400, steering_s=4800, warn=(2, 1), events=[
            ("steering_loss", {"cause": "brake"}), ("steering_loss", {"cause": "brake"}),
            ("steering_loss", {"cause": "cruise_off"}),
            ("lane_change", {"outcome": "completed", "peakLatAccel": 0.8}),
            ("lane_change", {"outcome": "completed", "peakLatAccel": -1.3}),
            ("lane_change", {"outcome": "cancelled", "peakLatAccel": 2.0}),
            ("brake", {"peakDrop1s": 3.4, "who": "driver"}), ("brake", {"peakDrop1s": 2.1, "who": "cruise"})])
        d2 = drive(window_s=1500, steering_s=600, bsm=False, warn=(1,))
        lines = report_lines([d1, d2], CLOSE)
        self.assertEqual(lines, [
            "2 drives, 1 h 55 min, steering on 1 h 30 min.",
            "Blind spot: off on 1 of 2 drives.",
            "Steering-limit warnings: 4.",
            "Steering turned off while moving: 3 (brake 2, cruise off 1).",
            "Lane changes: 3 (2 completed, 1 cancelled). Sideways push: median 1.1, highest 1.3 m/s².",
            "Hard braking: 1 (driver 1).",
            "Recording: complete.",
            CLOSE])

    def test_quiet_drive(self):
        self.assertEqual(report_lines([drive(window_s=20, steering_s=0)], CLOSE), [
            "1 drive, under 1 min, steering on under 1 min.", "Blind spot: on.", "Steering-limit warnings: 0.",
            "Steering turned off while moving: none.", "Lane changes: none.", "Hard braking: none.",
            "Recording: complete.", CLOSE])

    def test_recording_gaps_are_named(self):
        lines = report_lines([drive(failed=("lane",), listening=False), drive(end=False)], CLOSE)
        self.assertIn("Recording: listening off on 1 drive; measures stopped: lane; 1 drive without an end record.", lines)
        self.assertEqual(lines[0], "2 drives recorded.")          # one drive has no totals

    def test_unknown_blind_spot_is_not_reported_as_on_or_off(self):
        lines = report_lines([drive(bsm=True), drive(bsm=None)], CLOSE)
        self.assertIn("Blind spot: on; not reported for 1 of 2 drives.", lines)

    def test_no_advice_words(self):
        d = drive(events=[("steering_loss", {"cause": c}) for c in
                          ("brake", "gas", "cruise_off", "gear", "fault", "blinker", "driver", "none", None)])
        text = "\n".join(report_lines([d], CLOSE))
        for word in ADVICE:
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
