"""Synthetic tests for joined lane-change phases; no real vehicle records."""
import copy

from ramtools.lane_maneuver_sequences import analyze

NS = 1_000_000_000


def model(t, state, direction="left", segment=30, valid=True):
    return {"service": "drivingModelData", "t": round(t * NS),
            "segment": segment, "valid": valid,
            "meta.laneChangeState": state,
            "meta.laneChangeDirection": direction}


def cc(t, active=True, segment=30, valid=True):
    return {"service": "carControl", "t": round(t * NS), "valid": valid,
            "latActive": active, "segment": segment}


def route(segment=30):
    rows = [model(0, "off", segment=segment),
            model(.1, "laneChangeStarting", segment=segment),
            model(.2, "laneChangeStarting", segment=segment),
            model(.3, "laneChangeFinishing", segment=segment),
            model(.4, "laneChangeFinishing", segment=segment),
            model(.5, "off", "none", segment=segment)]
    rows += [cc(round(i * .05, 2), segment=segment) for i in range(11)]
    return rows


def test_completed_model_entry_and_recenter_with_verified_ownership():
    result = analyze(route())
    assert result["observedCompleteSequences"] == 1
    assert result["qualifiedOwnedSequences"] == 1
    assert result["medianStartingPhaseSeconds"] == .2
    assert result["medianFinishingPhaseSeconds"] == .2
    assert result["medianCompleteSequenceSeconds"] == .4
    assert result["qualifiedByDirection"] == {"left": 1}
    assert result["physicalLaneCrossingVerified"] is False
    assert result["physicalLaneCenterMeasured"] is False
    assert "segment" not in result


def test_shuffled_and_identical_duplicates_do_not_change_results():
    original = route()
    shuffled = list(reversed(original)) + [dict(original[2]), dict(original[-1])]
    assert analyze(original) == analyze(shuffled)


def test_conflicting_same_time_model_state_quarantines_sequence():
    rows = route()
    rows.append(model(.3, "laneChangeStarting"))
    assert analyze(rows)["qualifiedOwnedSequences"] == 0
    assert analyze(list(reversed(rows)))["qualifiedOwnedSequences"] == 0


def test_lateral_inactive_even_between_model_edges_is_rejected():
    rows = route()
    rows.append(cc(.25, False))
    res = analyze(rows)
    assert res["observedCompleteSequences"] == 1
    assert res["qualifiedOwnedSequences"] == 0
    assert res["exclusions"]["lateralOwnershipUnverified"] == 1


def test_missing_or_stale_control_never_creates_qualified_sequence():
    rows = [r for r in route() if r["service"] != "carControl"]
    assert analyze(rows)["qualifiedOwnedSequences"] == 0
    rows += [cc(.1), cc(.5)]
    assert analyze(rows, max_control_age_s=.05)["qualifiedOwnedSequences"] == 0


def test_model_gap_invalid_sample_and_segment_boundary_censor():
    for edit in ("gap", "invalid", "segment"):
        rows = route()
        m = [r for r in rows if r["service"] == "drivingModelData"]
        if edit == "gap":
            m[3]["t"] += 2 * NS
        elif edit == "invalid":
            m[3]["valid"] = False
        else:
            m[3]["segment"] = 31
        assert analyze(rows)["qualifiedOwnedSequences"] == 0


def test_unanchored_partial_maneuver_is_never_completed():
    rows = route()
    rows = [r for r in rows if not (r["service"] == "drivingModelData" and
                                    r["meta.laneChangeState"] == "off" and r["t"] == 0)]
    assert analyze(rows)["qualifiedOwnedSequences"] == 0


def test_direction_flip_and_regressed_model_phase_are_censored():
    rows = route()
    next(r for r in rows if r["service"] == "drivingModelData"
         and r["t"] == round(.4 * NS))["meta.laneChangeDirection"] = "right"
    assert analyze(rows)["qualifiedOwnedSequences"] == 0
    rows = route()
    next(r for r in rows if r["service"] == "drivingModelData"
         and r["t"] == round(.4 * NS))["meta.laneChangeState"] = "laneChangeStarting"
    assert analyze(rows)["qualifiedOwnedSequences"] == 0


def test_unknown_state_requires_fresh_neutral_anchor():
    rows = route()
    rows[1]["meta.laneChangeState"] = "unknown"
    assert analyze(rows)["qualifiedOwnedSequences"] == 0
    later = route()
    for r in later:
        r["t"] += 2 * NS
    assert analyze(rows + later)["qualifiedOwnedSequences"] == 1


def test_two_independent_same_clock_segments_cannot_be_joined():
    rows = route()
    m = [r for r in rows if r["service"] == "drivingModelData"]
    m[3]["segment"] = 31
    m[4]["segment"] = 31
    m[5]["segment"] = 31
    assert analyze(rows)["qualifiedOwnedSequences"] == 0


def test_invalid_timestamps_and_timing_parameters_fail_closed():
    rows = route()
    rows[0]["t"] = True
    assert analyze(rows)["status"] == "invalid_recording_clock"
    for kw in ({"max_model_gap_s": True}, {"max_control_age_s": 0},
               {"max_control_age_s": float("nan")}, {"max_model_gap_s": 3}):
        try:
            analyze(route(), **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(kw)


def car(t, rate, *, segment=30, speed=18.0):
    return {"service": "carState", "t": round(t * NS), "valid": True,
            "segment": segment, "vEgo": speed, "steeringRateDeg": rate,
            "steeringPressed": False, "canValid": True,
            "steerFaultTemporary": False, "steerFaultPermanent": False}


def joined_with_car():
    return route() + [car(.1, 1), car(.2, 3), car(.3, 5), car(.4, 2)]


def test_paired_observed_steering_envelope_requires_both_phases():
    res = analyze(joined_with_car())
    assert res["qualifiedOwnedSequences"] == 1
    assert res["qualifiedHealthyKinematicSequences"] == 1
    assert res["p90PeakAbsSteeringRateDegPerSecond"] == 5
    assert res["p90PeakWheelRateDerivativeDegPerSecondSquared"] == 30
    assert res["physicalLaneCrossingVerified"] is False
    assert res["physicalLaneCenterMeasured"] is False


def test_no_vehicle_samples_never_implies_good_lane_kinematics():
    res = analyze(route())
    assert res["qualifiedOwnedSequences"] == 1
    assert res["qualifiedHealthyKinematicSequences"] == 0
    assert res["exclusions"]["kinematicInsufficientTwoPhaseSamples"] == 1
    assert res["p90PeakAbsSteeringRateDegPerSecond"] is None


def test_intervention_eps_and_can_health_censor_entire_sequence():
    for key, value, reason in (
        ("steeringPressed", True, "kinematicInterventionOrEpsFault"),
        ("steeringPressed", None, "kinematicInterventionOrEpsFault"),
        ("steerFaultTemporary", True, "kinematicInterventionOrEpsFault"),
        ("steerFaultPermanent", None, "kinematicInterventionOrEpsFault"),
        ("canValid", False, "kinematicSegmentOrCanInvalid"),
        ("valid", 1, "kinematicSegmentOrCanInvalid"),
        ("segment", 31, "kinematicSegmentOrCanInvalid"),
    ):
        rows = joined_with_car()
        next(r for r in rows if r["service"] == "carState" and
             r["t"] == round(.3 * NS))[key] = value
        result = analyze(rows)
        assert result["qualifiedOwnedSequences"] == 1
        assert result["qualifiedHealthyKinematicSequences"] == 0
        assert result["exclusions"][reason] == 1


def test_sparse_low_speed_and_nonfinite_car_cannot_qualify():
    rows = [r for r in joined_with_car() if not
            (r["service"] == "carState" and r["t"] == round(.3 * NS))]
    assert analyze(rows)["exclusions"]["kinematicInsufficientTwoPhaseSamples"] == 1
    rows = joined_with_car()
    next(r for r in rows if r["service"] == "carState")["vEgo"] = 1.0
    assert analyze(rows)["exclusions"]["kinematicLowSpeed"] == 1
    rows = joined_with_car()
    next(r for r in rows if r["service"] == "carState")["steeringRateDeg"] = float("nan")
    assert analyze(rows)["exclusions"]["kinematicMissingOrNonfinite"] == 1
    rows = joined_with_car()
    assert analyze(rows, max_car_gap_s=.05)["exclusions"]["kinematicUnverifiedCoverage"] == 1


def test_equal_clock_carstate_conflict_is_order_invariant_and_private():
    import json
    base = joined_with_car()
    expected = analyze(base)
    assert analyze(list(reversed(base)) + [dict(next(r for r in base if r["service"] == "carState"))]) == expected
    collision = dict(next(r for r in base if r["service"] == "carState"),
                     steeringRateDeg=100, vin="TEST_PRIVATE_VIN", location="TEST_PRIVATE_GPS")
    for rows in (base + [collision], list(reversed(base + [collision]))):
        result = analyze(rows)
        assert result["qualifiedHealthyKinematicSequences"] == 0
        assert result["exclusions"]["kinematicSegmentOrCanInvalid"] == 1
        assert "TEST_PRIVATE_" not in json.dumps(result)


def test_kinematic_outputs_not_derived_from_invalid_clock_or_future_states():
    rows = joined_with_car()
    rows.append(dict(next(r for r in rows if r["service"] == "carState"), t=True))
    assert analyze(rows)["status"] == "invalid_recording_clock"
    rows = route() + [car(.6, 5), car(.7, 6), car(.8, 7), car(.9, 8)]
    assert analyze(rows)["qualifiedHealthyKinematicSequences"] == 0
    for kw in ({"max_car_gap_s": 0}, {"max_car_gap_s": float("nan")},
               {"min_speed_mps": True}, {"min_speed_mps": float("inf")}):
        try:
            analyze(joined_with_car(), **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(kw)


def complete_with_post_exit():
    """Anonymous synthetic continuous, laterally-active neutral model interval."""
    rows = joined_with_car()
    rows += [model(round(.6 + i * .1, 2), "off", "none")
             for i in range(10)]  # neutral through 1.5s
    rows += [cc(round(.55 + i * .05, 2)) for i in range(20)]
    rows += [car(round(.5 + i * .1, 2),
                 2.0 if i < 8 else .2) for i in range(10)]
    return rows


def test_post_exit_wheel_rate_requires_continuous_owned_neutral_model():
    result = analyze(complete_with_post_exit())
    assert result["qualifiedOwnedSequences"] == 1
    assert result["qualifiedHealthyKinematicSequences"] == 1
    assert result["qualifiedPostExitWheelObservations"] == 1
    assert result["postExitWindowSeconds"] == 1.0
    assert result["p90PeakPostExitAbsSteeringRateDegPerSecond"] == 2.0
    assert result["medianPostExitTailAbsSteeringRateDegPerSecond"] == .2
    assert result["physicalLaneCenterMeasured"] is False
    assert result["physicalLaneCrossingVerified"] is False
    assert "segment" not in result


def test_post_exit_missing_future_model_or_car_does_not_mean_settled():
    base = joined_with_car()
    assert analyze(base)["qualifiedPostExitWheelObservations"] == 0
    assert analyze(base)["p90PeakPostExitAbsSteeringRateDegPerSecond"] is None
    rows = complete_with_post_exit()
    rows = [r for r in rows if r["service"] != "carState" or r["t"] < .9 * NS]
    result = analyze(rows)
    assert result["qualifiedOwnedSequences"] == 1
    assert result["qualifiedPostExitWheelObservations"] == 0
    assert result["exclusions"]["postExitCarCoverageUnverified"] == 1


def test_post_exit_model_reentry_and_unverified_control_fail_closed():
    rows = complete_with_post_exit()
    rows.append(model(1.0, "laneChangeStarting"))
    for order in (rows, list(reversed(rows))):
        res = analyze(order)
        assert res["qualifiedPostExitWheelObservations"] == 0
        assert res["exclusions"]["postExitModelNotNeutral"] == 1
    rows = complete_with_post_exit()
    rows.append(cc(.85, False))
    result = analyze(rows)
    assert result["qualifiedPostExitWheelObservations"] == 0
    assert result["exclusions"]["postExitLateralOwnershipUnverified"] == 1


def test_post_exit_vehicle_intervention_and_wrong_segment_are_rejected():
    rows = complete_with_post_exit()
    for r in rows:
        if r["service"] == "carState" and r["t"] == 1 * NS:
            r["steeringPressed"] = True
    assert analyze(rows)["exclusions"]["postExitVehicleInvalidOrIntervention"] == 1
    rows = complete_with_post_exit()
    for r in rows:
        if r["service"] == "carState" and r["t"] == 1 * NS:
            r["segment"] = 31
    assert analyze(rows)["qualifiedPostExitWheelObservations"] == 0


def test_post_exit_validation_and_privacy():
    import json
    rows = complete_with_post_exit()
    rows[0]["location"] = "PRIVATE_LOCATION_DO_NOT_EXPORT"
    rows[0]["vin"] = "PRIVATE_VIN_DO_NOT_EXPORT"
    assert "PRIVATE_" not in json.dumps(analyze(rows))
    for val in (0, -1, True, float("nan"), float("inf"), 4):
        try:
            analyze(rows, post_exit_seconds=val)
        except ValueError:
            pass
        else:
            raise AssertionError(val)


def test_phase_transition_wheel_rate_spike_must_count_in_full_maneuver():
    # Two perfectly steady phases, but one abrupt change at the observed
    # model-state boundary. Previously reported a misleading zero derivative.
    rows = joined_with_car()
    for r in rows:
        if r["service"] == "carState":
            r["steeringRateDeg"] = 1 if r["t"] <= round(.2 * NS) else 31
    result = analyze(rows)
    assert result["qualifiedHealthyKinematicSequences"] == 1
    assert result["p90PeakWheelRateDerivativeDegPerSecondSquared"] == 300
    assert result["p90PeakPhaseTransitionWheelRateDerivativeDegPerSecondSquared"] == 300
    assert result["wheelRateDerivativeDefinition"] == "phase_boundary_inclusive_v2"
    assert result["physicalLaneCrossingVerified"] is False
    assert result["physicalLaneCenterMeasured"] is False
    assert analyze(list(reversed(rows))) == result


def test_phase_transition_gap_never_estimates_spurious_slope():
    rows = joined_with_car()
    for r in rows:
        if r["service"] == "carState" and r["t"] == round(.3 * NS):
            r["t"] = round(.36 * NS)  # 160 ms across phase boundary > 150 ms max
            r["steeringRateDeg"] = 400
    result = analyze(rows)
    assert result["qualifiedHealthyKinematicSequences"] == 0
    assert result["exclusions"]["kinematicUnverifiedCoverage"] == 1
    assert result["p90PeakWheelRateDerivativeDegPerSecondSquared"] is None
    assert result["p90PeakPhaseTransitionWheelRateDerivativeDegPerSecondSquared"] is None


def test_phase_transition_collision_or_fault_has_no_good_score_and_no_private_export():
    import json
    for edit in ("collision", "fault"):
        rows = joined_with_car()
        pivot = next(r for r in rows if r["service"] == "carState"
                     and r["t"] == round(.3 * NS))
        if edit == "collision":
            rows.append(dict(pivot, steeringRateDeg=999, vin="SECRET_VIN"))
        else:
            pivot["steerFaultPermanent"] = True
        result = analyze(rows)
        assert result["qualifiedHealthyKinematicSequences"] == 0
        assert result["p90PeakPhaseTransitionWheelRateDerivativeDegPerSecondSquared"] is None
        assert "SECRET_VIN" not in json.dumps(result)


def test_post_exit_requires_healthy_full_maneuver_not_just_a_good_tail():
    # A healthy later wheel-rate trace does not make an earlier interrupted
    # maneuver a qualified Comma comfort observation. No private data export.
    import json
    for edit in ("driver", "can", "eps", "missing"):
        rows = complete_with_post_exit()
        pivot = next(r for r in rows if r["service"] == "carState"
                     and r["t"] == round(.3 * NS))
        if edit == "driver":
            pivot["steeringPressed"] = True
        elif edit == "can":
            pivot["canValid"] = False
        elif edit == "eps":
            pivot["steerFaultTemporary"] = True
        else:
            rows.remove(pivot)
        rows[0]["vin"] = "PRIVATE_TEST_VIN"
        for order in (rows, list(reversed(rows))):
            result = analyze(order)
            assert result["qualifiedOwnedSequences"] == 1
            assert result["qualifiedHealthyKinematicSequences"] == 0
            assert result["qualifiedPostExitWheelObservations"] == 0
            assert result["exclusions"]["postExitManeuverKinematicsUnqualified"] == 1
            assert result["p90PeakPostExitAbsSteeringRateDegPerSecond"] is None
            assert result["medianPostExitTailAbsSteeringRateDegPerSecond"] is None
            assert "PRIVATE_TEST_VIN" not in json.dumps(result)


def test_post_exit_prechange_reentry_censors_settling_proxy():
    # The model may start preparing another maneuver without entering its
    # starting phase. preLaneChange is an anchor, NOT a settled off-only tail.
    rows = complete_with_post_exit()
    pre = next(r for r in rows if r["service"] == "drivingModelData"
               and r["t"] == round(.9 * NS))
    pre["meta.laneChangeState"] = "preLaneChange"
    pre["meta.laneChangeDirection"] = "left"
    for order in (rows, list(reversed(rows))):
        result = analyze(order)
        assert result["qualifiedOwnedSequences"] == 1
        assert result["qualifiedHealthyKinematicSequences"] == 1
        assert result["qualifiedPostExitWheelObservations"] == 0
        assert result["exclusions"]["postExitModelNotNeutral"] == 1
        assert result["p90PeakPostExitAbsSteeringRateDegPerSecond"] is None
        assert result["postExitObservationDefinition"] == "healthy_maneuver_off_only_v2"


def test_post_exit_unmodified_healthy_maneuver_remains_qualified():
    result = analyze(complete_with_post_exit())
    assert result["qualifiedHealthyKinematicSequences"] == 1
    assert result["qualifiedPostExitWheelObservations"] == 1
    assert result["p90PeakPostExitAbsSteeringRateDegPerSecond"] == 2.0
    assert result["postExitObservationDefinition"] == "healthy_maneuver_off_only_v2"
