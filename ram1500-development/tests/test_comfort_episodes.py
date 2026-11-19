"""Synthetic privacy-safe tests for pinned comfort episode analysis."""
from ramtools.comfort_episodes import evaluate

NS=1000000000

def row(s,ts,**attrs):
  if s == "carState":
    attrs.setdefault("canValid", True)
  return {"service":s,"t":round(ts*NS),"valid":True,"segment":30,**attrs}

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


def test_excluded_lead_cannot_bridge_following_episodes():
  # Two 1.1 s bursts must not count as one sustained 2.4 s follow episode
  # across an intervening radar rejection. Valid sample spacing is only 0.2 s.
  for reason in ("unconfirmed", "invalid_message", "driver_pedal"):
    records=[]
    for start in (0.,1.3):
      for i in range(12):
        t=start+i*.1
        records.append(row("carState",t,vEgo=20.,gasPressed=False,brakePressed=False,
                           **{"cruiseState.enabled":True}))
        records.append(row("radarState",t+.02,**{"leadOne.status":True,
                       "leadOne.dRel":40.,"leadOne.vRel":-1.,"leadOne.radar":True}))
    if reason=="unconfirmed":
      records.append(row("radarState",1.22,**{"leadOne.status":False}))
    elif reason=="invalid_message":
      bad=row("radarState",1.22,**{"leadOne.status":True,"leadOne.dRel":40.})
      bad["valid"]=False
      records.append(bad)
    else:
      records.append(row("carState",1.2,vEgo=20.,gasPressed=True,brakePressed=False,
                         **{"cruiseState.enabled":True}))
      records.append(row("radarState",1.22,**{"leadOne.status":True,
                     "leadOne.dRel":40.,"leadOne.vRel":-1.}))
    following=evaluate(records)["following"]
    assert following["validCruiseLeadObservations"]==24
    assert following["qualifyingEpisodeCount"]==0
    assert following["qualifyingEpisodeTotalSeconds"]==0.
    reject=following["leadFilteringRejections"]
    assert reject[{"unconfirmed":"leadNotValid","invalid_message":"invalidRadarMessage",
                   "driver_pedal":"driverPedalOrUnknown"}[reason]]==1


def test_following_episode_cannot_span_two_contiguous_segments():
  records=[]
  # Valid 1.1-second bursts on either side of the boundary must remain
  # two short episodes, even when the 100 ms clock intervals are continuous.
  for seg,start in ((30,0.),(31,1.2)):
    for i in range(12):
      t=start+i*.1
      records.append(row("carState",t,segment=seg,vEgo=20.,
                         gasPressed=False,brakePressed=False,
                         **{"cruiseState.enabled":True}))
      records.append(row("radarState",t+.02,segment=seg,
                         **{"leadOne.status":True,"leadOne.dRel":30.,
                            "leadOne.vRel":-1.,"leadOne.radar":True}))
  result=evaluate(records)["following"]
  assert result["validCruiseLeadObservations"]==24
  assert result["qualifyingEpisodeCount"]==0


def test_following_rejects_fresh_but_wrong_segment_state():
  records=[]
  for i in range(30):
    t=i*.1
    records.append(row("carState",t,segment=31,vEgo=20.,
                       gasPressed=False,brakePressed=False,
                       **{"cruiseState.enabled":True}))
    records.append(row("radarState",t+.02,segment=30,
                       **{"leadOne.status":True,"leadOne.dRel":30.,"leadOne.vRel":-1.}))
  result=evaluate(records)["following"]
  assert result["validCruiseLeadObservations"]==0
  assert result["leadFilteringRejections"]["carStateSegmentMismatch"]==30
  assert result["qualifyingEpisodeCount"]==0


def test_following_missing_radar_segment_is_not_observational_evidence():
  records=[]
  for i in range(30):
    t=i*.1
    records.append(row("carState",t,vEgo=20.,
                       gasPressed=False,brakePressed=False,
                       **{"cruiseState.enabled":True}))
    records.append(row("radarState",t+.02,segment=None,
                       **{"leadOne.status":True,"leadOne.dRel":30.,"leadOne.vRel":-1.}))
  result=evaluate(records)["following"]
  assert result["validCruiseLeadObservations"]==0
  assert result["leadFilteringRejections"]["radarSegmentUnknown"]==30

def test_invalid_or_unknown_vehicle_state_and_can_health_break_following_continuity():
  # One poisoned snapshot between two 1.6-s bursts must not be ignored by an
  # as-of join or turn into a fabricated qualifying 3.3-s follow episode.
  for signal, bad_value in (("valid", False), ("valid", None),
                            ("canValid", False), ("canValid", None)):
    records=[]
    for i in range(34):
      t=i*.1
      state=row("carState", t, vEgo=20., gasPressed=False, brakePressed=False,
                **{"cruiseState.enabled": True})
      if i == 17:
        state[signal] = bad_value
      records.append(state)
      records.append(row("radarState", t+.02,
                         **{"leadOne.status": True, "leadOne.dRel": 35.,
                            "leadOne.vRel": -1., "leadOne.radar": True}))
    observed=evaluate(records)["following"]
    assert observed["validCruiseLeadObservations"] == 33, (signal, bad_value)
    assert observed["leadFilteringRejections"]["invalidCarStateOrCan"] == 1
    assert observed["qualifyingEpisodeCount"] == 0


def test_unknown_radar_message_validity_cannot_extend_following():
  records=[]
  for i in range(34):
    t=i*.1
    records.append(row("carState", t, vEgo=20., gasPressed=False,
                       brakePressed=False, **{"cruiseState.enabled": True}))
    radar=row("radarState", t+.02, **{"leadOne.status": True,
              "leadOne.dRel": 35., "leadOne.vRel": -1.})
    if i == 17:
      radar["valid"] = None
    records.append(radar)
  following=evaluate(records)["following"]
  assert following["validCruiseLeadObservations"] == 33
  assert following["leadFilteringRejections"]["invalidRadarMessage"] == 1
  assert following["qualifyingEpisodeCount"] == 0


def _uninterrupted_factory_cruise_fixture():
  rows=[]
  for i in range(34):
    t=i*.1
    rows.append(row("carState",t,vEgo=20.,gasPressed=False,
                    brakePressed=False,**{"cruiseState.enabled":True}))
    rows.append(row("radarState",t+.02,**{"leadOne.status":True,
                    "leadOne.dRel":40.,"leadOne.vRel":-1.,"leadOne.radar":True}))
  return rows


def test_identical_equal_time_radar_frames_count_as_one_observation():
  baseline=_uninterrupted_factory_cruise_fixture()
  expected=evaluate(baseline)["following"]
  duplicate=next(r.copy() for r in baseline if
                 r["service"]=="radarState" and r["t"]==round(1.72*NS))
  observed=evaluate([*baseline,duplicate])["following"]
  assert expected["validCruiseLeadObservations"]==34
  assert expected["qualifyingEpisodeCount"]==1
  assert observed==expected


def test_conflicting_equal_time_radar_cannot_extend_following_episode():
  baseline=_uninterrupted_factory_cruise_fixture()
  original=next(r for r in baseline if r["service"]=="radarState"
                and r["t"]==round(1.72*NS))
  # Ambiguous at one timestamp even though either individual lead would
  # otherwise be valid. Both original and reverse order must censor it.
  conflicting=original.copy()
  conflicting["leadOne.dRel"]=70.
  for rows in ([*baseline,conflicting],
               [conflicting,*baseline]):
    following=evaluate(rows)["following"]
    assert following["validCruiseLeadObservations"]==33
    assert following["leadFilteringRejections"]["ambiguousRadarTimestamp"]==1
    assert following["qualifyingEpisodeCount"]==0
    assert following["qualifyingEpisodeTotalSeconds"]==0.


def test_equal_time_disagreement_in_lead_source_or_validity_censors():
  for field,value in (("leadOne.radar",False),("leadOne.status",False),
                      ("valid",False),("leadOne.vRel",-3.)):
    baseline=_uninterrupted_factory_cruise_fixture()
    original=next(r for r in baseline if r["service"]=="radarState"
                  and r["t"]==round(1.72*NS))
    conflicting=original.copy()
    conflicting[field]=value
    following=evaluate([*baseline,conflicting])["following"]
    assert following["leadFilteringRejections"]["ambiguousRadarTimestamp"]==1
    assert following["qualifyingEpisodeCount"]==0



def test_identical_equal_time_carstate_coalesces_without_extra_follow_weight():
  source=_uninterrupted_factory_cruise_fixture()
  original=next(r for r in source if r["service"]=="carState"
                and r["t"]==round(1.7*NS))
  expected=evaluate(source)["following"]
  observed=evaluate([*source,original.copy()])["following"]
  assert observed["qualifyingEpisodeCount"]==1
  assert observed["validCruiseLeadObservations"]==expected["validCruiseLeadObservations"]
  assert observed["medianObservedGapSeconds"]==expected["medianObservedGapSeconds"]
  assert observed["repeatedCarStateTimestampRows"]==1
  assert observed["ambiguousCarStateTimestampGroups"]==0


def test_conflicting_equal_time_cruise_and_kinematics_never_join_following():
  source=_uninterrupted_factory_cruise_fixture()
  original=next(r for r in source if r["service"]=="carState"
                and r["t"]==round(1.7*NS))
  for field,value in (("cruiseState.enabled",False),("vEgo",30.),
                      ("gasPressed",True),("canValid",False),("segment",31)):
    invalid={**original,field:value}
    for records in ([*source,invalid],[invalid,*source]):
      following=evaluate(records)["following"]
      assert following["repeatedCarStateTimestampRows"]==1
      assert following["ambiguousCarStateTimestampGroups"]==1
      assert following["validCruiseLeadObservations"]==33
      assert following["leadFilteringRejections"]["ambiguousCarStateTimestamp"]==1
      assert following["qualifyingEpisodeCount"]==0
