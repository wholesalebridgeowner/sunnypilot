"""Synthetic tests only; no private drive files or vehicle required."""
from ramtools.lane_phase_quality import analyze

N = 1_000_000_000

def row(service, seconds, **fields):
  return {"service": service, "t": round(seconds * N), "valid": True, "segment": 30, **fields}

def cs(t, rate, speed=20, pressed=False, segment=30):
  return row("carState", t, steeringRateDeg=rate, steeringPressed=pressed, vEgo=speed, segment=segment)

def model(t, state, direction="left", valid=True, segment=30):
  return row("drivingModelData", t, **{"meta.laneChangeState": state,
          "meta.laneChangeDirection": direction, "valid": valid, "segment": segment})

def test_entry_and_recenter_are_separate_complete_phases_without_crossing_claim():
  inputs = [model(0,"off"), model(.1,"laneChangeStarting"), model(.2,"laneChangeStarting"),
            model(.3,"laneChangeStarting"), model(.4,"laneChangeStarting"),
            model(.5,"laneChangeFinishing"), model(.6,"laneChangeFinishing"),
            model(.7,"laneChangeFinishing"), model(.8,"laneChangeFinishing"), model(.9,"off")]
  inputs += [cs(t, rate) for t,rate in ((.1,1),(.2,3),(.3,5),(.4,7),(.5,8),(.6,5),(.7,2),(.8,1))]
  report = analyze(inputs)
  start=report["phaseWindows"]["laneChangeStarting"]
  finish=report["phaseWindows"]["laneChangeFinishing"]
  assert start["counts"]["qualifiedWindows"]==1
  assert finish["counts"]["qualifiedWindows"]==1
  assert start["medianDurationSeconds"]==.4
  assert start["p90PeakAbsSteeringRateDegPerSecond"]==7
  assert finish["p90PeakAbsSteeringRateDegPerSecond"]==8
  assert start["p90SteeringRateDerivativeDegPerSecondSquared"]==20
  assert report["physicalLaneBoundaryCrossing"]=="not_observable_from_logged_model_state"
  assert "startSeconds" not in str(report)

def test_model_gap_censors_and_no_false_recenter_completion():
  inputs=[model(.0,"laneChangeStarting"),model(.1,"laneChangeStarting"),
          model(2,"laneChangeFinishing"),model(2.1,"laneChangeFinishing")]
  report=analyze(inputs)
  assert report["phaseWindows"]["laneChangeStarting"]["counts"]=={"censoredByGapOrInvalidModel":1}
  assert report["phaseWindows"]["laneChangeFinishing"]["counts"]=={"censoredAtEnd":1}

def test_invalid_model_breaks_interval_and_invalid_vehicle_or_driver_is_rejected():
  inputs=[model(.1,"laneChangeStarting"),model(.2,"laneChangeStarting",valid=False),
          model(.3,"laneChangeStarting"),model(.4,"laneChangeStarting"),model(.5,"off")]
  inputs += [cs(.3,1),cs(.4,3),cs(.45,4)]
  assert analyze(inputs)["phaseWindows"]["laneChangeStarting"]["counts"]["censoredByGapOrInvalidModel"]==1
  inputs=[model(.1,"laneChangeFinishing"),model(.2,"laneChangeFinishing"),model(.3,"laneChangeFinishing"),model(.4,"off")]
  inputs += [cs(.1,1),cs(.2,2,pressed=True),cs(.3,3)]
  assert analyze(inputs)["phaseWindows"]["laneChangeFinishing"]["counts"]["driverOverrideOrUnknown"]==1
  inputs[-2]["steeringPressed"]=None
  assert analyze(inputs)["phaseWindows"]["laneChangeFinishing"]["counts"]["driverOverrideOrUnknown"]==1

def test_no_future_kinematics_filling_and_no_cross_segment_bridge():
  inputs=[model(.1,"laneChangeStarting"),model(.2,"laneChangeStarting"),model(.4,"off"),
          cs(.4,2),cs(.5,3),cs(.6,4)]
  a=analyze(inputs)["phaseWindows"]["laneChangeStarting"]
  assert a["counts"]["insufficientCarState"]==1
  inputs=[model(.1,"laneChangeStarting"),model(.2,"laneChangeStarting"),model(.4,"off"),
          cs(.1,1),cs(.2,2),cs(.3,3)]
  inputs[5]["t"] += 1_000_000_000
  a=analyze(inputs)["phaseWindows"]["laneChangeStarting"]
  assert a["counts"]["insufficientCarState"]==1

def test_unknown_speed_missing_rates_and_gaps_fail_closed():
  base=[model(0,"laneChangeFinishing"),model(.2,"laneChangeFinishing"),model(.4,"laneChangeFinishing"),model(.6,"off")]
  low=base+[cs(.1,1,speed=2),cs(.3,2,speed=2),cs(.5,3,speed=2)]
  assert analyze(low)["phaseWindows"]["laneChangeFinishing"]["counts"]["lowSpeedExcluded"]==1
  missing=base+[cs(.1,1),cs(.3,2),cs(.5,3)]
  missing[-1]["steeringRateDeg"]=float("nan")
  assert analyze(missing)["phaseWindows"]["laneChangeFinishing"]["counts"]["missingKinematics"]==1
  gap=base+[cs(.01,1),cs(.10,2),cs(.50,3)]
  assert analyze(gap)["phaseWindows"]["laneChangeFinishing"]["counts"]["carStateGap"]==1

def test_invalid_parameters_and_empty():
  assert analyze([])["status"]=="no_model_data"
  for k in ({"max_model_gap_s":0},{"max_car_gap_s":-1},{"min_steering_samples":True},
            {"min_steering_samples":1},{"min_speed_mps":-1}):
    try:analyze([],**k)
    except ValueError:pass
    else:raise AssertionError(k)


def test_model_phase_is_censored_at_recording_segment_boundary():
  # Two unrelated model phases on consecutive R-log segments must never be
  # interpreted as a single completed lane-change entry.
  models=[model(.1,"laneChangeStarting"), model(.2,"laneChangeStarting"),
          model(.3,"laneChangeFinishing",segment=31),
          model(.4,"laneChangeFinishing",segment=31), model(.5,"off",segment=31)]
  cars=[cs(.1,1),cs(.2,2),cs(.25,3),
        cs(.3,4,segment=31),cs(.4,5,segment=31),cs(.45,6,segment=31)]
  report=analyze(models+cars)
  start=report["phaseWindows"]["laneChangeStarting"]
  assert start["counts"]["censoredBySegmentBoundaryOrUnverified"]==1
  assert start["counts"].get("qualifiedWindows",0)==0


def test_unknown_model_segment_censors_prior_phase():
  rows=[model(.1,"laneChangeStarting"),model(.2,"laneChangeStarting",segment=None),
        model(.3,"laneChangeFinishing"),model(.4,"off")]
  rows += [cs(.1,1),cs(.2,2),cs(.3,3)]
  report=analyze(rows)["phaseWindows"]["laneChangeStarting"]
  assert report["counts"]["censoredBySegmentBoundaryOrUnverified"]==1
  assert report["counts"].get("qualifiedWindows",0)==0


def test_mixed_segment_car_states_cannot_qualify_model_phase():
  rows=[model(.1,"laneChangeStarting"), model(.2,"laneChangeStarting"),
        model(.3,"laneChangeStarting"), model(.4,"off")]
  rows += [cs(.1,1),cs(.2,2,segment=31),cs(.3,3)]
  phase=analyze(rows)["phaseWindows"]["laneChangeStarting"]
  assert phase["counts"]["carStateSegmentMismatch"]==1
  assert phase["counts"].get("qualifiedWindows",0)==0


def test_independent_same_segment_model_phases_remain_qualified():
  # A legitimate completed phase in each segment is still counted.
  rows=[model(.1,"laneChangeStarting"),model(.2,"laneChangeStarting"),
        model(.4,"off"), model(.5,"laneChangeStarting",segment=31),
        model(.6,"laneChangeStarting",segment=31),model(.8,"off",segment=31)]
  rows += [cs(.1,1),cs(.2,2),cs(.3,3),
           cs(.5,4,segment=31),cs(.6,5,segment=31),cs(.7,6,segment=31)]
  phase=analyze(rows)["phaseWindows"]["laneChangeStarting"]
  assert phase["counts"]["qualifiedWindows"]==2
  assert phase["counts"].get("censoredBySegmentBoundaryOrUnverified",0)==0


def test_unknown_model_validity_censors_without_completion():
  models=[model(.1,"laneChangeStarting"),model(.2,"laneChangeStarting"),
          model(.3,"laneChangeStarting",valid=None),model(.4,"off")]
  cars=[cs(.1,1),cs(.2,2),cs(.3,3)]
  phase=analyze(models+cars)["phaseWindows"]["laneChangeStarting"]
  assert phase["counts"]["censoredByGapOrInvalidModel"]==1
  assert phase["counts"].get("qualifiedWindows",0)==0


def test_unknown_car_validity_rejects_entire_phase():
  rows=[model(.1,"laneChangeFinishing"),model(.2,"laneChangeFinishing"),
        model(.3,"laneChangeFinishing"),model(.4,"off")]
  rows += [cs(.1,1),cs(.2,2),cs(.3,3)]
  rows[5]["valid"]=None
  phase=analyze(rows)["phaseWindows"]["laneChangeFinishing"]
  assert phase["counts"]["invalidCarState"]==1
  assert phase["counts"].get("qualifiedWindows",0)==0


def test_model_direction_flip_censors_whole_phase_not_two_maneuvers():
  rows=[model(.1,"laneChangeStarting",direction="left"),
        model(.2,"laneChangeStarting",direction="left"),
        model(.3,"laneChangeStarting",direction="right"),
        model(.4,"laneChangeStarting",direction="right"),
        model(.5,"off",direction="none")]
  rows += [cs(.1,1),cs(.2,2),cs(.3,3),cs(.4,4)]
  phase=analyze(rows)["phaseWindows"]["laneChangeStarting"]
  assert phase["counts"]["censoredByDirectionChangeOrUnknown"]==1
  assert phase["counts"].get("qualifiedWindows",0)==0


def test_unknown_start_direction_blocks_phase_until_exit():
  rows=[model(.1,"laneChangeStarting",direction="none"),
        model(.2,"laneChangeStarting",direction="left"),
        model(.3,"laneChangeStarting",direction="left"),
        model(.4,"off",direction="none")]
  rows += [cs(.1,1),cs(.2,2),cs(.3,3)]
  phase=analyze(rows)["phaseWindows"]["laneChangeStarting"]
  assert phase["counts"]["censoredByDirectionChangeOrUnknown"]==1
  assert phase["counts"].get("qualifiedWindows",0)==0


def test_direction_on_exit_does_not_invalidate_stable_right_phase():
  rows=[model(.1,"laneChangeFinishing",direction="right"),
        model(.2,"laneChangeFinishing",direction="right"),
        model(.3,"laneChangeFinishing",direction="right"),
        model(.4,"off",direction="none")]
  rows += [cs(.1,1),cs(.2,2),cs(.3,3)]
  phase=analyze(rows)["phaseWindows"]["laneChangeFinishing"]
  assert phase["counts"]["qualifiedWindows"]==1
  assert phase["qualifiedByDirection"]=={"right":1}
