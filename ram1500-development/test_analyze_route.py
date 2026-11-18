"""Pure fixture tests for the offline Ram log analyzer; no device or network needed."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

spec=importlib.util.spec_from_file_location("ram_analyzer",Path(__file__).with_name("analyze_route.py"))
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class FakeMessage:
  def __init__(self,t,kind,payload):
    self.logMonoTime=t
    self.valid=True
    self.kind=kind
    setattr(self,kind,payload)
  def which(self):
    return self.kind

class FakeLateral:
  def __init__(self):
    self.torqueState=NS(active=True,output=.4,error=.2,saturated=True,
                        actualLateralAccel=.3,desiredLateralAccel=.5)
  def which(self):
    return "torqueState"

class AnalyzerTests(unittest.TestCase):
  def setUp(self):
    self.messages=[
      FakeMessage(1_000_000_000,"carState",NS(vEgo=17.2,steeringAngleDeg=2.5,
        steeringTorque=1.5,steeringTorqueEps=.6,steeringPressed=False)),
      FakeMessage(2_000_000_000,"onroadEvents",[NS(name="steerSaturated")]),
      FakeMessage(2_500_000_000,"onroadEvents",[NS(name="steerSaturated")]),
      FakeMessage(4_100_000_000,"onroadEvents",[NS(name="steerSaturated")]),
      FakeMessage(4_200_000_000,"controlsState",NS(desiredCurvature=.02,lateralControlState=FakeLateral())),
      FakeMessage(4_300_000_000,"carOutput",NS(actuatorsOutput=NS(torque=.4,steeringAngleDeg=2.0,torqueOutputCan=120))),
    ]
  def test_sample_counts_distinct_from_episodes(self):
    result=module.analyze([Path("0/qlog.zst")],lambda _:self.messages,2,3)
    self.assertEqual(result["summary"]["eventSampleCounts"]["steerSaturated"],3)
    self.assertEqual(len(result["summary"]["eventEpisodes"]["steerSaturated"]),2)
    state=next(r for r in result["rows"] if r["stream"]=="controlsState")
    self.assertEqual(state["lateralState"],"torqueState")
    self.assertTrue(state["lateralSaturated"])
    self.assertAlmostEqual(state["lateralError"],.2)
    output=next(r for r in result["rows"] if r["stream"]=="carOutput")
    self.assertEqual(output["outputTorqueCan"],120)
  def test_missing_fields_stay_null(self):
    r=module.timeline(FakeMessage(10,"carState",NS(vEgo=10)),"carState")
    self.assertIsNone(r["steeringTorque"])
  def test_no_data_and_invalid_window(self):
    with self.assertRaises(ValueError):
      module.analyze([Path("x")],lambda _:[])
    with self.assertRaises(ValueError):
      module.analyze([Path("x")],lambda _:self.messages,window=-1)
  def test_csv_json_export(self):
    result=module.analyze([Path("x")],lambda _:self.messages,2,3)
    with tempfile.TemporaryDirectory() as tmp:
      prefix=Path(tmp)/"probe"
      module.save(result,prefix)
      self.assertIn("outputTorqueCan",prefix.with_suffix(".csv").read_text())
      self.assertIn("eventEpisodes",prefix.with_suffix(".json").read_text())

if __name__ == "__main__":
  unittest.main()
