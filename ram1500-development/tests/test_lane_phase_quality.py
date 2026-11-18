"""Synthetic tests only; no private drive files or vehicle required."""
from ramtools.lane_phase_quality import analyze

N = 1_000_000_000

def row(service, seconds, **fields):
  return {"service": service, "t": round(seconds * N), "valid": True, **fields}

def cs(t, rate, speed=20, pressed=False):
  return row("carState", t, steeringRateDeg=rate, steeringPressed=pressed, vEgo=speed)

def model(t, state, direction="left", valid=True):
  return row("drivingModelData", t, **{"meta.laneChangeState": state,
          "meta.laneChangeDirection": direction, "valid": valid})

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
