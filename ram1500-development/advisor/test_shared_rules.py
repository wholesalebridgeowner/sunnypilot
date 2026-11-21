"""The shared constants exist once, with the agreed values."""
import ast
import unittest
from pathlib import Path

import shared_rules

EXPECTED = {
    "DT_CAP_S": 0.25,
    "FIRST_DT_S": 0.10,
    "MODEL_DT_CAP_S": 1.0,
    "WINDOW_S": 60.0,
    "FUTURE_TOL_S": 0.100,
    "FRESH_S": 0.500,
    "FRESH_MODEL_QLOG_S": 1.25,
    "FRESH_LEAD_QLOG_S": 0.75,
    "ALERTS_FRESH_S": 5.0,
    "LATCH_SPEED_MS": 14.5,
    "LATCH_RESET_GAP_S": 5.0,
    "PRESS_MIN_SPAN_S": 0.08,
    "DRIVER_RECENT_S": 2.05,
    "HOLD_AFTER_S": 5.0,
    "HOLD_BEFORE_S": 2.0,
    "EVAL_LATE_S": 6.0,
    "EVAL_LATE_LANE_S": 8.6,
    "V_MIN_QUALITY_MS": 15.0,
    "STRAIGHT_HALF_S": 5.05,
    "STRAIGHT_MEAN_CURV": 0.0002,
    "STRAIGHT_MIN_COVER_S": 8.0,
    "SWING_TREND_HALF_S": 5.05,
    "LANE_HALF_S": 5.0,
    "CURVE_DEMAND_MS2": 0.5,
    "SHARP_DEMAND_MS2": 1.5,
    "CURVE_EVENT_GAP_S": 3.15,
    "LAG_TRAIL_S": 3.05,
    "CURVE_LOW_MIN_MS": 5.0,
    "LAG_STEP_S": 0.1,
    "LAG_STEPS": 10,
    "LANE_MIN_SCORED_S": 20.0,
    "SWING_MIN_STRAIGHT_S": 15.0,
    "AY_MIN_S": 30.0,
    "AY_PERIOD_MIN_S": 0.095,
    "AY_PERIOD_MAX_S": 0.125,
    "LAG_MIN_EVENTS": 30,
    "LAG_MIN_S": 200.0,
    "AT_LIMIT_TORQUE": 343,
    "ASK_LIMIT": 0.98,
    "EVENT_MERGE_S": 10.0,
    "LIMIT_EVENT_MIN_S": 0.4,
    "MIN_DRIVE_MOVING_S": 60,
    "MOVING_ABOVE_MS": 0.5,
    "DELAY_TRUST_MIN_S": 0.05,
    "DELAY_TRUST_MAX_S": 0.95,
    "DELAY_BAND_MIN_S": 0.28,
    "DELAY_BAND_MAX_S": 0.38,
    "MODEL_SPEED_SPACINGS": 20,
    "MODEL_SLOW_PERIOD_S": 0.2,
    "MODEL_PERIOD_MIN_S": 0.05,
    "MODEL_PERIOD_MAX_S": 0.5,
    "MODEL_GAP_FAST_S": 0.35,
    "MODEL_GAP_SLOW_S": 1.25,
    "ALERT_BELOW_STEER_SPEED": "belowSteerSpeed",
    "ALERT_STEER_SATURATED": "steerSaturated",
}

# Version 1 files that still carry their own numbers until they are rewritten on the pipeline.
NOT_YET_REWRITTEN = {"agent.py"}


class SharedRulesTests(unittest.TestCase):
    def test_values(self):
        for name, value in EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(shared_rules, name), value)

    def test_limit_torque_is_98_percent_of_the_truck_limit(self):
        self.assertEqual(shared_rules.AT_LIMIT_TORQUE, round(0.98 * 350))

    def test_no_other_constant_hides_in_the_module(self):
        public = {name for name in vars(shared_rules) if name.isupper()}
        self.assertEqual(public, set(EXPECTED))

    def test_no_other_module_defines_its_own_copy(self):
        folder = Path(shared_rules.__file__).parent
        for path in sorted(folder.glob("*.py")):
            if path.name == "shared_rules.py" or path.name.startswith("test_") or path.name in NOT_YET_REWRITTEN:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                targets = []
                if isinstance(node, ast.Assign):
                    targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    targets = [node.target.id]
                for name in targets:
                    with self.subTest(file=path.name, name=name):
                        self.assertNotIn(name, EXPECTED)


if __name__ == "__main__":
    unittest.main()
