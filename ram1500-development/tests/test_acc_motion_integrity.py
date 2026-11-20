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


def test_equal_clock_radar_conflict_cannot_select_a_lead_by_input_order():
    states = drive()
    complete = [radar(i*.1) for i in range(31)]
    conflict = radar(1.5, status=False)
    # A conflicting lead/non-lead pair at 1.5s must interrupt the continuous
    # lead run, regardless of which frame was returned first.
    for rows in (complete + [conflict], [conflict] + complete):
        out = analyze(states + rows)
        assert out["quality"]["ambiguousRadarClocks"] == 1
        assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0
        assert out["quality"]["qualifiedEpisodes"] == 0
        assert out["measurementQualification"].endswith("_v2")


def test_equal_clock_radar_conflict_never_resurrects_old_valid_frame():
    rows = drive()
    lead = [radar(0), radar(1.5, status=True), radar(1.5, status=False)]
    out = analyze(rows + lead, max_radar_age_s=2)
    assert out["quality"]["ambiguousRadarClocks"] == 1
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 0


def test_identical_equal_clock_radar_is_folded_without_false_break():
    states = drive()
    radars = [radar(i*.1) for i in range(31)]
    out = analyze(states + radars + [radar(1.5)])
    assert out["quality"]["duplicateRadarRows"] == 1
    assert out["quality"]["ambiguousRadarClocks"] == 0
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 1


def test_conflicting_carstate_clock_breaks_acceleration_jerk_episode():
    rows = drive()
    conflict = state(1.5, aEgo=99.0)
    for states in (rows + [conflict], [conflict] + rows):
        out = analyze(states)
        assert out["quality"]["carStateRows"] == 32
        assert out["quality"]["ambiguousCarStateClocks"] == 1
        assert out["quality"]["excludedRowsByReason"]["ambiguous_carState_clock"] == 1
        assert out["quality"]["qualifiedEpisodes"] == 0
        assert out["byObservedLeadContext"]["lead_unavailable"]["rawJerkPairs"] == 0


def test_duplicate_carstate_keeps_only_one_derivative_sample():
    rows = drive()
    out = analyze(rows + [state(1.5, aEgo=.15)])
    assert out["quality"]["duplicateCarStateRows"] == 1
    assert out["quality"]["uniqueCarStateClocks"] == 31
    assert out["byObservedLeadContext"]["lead_unavailable"]["rawJerkPairs"] == 30


def test_radar_conflict_is_scoped_to_its_segment():
    rows = drive()
    radars = [radar(i*.1, segment=30) for i in range(31)]
    # Two frame values at the SAME time in another segment must not
    # contaminate the matched radar stream of the actual vehicle segment.
    other = [radar(1.5, segment=31), radar(1.5, status=False, segment=31)]
    out = analyze(rows + radars + other)
    assert out["quality"]["ambiguousRadarClocks"] == 1
    assert out["byObservedLeadContext"]["radar_flagged_lead"]["episodeCount"] == 1
