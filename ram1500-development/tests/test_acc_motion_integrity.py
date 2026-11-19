"""Synthetic read-only tests: no device, log, VIN, payload or route identifiers."""
from ramtools.acc_motion_integrity import analyze
from ramtools.input_scope import require_one_route

N = 1_000_000_000

def state(t, **changes):
    row = {"service": "carState", "t": round(t*N), "valid": True,
           "canValid": True, "cruiseState.enabled": True,
           "gasPressed": False, "brakePressed": False,
           "gearShifter": "drive", "vEgo": 20., "aEgo": .1, "segment": 30}
    row.update(changes)
    return row

def radar(t, status=True, flag=True, d=40., valid=True, segment=30):
    return {"service": "radarState", "t": round(t*N), "valid": valid,
            "segment": segment,
            "leadOne.status": status, "leadOne.radar": flag, "leadOne.dRel": d}

def drive(t0=0, count=31, step=.1):
    return [state(t0+i*step, aEgo=round(i*step*.1, 4)) for i in range(count)]

def test_qualified_cruise_motion_is_observed_not_throttle():
    out = analyze(drive())
    stats = out["byObservedLeadContext"]["lead_unavailable"]
    assert stats["episodeCount"] == 1
    assert stats["durationSeconds"] == 3.
    assert stats["accelerationSamples"] == 31
    assert stats["rawJerkPairs"] == 30
    assert stats["p90AbsoluteRawJerkMps3"] == .1
    assert stats["smoothedWindowJerkPairs"] >= 3
    assert "not_throttle" in out["status"]
    assert "aEgo" in " ".join(out["limits"])
    assert not any("segment" in k.lower() or "timestamp" in k.lower() for k in stats)

def test_pedal_and_cruise_rejections_split_short_bursts():
    for key, value in (("brakePressed", True), ("gasPressed", True),
                       ("cruiseState.enabled", False), ("canValid", False)):
        rows = drive(0,12) + [state(1.2, **{key: value})] + drive(1.3,12)
        out = analyze(rows)
        assert out["quality"]["qualifiedEpisodes"] == 0, key
        assert out["quality"]["continuousCandidateRuns"] == 2
        assert sum(out["quality"]["excludedRowsByReason"].values()) == 1

def test_segment_and_missing_time_never_bridge():
    rows = drive(0,12) + drive(1.3,12)
    for r in rows[12:]:
        r["segment"] = 31
    assert analyze(rows)["quality"]["qualifiedEpisodes"] == 0
    assert analyze(drive(0,12) + drive(2.0,12))["quality"]["qualifiedEpisodes"] == 0

def test_invalid_radar_cannot_carry_forward_old_confirmed_lead():
    rows = drive()
    rows += [radar(i*.1) for i in range(31) if i != 15]
    rows += [radar(1.5, valid=False)]
    out = analyze(rows)
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0
    assert out["quality"]["continuousCandidateRuns"] >= 3

def test_future_radar_never_backfills_present():
    out = analyze(drive() + [radar(2.0)])
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0
    assert out["byObservedLeadContext"]["lead_unavailable"]["episodeCount"] == 0

def test_source_flag_preserved_not_equated_with_oem_acc_target():
    out = analyze(drive() + [radar(i*.1, flag=False) for i in range(31)])
    assert out["byObservedLeadContext"]["model_flagged_lead"]["episodeCount"] == 1
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0
    assert "factory ACC" in " ".join(out["limits"])
    assert "raw CAN" in " ".join(out["limits"])

def test_unknown_sensor_or_gear_rejected_instead_of_interpolated():
    for key, value in (("gasPressed", None), ("brakePressed", None),
                       ("gearShifter", "reverse"), ("aEgo", float("nan")),
                       ("vEgo", 0), ("valid", False), ("segment", None)):
        rows = drive()
        rows[15][key] = value
        assert analyze(rows)["quality"]["qualifiedEpisodes"] == 0, key

def test_duplicate_timestamps_cannot_form_zero_dt_derivative():
    rows = drive()
    rows.insert(15, state(1.4, aEgo=500.))
    assert analyze(rows)["quality"]["qualifiedEpisodes"] == 0

def test_invalid_thresholds_fail_closed():
    for kw in ({"max_state_gap_s": float("nan")}, {"smooth_window_s": False},
               {"min_episode_samples": True}, {"min_smooth_samples": 1}):
        try:
            analyze([], **kw)
        except ValueError:
            pass
        else:
            raise AssertionError(kw)

def test_short_episodes_excluded_and_no_identifying_detail():
    out = analyze(drive(0,15))
    assert out["quality"]["qualifiedEpisodes"] == 0
    assert out["byObservedLeadContext"]["lead_unavailable"]["durationSeconds"] == 0.
    # A privacy caveat may literally mention "VIN" or "GPS": inspect structured\n    # exported keys, not warning prose, to avoid false-positive leak tests.\n    for section in (out, out["quality"], *out["byObservedLeadContext"].values()):\n        assert not {"vin", "gpsLocation", "rawpayload", "route", "segment", "t"}.intersection(section)

def test_radar_does_not_cross_segment_even_if_recent():
    # Reused segment 30 radar timestamps must not label segment 31 states.
    states = [{**r, "segment": 31} for r in drive()]
    other_segment = [radar(i*.1, segment=30) for i in range(31)]
    out = analyze(states + other_segment)
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0
    assert out["byObservedLeadContext"]["lead_unavailable"]["episodeCount"] == 1
    assert out["quality"]["qualifiedEpisodes"] == 1


def test_same_timestamp_wrong_segment_cannot_shadow_matching_radar():
    # Deliberately place wrong-segment rows AFTER matching rows at equal t.
    states = [{**r, "segment": 31} for r in drive()]
    radar_rows = []
    for i in range(31):
        radar_rows += [radar(i*.1, flag=False, segment=31),
                       radar(i*.1, flag=True, segment=30)]
    out = analyze(states + radar_rows)
    assert out["byObservedLeadContext"]["model_flagged_lead"]["episodeCount"] == 1
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0


def test_radar_without_segment_is_unavailable_not_joinable():
    unknown = [radar(i*.1, segment=None) for i in range(31)]
    out = analyze(drive() + unknown)
    assert out["quality"]["unscopedRadarRows"] == 31
    assert out["byObservedLeadContext"]["lead_unavailable"]["episodeCount"] == 1
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0

def test_one_route_gate_accepts_multiple_segments_of_same_route():
    first = "00000000--aaaaaaaaaa--29/rlog.zst"
    second = "00000000--aaaaaaaaaa--30/rlog.zst"
    assert len(require_one_route([first, second])) == 2


def test_one_route_gate_rejects_other_routes_or_missing_provenance():
    good = "00000000--aaaaaaaaaa--29/rlog.zst"
    wrong_route = "00000000--bbbbbbbbbb--30/rlog.zst"
    for paths in ([good, wrong_route], [good, "unknown.rlog.zst"], []):
        try:
            require_one_route(paths)
        except ValueError as exc:
            # Privacy gate messages should never include route tokens.
            assert "aaaaaaaaaa" not in str(exc)
            assert "bbbbbbbbbb" not in str(exc)
        else:
            raise AssertionError("Mixed or unidentified route accepted")


def _lead_observed_drive():
    """Three seconds of synthetic cruise and same-segment model-flagged lead."""
    return drive() + [radar(i*.1, flag=False) for i in range(31)]


def test_repeated_identical_radar_observation_does_not_inflate_motion():
    baseline = _lead_observed_drive()
    original = next(x for x in baseline if x["service"] == "radarState"
                    and x["t"] == round(1.5*N))
    expected = analyze(baseline)
    actual = analyze([*baseline, original.copy()])
    assert expected["byObservedLeadContext"] == actual["byObservedLeadContext"]
    assert actual["quality"]["qualifiedEpisodes"] == 1
    assert actual["quality"]["repeatedRadarTimestampRows"] == 1
    assert actual["quality"]["ambiguousRadarTimestampGroups"] == 0


def test_conflicting_same_time_radar_flags_censor_both_record_orders():
    baseline = _lead_observed_drive()
    original = next(x for x in baseline if x["service"] == "radarState"
                    and x["t"] == round(1.5*N))
    contradictory = {**original, "leadOne.radar": True}
    for data in ([*baseline, contradictory],
                 [contradictory, *baseline]):
        result = analyze(data)
        assert result["quality"]["repeatedRadarTimestampRows"] == 1
        assert result["quality"]["ambiguousRadarTimestampGroups"] == 1
        assert result["byObservedLeadContext"]["model_flagged_lead"]["episodeCount"] == 0
        assert result["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0
        assert result["quality"]["qualifiedEpisodes"] == 0


def test_same_time_radar_validity_conflict_cannot_backfill_prior_lead():
    baseline = _lead_observed_drive()
    original = next(x for x in baseline if x["service"] == "radarState"
                    and x["t"] == round(1.5*N))
    invalid = {**original, "valid": False}
    for data in ([*baseline, invalid], [invalid, *baseline]):
        stats = analyze(data)
        assert stats["quality"]["ambiguousRadarTimestampGroups"] == 1
        assert stats["quality"]["qualifiedEpisodes"] == 0


def test_same_time_relative_velocity_disagreement_is_not_silently_accepted():
    baseline = _lead_observed_drive()
    original = next(x for x in baseline if x["service"] == "radarState"
                    and x["t"] == round(1.5*N))
    original["leadOne.vRel"] = -1.
    inconsistent = {**original, "leadOne.vRel": -3.}
    stats = analyze([*baseline, inconsistent])
    assert stats["quality"]["ambiguousRadarTimestampGroups"] == 1
    assert stats["quality"]["qualifiedEpisodes"] == 0



def test_identical_equal_time_carstate_coalesces_without_inflating_motion():
    source = _lead_observed_drive()
    original = next(r for r in source if r["service"] == "carState"
                    and r["t"] == round(1.5*N))
    expected = analyze(source)
    result = analyze([*source, original.copy()])
    assert result["byObservedLeadContext"] == expected["byObservedLeadContext"]
    assert result["quality"]["qualifiedEpisodes"] == 1
    assert result["quality"]["repeatedCarStateTimestampRows"] == 1
    assert result["quality"]["ambiguousCarStateTimestampGroups"] == 0


def test_conflicting_equal_time_carstate_censors_both_insert_orders():
    source = _lead_observed_drive()
    original = next(r for r in source if r["service"] == "carState"
                    and r["t"] == round(1.5*N))
    for field, different in (("cruiseState.enabled", False),
                             ("aEgo", 100.), ("canValid", False),
                             ("segment", 31), ("gasPressed", True)):
        contradiction = {**original, field: different}
        for data in ([*source, contradiction], [contradiction, *source]):
            result = analyze(data)
            assert result["quality"]["repeatedCarStateTimestampRows"] == 1
            assert result["quality"]["ambiguousCarStateTimestampGroups"] == 1
            assert result["quality"]["qualifiedEpisodes"] == 0
            assert result["quality"]["excludedRowsByReason"]["invalid_vehicle_or_can"] == 1
            assert result["byObservedLeadContext"]["model_flagged_lead"]["episodeCount"] == 0
