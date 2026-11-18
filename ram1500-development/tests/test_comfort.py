"""Pure tests for pinned-build comfort analysis, no truck, no accounts."""
from ramtools.comfort import metrics, percentile

def row(s,t,**fields):
  return {"service":s,"t":int(t*1e9),"valid":True,**fields}

def test_measured_headway_only_when_cruise_and_no_pedals():
  items=[
    row("carState",1.00,vEgo=20.,aEgo=.2,**{"cruiseState.enabled":True,"gasPressed":False,"brakePressed":False}),
    row("radarState",1.10,**{"leadOne.status":True,"leadOne.dRel":40.,"leadOne.vRel":-1.,"leadOne.radar":False}),
    row("carState",1.20,vEgo=20.,aEgo=.25,**{"cruiseState.enabled":True,"gasPressed":True,"brakePressed":False}),
    row("radarState",1.25,**{"leadOne.status":True,"leadOne.dRel":50.,"leadOne.vRel":0.,"leadOne.radar":False}),
    row("carParams",1.30,openpilotLongitudinalControl=False,pcmCruise=True),
    row("carControl",1.40,longActive=False),
  ]
  out=metrics(items)
  assert out["headway"]["matchedCruiseSamples"]==1
  assert out["headway"]["medianSeconds"]==2.
  assert out["headway"]["sourceRadarFlagCounts"]=={"False":1}
  assert out["longitudinal"]["controlLongActiveSamples"]["False"]==1

def test_stale_lead_and_invalid_rows_not_attributed():
  items=[
    row("carState",1.0,vEgo=12.,aEgo=0.,**{"cruiseState.enabled":True,"gasPressed":False,"brakePressed":False}),
    row("radarState",1.5,**{"leadOne.status":True,"leadOne.dRel":20.}),
    row("radarState",1.08,**{"leadOne.status":False,"leadOne.dRel":20.}),
    {"service":"radarState","t":int(1.09e9),"valid":False,"leadOne.status":True,"leadOne.dRel":15.},
  ]
  assert metrics(items)["headway"]["matchedCruiseSamples"]==0

def test_lane_start_end_state_and_accel_jerk():
  items=[
    row("carState",0.0,aEgo=0.,steeringRateDeg=0.,**{"cruiseState.enabled":True,"gasPressed":False,"brakePressed":False}),
    row("drivingModelData",.05,**{"meta.laneChangeState":"preLaneChange","meta.laneChangeDirection":"left"}),
    row("drivingModelData",.15,**{"meta.laneChangeState":"laneChangeStarting","meta.laneChangeDirection":"left"}),
    row("carState",.2,aEgo=.1,steeringRateDeg=2.5,**{"cruiseState.enabled":True,"gasPressed":False,"brakePressed":False}),
    row("carState",.3,aEgo=.15,steeringRateDeg=4.,**{"cruiseState.enabled":True,"gasPressed":False,"brakePressed":False}),
    row("drivingModelData",.5,**{"meta.laneChangeState":"laneChangeFinishing","meta.laneChangeDirection":"left"}),
  ]
  v=metrics(items)
  assert len(v["laneChanges"])==1
  assert v["laneChanges"][0]["peakAbsSteeringRateDegPerSecond"]==4.
  assert v["laneChanges"][0]["durationSeconds"]==.35
  assert v["longitudinal"]["jerkSamples"]==1

def test_empty_and_bad_numbers():
  assert metrics([])["status"]=="no_data"
  assert percentile([float("nan"),None],.9) is None
