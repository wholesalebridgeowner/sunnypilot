"""Synthetic independent causal privacy tests for OEM parking EPS episodes."""
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).with_name("park_assist_event_audit.py")
spec=importlib.util.spec_from_file_location("park_event",p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def test_factory_flag_controls_episode_and_steering_comma_is_causal(self):
        a=mod.ParkEpisodes()
        a.update(1.0,"carControl",lat_active=False)
        a.update(1.0,"carOutput",comma_torque=0)
        a.update(1.01,"eps",park_flag=False)
        a.update(1.02,"eps",park_flag=True)
        a.update(1.03,"carState",speed=.05,angle=-100,driver_pressed=False,gear="reverse")
        a.update(1.04,"carControl",lat_active=False)
        a.update(1.04,"carOutput",comma_torque=0)
        a.update(1.05,"carState",speed=.0,angle=150,driver_pressed=True,gear="reverse")
        a.update(1.06,"eps",park_flag=True)
        a.update(1.07,"eps",park_flag=False)
        result=a.finish()
        self.assertEqual(result["epsFrameObservations"],4)
        self.assertEqual(len(result["factoryParkEpisodes"]),1)
        ep=result["factoryParkEpisodes"][0]
        self.assertEqual(ep["nearStopAngleRangeDeg"],250)
        self.assertEqual(ep["nearStopSamples"],2)
        self.assertEqual(ep["freshCommaLatSamples"],2)
        self.assertEqual(ep["commaLatActiveFalse"],2)
        self.assertEqual(ep["nonzeroCommaTorqueSamples"],0)
        self.assertEqual(ep["steeringPressedNearStopSamples"],1)
        self.assertTrue(ep["closedWithFalseFlag"])
        self.assertNotIn("startSeconds",str(ep))

    def test_missing_old_or_future_comma_inputs_never_counted(self):
        a=mod.ParkEpisodes()
        a.update(0.0,"carControl",lat_active=True)
        a.update(0.0,"carOutput",comma_torque=350)
        a.update(1.0,"eps",park_flag=True)
        a.update(1.01,"carState",speed=0,angle=80,driver_pressed=False,gear="drive")
        a.update(1.02,"eps",park_flag=False)
        ep=a.finish()["factoryParkEpisodes"][0]
        self.assertEqual(ep["freshCommaLatSamples"],0)
        self.assertEqual(ep["freshCommaTorqueSamples"],0)

    def test_partial_window_is_censored_without_false_flag(self):
        a=mod.ParkEpisodes()
        a.update(4.0,"eps",park_flag=True)
        a.update(4.01,"carState",speed=0,angle=40)
        ep=a.finish()["factoryParkEpisodes"][0]
        self.assertFalse(ep["closedWithFalseFlag"])
        self.assertIsNone(ep["nearStopAngleRangeDeg"] if ep["nearStopSamples"]==0 else None)

    def test_invalid_and_out_of_order_rejected(self):
        a=mod.ParkEpisodes()
        for t in (-1,float("nan")):
            with self.assertRaises(ValueError):a.update(t,"eps",park_flag=True)
        a.update(1.0,"eps",park_flag=True)
        a.update(.5,"eps",park_flag=False)
        self.assertEqual(a.finish()["outOfOrderObservations"],1)

    def test_nonfinite_speed_and_angle_not_mistaken_for_stop(self):
        a=mod.ParkEpisodes()
        a.update(0.0,"eps",park_flag=True)
        a.update(.01,"carState",speed=float("nan"),angle=50)
        a.update(.02,"carState",speed=0,angle=float("nan"))
        a.update(.03,"eps",park_flag=False)
        self.assertEqual(a.finish()["factoryParkEpisodes"][0]["nearStopSamples"],0)


if __name__=="__main__":
    unittest.main()
