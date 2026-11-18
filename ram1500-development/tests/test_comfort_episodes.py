"""Synthetic privacy-safe tests for pinned comfort episode analysis."""
from ramtools.comfort_episodes import evaluate

NS=1000000000

def row(s,ts,**attrs):
  return {"service":s,"t":round(ts*NS),"valid":True,**attrs}

def test_follow_requires_cruise_no_pedal_and_fresh_state():
  rows=[]
  for i in range(40):
    t=i*.1
    rows.append(row("carState",t,vEgo=20.,gasPressed=False,brakePressed=False,**{"cruiseState.enabled":True}))
    rows.append(row("radarState",t+.02,**{"leadOne.status":True,"leadOne.dRel":40.,"leadOne.vRel":-2.,"leadOne.radar":False}))
  # Driver-pedal input must remove that observation but not create invented gap.
  rows.append(row("carState",5.0,vEgo=20.,gasPressed=True,brakePressed=False,**{"cruiseState.enabled":True}))
  rows.append(row("radarState",5.02,**{"leadOne.status":True,"leadOne.dRel":40.,"leadOne.vRel":-2.}))
  s=evaluate(rows)
  f=s["following"]
  assert f["qualifyingEpisodeCount"]==1
  assert f["medianObservedGapSeconds"]==2.
  assert f["closingTTCMedianSeconds"]==20.
  assert f["leadFilteringRejections"]["driverPedalOrUnknown"]>=1

def test_stale_future_and_invalid_leads_excluded():
  rows=[row("carState",1.1,vEgo=15.,gasPressed=False,brakePressed=False,**{"cruiseState.enabled":True}),
        row("radarState",1.0,**{"leadOne.status":True,"leadOne.dRel":18.,"leadOne.vRel":-1.}),
        row("radarState",1.5,**{"leadOne.status":True,"leadOne.dRel":18.,"leadOne.vRel":-1.}),
        row("radarState",1.12,**{"leadOne.status":False,"leadOne.dRel":18.,"leadOne.vRel":-1.})]
  stats=evaluate(rows)
  assert stats["following"]["validCruiseLeadObservations"]==0
  assert stats["following"]["leadFilteringRejections"]["staleCarState"]==2
  assert stats["following"]["leadFilteringRejections"]["leadNotValid"]==1

def test_model_lc_phases_and_rate_derivatives():
  rows=[row("drivingModelData",.0,**{"meta.laneChangeState":"preLaneChange"}),
        row("drivingModelData",.1,**{"meta.laneChangeState":"laneChangeStarting","meta.laneChangeDirection":"left"}),
        row("drivingModelData",.5,**{"meta.laneChangeState":"laneChangeStarting","meta.laneChangeDirection":"left"}),
        row("drivingModelData",.8,**{"meta.laneChangeState":"laneChangeFinishing","meta.laneChangeDirection":"left"})]
  for k in range(6):
    rows.append(row("carState",.12+k*.1,steeringRateDeg=2.+k))
  stats=evaluate(rows)
  lc=stats["laneChange"]
  assert lc["startingTransitions"]==1
  assert lc["qualifiedWindows"]==1
  assert lc["p90PeakSteeringRateDegPerSec"]==7.
  assert lc["derivedRatePairs"]==5

def test_censored_or_too_short_lane_changes_excluded():
  rows=[row("drivingModelData",.0,**{"meta.laneChangeState":"off"}),
        row("drivingModelData",.1,**{"meta.laneChangeState":"laneChangeStarting"}),
        row("drivingModelData",.2,**{"meta.laneChangeState":"laneChangeFinishing"}),
        row("drivingModelData",.3,**{"meta.laneChangeState":"laneChangeStarting"})]
  stats=evaluate(rows)
  assert stats["laneChange"]["startingTransitions"]==2
  assert stats["laneChange"]["qualifiedWindows"]==0
  assert stats["laneChange"]["rejectedWindows"]=={"tooFewSteeringSamples":1,"incompleteAtRouteEnd":1}

def test_empty_and_parameter_checks():
  assert evaluate([])["status"]=="no_data"
  for kwargs in ({"minimum_follow_s":-1},{"min_lane_samples":0}):
    try:evaluate([],**kwargs)
    except ValueError:pass
    else:raise AssertionError("expected validation failure")
