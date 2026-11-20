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
