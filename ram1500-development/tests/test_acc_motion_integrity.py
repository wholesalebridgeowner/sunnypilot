"""Synthetic read-only tests: no device, log, VIN, payload or route identifiers."""
from ramtools.acc_motion_integrity import analyze

N = 1_000_000_000

def state(t, **changes):
    row = {"service": "carState", "t": round(t*N), "valid": True,
           "canValid": True, "cruiseState.enabled": True,
           "gasPressed": False, "brakePressed": False,
           "gearShifter": "drive", "vEgo": 20., "aEgo": .1, "segment": 30}
    row.update(changes)
    return row

def radar(t, status=True, flag=True, d=40., valid=True):
    return {"service": "radarState", "t": round(t*N), "valid": valid,
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
    assert not any(k in str(out).lower() for k in ("vin", "gpslocation", "rawpayload"))
