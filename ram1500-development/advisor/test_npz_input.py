"""npz input adapter: decimated-log batches from tables (invented numbers)."""
import tempfile
import unittest
from pathlib import Path

import npz_input
from pipeline import Pipeline, PipelineConfig


def car_state_row(t, v=20.0, gear=2, pressed=0.0, brake=0.0, left=0.0, right=0.0, cruise=1.0):
    #       t  seg vEgo aEgo angle rate torque eps  pressed gas brake gear left right cruise
    return [t, 0.0, v, 0.0, 1.5, 0.0, 12.0, -30.0, pressed, 0.0, brake, gear, left, right, cruise]


def tables():
    return {
        "cs": [car_state_row(100.0), car_state_row(100.1, pressed=1.0, left=1.0), car_state_row(100.2, gear=1)],
        "cc": [[99.995, 0.0, 1.0, 1.0, 0.0, -0.4, 0.0], [100.1, 0.0, 0.0, 1.0, 0.0, 0.2, 0.0]],
        "ctl": [[100.0, 0.0, 0.009, 0.01, 0.0, 1.0, 0.0]],
        "co": [[100.05, 0.0, -345.0, -0.98], [100.09, 0.0, -100.0, -0.3]],
        "rs": [[99.9, 0.0, 1.0, 31.5, -1.0, 0.0]],
        "dmd": [[99.8, 0.0, -1.8, 1.7, 0.95, 0.9, 2.0, 1.0, 0.0], [100.2, 0.0, -1.8, 1.7, 0.95, 0.9, -1.0, 0.0, 0.0]],
        "ld": [[100.15, 0.0, 0.33]],
        "ev": [[99.0, 0.0, 0.0, 0.0], [100.1, 0.0, 1.0, 2.0]],
        "ev_names": ["", "belowSteerSpeed|steerSaturated"],
    }


class DecimatedBatchesTests(unittest.TestCase):
    def setUp(self):
        self.batches = list(npz_input.decimated_batches(tables()))

    def test_one_batch_per_car_state(self):
        self.assertEqual([b["carState"][0] for b in self.batches],
                         [100_000_000_000, 100_100_000_000, 100_200_000_000])

    def test_newest_message_at_or_before_each_car_state(self):
        first, second, third = self.batches
        self.assertEqual(first["carControl"][0], 99_995_000_000)
        self.assertEqual(first["controlsState"][0], 100_000_000_000)        # same time stamp: carState goes last
        self.assertNotIn("carOutput", first)                                # stamped after this carState
        self.assertEqual(second["carOutput"][0], 100_090_000_000)           # the newest of the two in between
        self.assertEqual(second["carOutput"][1].actuatorsOutput.torqueOutputCan, -100.0)
        self.assertNotIn("controlsState", second)                           # nothing new
        self.assertEqual(third["liveDelay"][1].lateralDelay, 0.33)

    def test_message_fields(self):
        first, second, third = self.batches
        car = second["carState"][1]
        self.assertEqual((car.vEgo, car.steeringAngleDeg, car.steeringTorque, car.steeringTorqueEps),
                         (20.0, 1.5, 12.0, -30.0))
        self.assertTrue(car.steeringPressed and car.leftBlinker and car.cruiseState.enabled)
        self.assertFalse(car.rightBlinker or car.brakePressed)
        self.assertEqual((car.gearShifter, third["carState"][1].gearShifter), ("drive", "park"))
        self.assertIs(first["carControl"][1].latActive, True)
        self.assertEqual(first["carControl"][1].actuators.torque, -0.4)
        self.assertEqual((first["controlsState"][1].curvature, first["controlsState"][1].desiredCurvature),
                         (0.009, 0.01))
        self.assertEqual((first["radarState"][1].leadOne.status, first["radarState"][1].leadOne.dRel), (True, 31.5))
        model = first["drivingModelData"][1]
        self.assertEqual((model.laneLineMeta.leftY, model.laneLineMeta.rightProb), (-1.8, 0.9))
        self.assertEqual((model.meta.laneChangeState, model.meta.laneChangeDirection), ("laneChangeStarting", "left"))
        self.assertEqual(third["drivingModelData"][1].meta.laneChangeState, "unknown")

    def test_alert_names(self):
        first, second, _ = self.batches
        self.assertEqual(first["onroadEvents"][1], [])
        self.assertEqual([e.name for e in second["onroadEvents"][1]], ["belowSteerSpeed", "steerSaturated"])

    def test_streams_can_be_left_out(self):
        batches = list(npz_input.decimated_batches(tables(), without=("onroadEvents", "liveDelay")))
        self.assertFalse(any("onroadEvents" in b or "liveDelay" in b for b in batches))
        self.assertTrue(all("carState" in b for b in batches))

    def test_batches_run_through_the_pipeline(self):
        pipeline = Pipeline(PipelineConfig.for_decimated_log(measures=()))
        out = []
        for b in self.batches:
            out.extend(pipeline.ingest_tick(b))
        out.extend(pipeline.end())
        self.assertEqual([kind for kind, _ in out], ["summary"])
        self.assertEqual(out[0][1]["n"], 3)
        self.assertEqual(out[0][1]["tMonoS"], 100.0)


class LoadTests(unittest.TestCase):
    def test_load_reads_the_tables_back(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is not installed")
        source = tables()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drive.npz"
            arrays = {name: np.array(rows, dtype=np.float64) for name, rows in source.items() if name != "ev_names"}
            np.savez_compressed(path, ev_names=np.array(source["ev_names"], dtype=str), **arrays)
            loaded = npz_input.load(path)
        self.assertEqual(loaded, source)


if __name__ == "__main__":
    unittest.main()
