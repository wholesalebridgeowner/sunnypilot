"""Event streams become one summary per minute."""
import unittest
from types import SimpleNamespace as ns

from replay_report import summaries_from_events

CS = ns(vEgo=25.0, aEgo=0.0, steeringRateDeg=0.0, steeringPressed=False)


class ReplayTests(unittest.TestCase):
    def test_one_summary_per_minute_plus_remainder(self):
        events = []
        for i in range(1250):  # 125 s at 10 Hz
            t = i * 100_000_000
            events.append(("carControl", t, ns(latActive=True)))
            events.append(("controlsState", t, ns(desiredCurvature=0.0, curvature=0.0)))
            events.append(("carState", t, CS))
        summaries = summaries_from_events(events, sample_hz=10.0)
        self.assertEqual([s["samples"] for s in summaries], [600, 600, 50])
        self.assertEqual(summaries[0]["engagedMinutes"], 1.0)

    def test_no_events_no_summaries(self):
        self.assertEqual(summaries_from_events([], sample_hz=10.0), [])


if __name__ == "__main__":
    unittest.main()
