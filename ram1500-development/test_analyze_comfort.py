"""Read-only longitudinal/headway/lane-change analyzer fixture tests."""
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

spec=importlib.util.spec_from_file_location("comfort",Path(__file__).with_name("analyze_comfort.py"))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class Msg:
  def __init__(self,nanos,kind,payload):
    self.logMonoTime=nanos
    self.valid=True
    self._kind=kind
    setattr(self,kind,payload)
  def which(self):return self._kind

def messages():
  return [
    Msg(1_000_000_000,"carState",NS(vEgo=20.0,aEgo=.1,gasPressed=False,
      brakePressed=False,gas=0.0,steeringAngleDeg=1.2,steeringRateDeg=.02,
      steeringTorque=2,steeringTorqueEps=5,steeringPressed=False,
      cruiseState=NS(available=True,enabled=True,speed=22.0,standstill=False))),
    Msg(1_100_000_000,"radarState",NS(leadOne=NS(status=True,dRel=40.,vRel=-3.,
      vLead=17.,aRel=0.,radar=False,modelProb=.9),leadTwo=NS(status=False))),
    Msg(1_200_000_000,"carControl",NS(longActive=False,latActive=True,
       actuators=NS(accel=0.6,torque=.2,longControlState="off"))),
    Msg(1_250_000_000,"longitudinalPlan",NS(hasLead=True,aTarget=.5,
      shouldStop=False,allowThrottle=True,allowBrake=True,longitudinalPlanSource="lead0",
      accels=[.5,.3],speeds=[20.,20.1],jerks=[-.1,.0])),
    Msg(1_300_000_000,"drivingModelData",NS(meta=NS(laneChangeState="preLaneChange",laneChangeDirection="left"),
      laneLineMeta=NS(leftY=1.8,rightY=-1.7,leftProb=.9,rightProb=.9))),
    Msg(1_500_000_000,"drivingModelData",NS(meta=NS(laneChangeState="laneChangeStarting",laneChangeDirection="left"),
      laneLineMeta=NS(leftY=1.8,rightY=-1.7,leftProb=.8,rightProb=.8))),
    Msg(2_000_000_000,"drivingModelData",NS(meta=NS(laneChangeState="laneChangeFinishing",laneChangeDirection="left"),
      laneLineMeta=NS(leftY=1.8,rightY=-1.7,leftProb=.7,rightProb=.7))),
  ]

class TestComfort(unittest.TestCase):
  def test_headway_is_valid_distance_over_ego_speed(self):
    r=a.analyze([Path("0/qlog.zst")],lambda _: messages())
    h=r["summary"]["leadHeadway"]
    self.assertEqual(h["matchedSamples"],1)
    self.assertEqual(h["medianSeconds"],2)
    self.assertEqual(r["summary"]["longitudinalActiveSamples"]["False"],1)
    self.assertEqual(r["summary"]["longitudinalPlanSources"]["lead0"],1)
  def test_gap_is_not_estimated_if_stale_or_invalid(self):
    rows=[a.message_record(x) for x in messages()]
    radar=next(x for x in rows if x["stream"]=="radarState")
    radar["seconds"]=10.
    self.assertEqual(a.summarize(rows)[0]["leadHeadway"]["matchedSamples"],0)
  def test_lane_transitions_and_missing_values(self):
    result=a.analyze([Path("x")],lambda _: messages())
    t=result["summary"]["laneChangeTransitions"]
    self.assertEqual([x["state"] for x in t],["preLaneChange","laneChangeStarting","laneChangeFinishing"])
    self.assertIsNone(a.get(NS(), "missing"))
    self.assertIsNone(a.summarize([])[0]["durationSeconds"])
  def test_no_false_headway_for_unconfirmed_lead(self):
    rows=[a.message_record(x) for x in messages()]
    radar=next(x for x in rows if x["stream"]=="radarState")
    radar["leadOne_status"]=False
    self.assertEqual(a.summarize(rows)[0]["leadHeadway"]["matchedSamples"],0)

if __name__=="__main__":
  unittest.main()
