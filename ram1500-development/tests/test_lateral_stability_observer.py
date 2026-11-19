"""Fixture tests for cautious steering reversal observer, not actual lane-centering."""
from ramtools.lateral_stability_observer import analyze_segment, combine
import pytest

NS=1_000_000_000

def route(reversing=True,overrides=None):
    states=[]
    commands=[]
    curves=[]
    outputs=[]
    overrides=overrides or set()
    for i in range(600):
        t=i*10_000_000
        # 1-second constant rate intervals, ±2.5 deg/s, enough for sign-dwell.
        rate=2.5 * (-1 if reversing and i//100%2 else 1)
        states.append((t,True,True,"drive",23.,rate,i in overrides,False))
        commands.append((t,True))
        curves.append((t,0.0001))
        outputs.append((t,20))
    return states,commands,curves,outputs

def test_real_alternating_rate_not_silently_called_lane_pingpong():
    out=analyze_segment(*route())
    assert out["eligibleSamples"]==600
    assert 5.8<out["eligibleContinuousSeconds"]<6
    assert out["stableSignReversals"]>=4
    assert out["reversalsPerMinuteProxy"]>0
    assert "NOT proven lane-center ping-pong" in str(out["limitations"])

def test_steady_rate_no_reversals():
    r=analyze_segment(*route(reversing=False))
    assert r["stableSignReversals"]==0
    assert r["eligibleSamples"]==600

def test_driver_override_breaks_continuity():
    r=analyze_segment(*route(overrides=set(range(200,350))))
    assert r["eligibleSamples"]==450
    assert r["rejections"]["driver_or_fault"]==150
    assert r["eligibleContinuousSeconds"]<5

def test_future_and_stale_source_fails_closed():
    states,cc,curve,out=route()
    a=analyze_segment(states,[(7*NS,True)],curve,out)
    assert a["eligibleSamples"]==0
    assert a["rejections"]["lateral_not_verified"]==600

def test_limits_and_turns_excluded():
    states,cc,curve,out=route()
    high=analyze_segment(states,cc,curve,[(t,320) for t,_ in out])
    assert high["eligibleSamples"]==0
    assert high["rejections"]["torque_limit_or_unavailable"]==600
    corners=analyze_segment(states,cc,[(t,0.004) for t,_ in curve],out)
    assert corners["eligibleSamples"]==0
    assert corners["rejections"]["curvature_not_straight"]==600

def test_combine_never_claims_a_validated_setting():
    rows=[analyze_segment(*route()) for _ in range(2)]
    report=combine(rows)
    assert report["segments"]==2
    assert report["eligibleSamples"]==1200
    assert report["noVehicleWrite"] is True
    assert report["reversalsPerMinuteProxy"] is not None
    assert "physical lane-center error" in str(report["limitations"])

def test_bad_input_raises():
    with pytest.raises(ValueError):
        analyze_segment([(0,True)],[],[],[])


def test_contiguous_exposure_is_reported_separately_from_sample_count():
    out=analyze_segment(*route(reversing=False))
    assert out["contiguousEligibleRuns"]==1
    assert out["runsAtLeastHalfSecond"]==1
    assert out["runsAtLeastOneSecond"]==1
    assert out["runsAtLeastTwoSeconds"]==1
    assert out["longestEligibleRunSeconds"]==5.99
    assert out["exposureSecondsInRunsAtLeastTwoSeconds"]==5.99
    assert out["sustainedReversalObservability"]=="limited_proxy_only"


def test_many_short_exposures_cannot_exclude_ping_pong():
    # 24 separate 220ms runs: plenty of accepted samples but not a single
    # two-second uninterrupted opportunity to observe sustained reversals.
    overrides={i for i in range(600) if i%25>=22}
    out=analyze_segment(*route(overrides=overrides))
    assert out["eligibleSamples"]==528
    assert out["eligibleContinuousSeconds"]>4.8
    assert out["contiguousEligibleRuns"]==24
    assert out["runsAtLeastHalfSecond"]==0
    assert out["runsAtLeastTwoSeconds"]==0
    assert out["longestEligibleRunSeconds"]==0.21
    assert out["exposureSecondsInRunsAtLeastTwoSeconds"]==0
    assert out["stableSignReversals"]==0
    assert out["sustainedReversalObservability"]=="insufficient_contiguous_exposure"


def test_single_bad_sample_splits_an_otherwise_contiguous_run():
    out=analyze_segment(*route(reversing=False,overrides={300}))
    assert out["contiguousEligibleRuns"]==2
    assert out["runsAtLeastTwoSeconds"]==2
    assert out["longestEligibleRunSeconds"]==2.99
    assert out["eligibleContinuousSeconds"]==5.97


def test_time_gap_splits_eligible_runs_even_without_rejected_samples():
    state,control,curve,output=route(reversing=False)
    shift=300_000_000
    def shifted(rows):
        return [(row[0]+(shift if i>=300 else 0), *row[1:])
                for i,row in enumerate(rows)]
    out=analyze_segment(shifted(state),shifted(control),shifted(curve),shifted(output))
    assert out["eligibleSamples"]==600
    assert out["contiguousEligibleRuns"]==2
    assert out["runsAtLeastTwoSeconds"]==2
    assert out["eligibleContinuousSeconds"]==5.98


def test_no_qualified_samples_have_no_exposure_windows():
    state,cc,curve,output=route()
    out=analyze_segment(state,[(7*NS,True)],curve,output)
    assert out["contiguousEligibleRuns"]==0
    assert out["longestEligibleRunSeconds"]==0
    assert out["exposureSecondsInRunsAtLeastTwoSeconds"]==0
    assert out["sustainedReversalObservability"]=="insufficient_contiguous_exposure"


def test_combine_cannot_turn_two_short_runs_into_one_long_run():
    # Summation must preserve segment boundaries: do not infer a long
    # observed exposure from numerous short windows.
    overrides={i for i in range(600) if i%25>=22}
    short=analyze_segment(*route(overrides=overrides))
    merged=combine([short,short])
    assert merged["eligibleSamples"]==1056
    assert merged["contiguousEligibleRuns"]==48
    assert merged["runsAtLeastTwoSeconds"]==0
    assert merged["exposureSecondsInRunsAtLeastTwoSeconds"]==0
    assert merged["longestEligibleRunSeconds"]==0.21
    assert merged["sustainedReversalObservability"]=="insufficient_contiguous_exposure"


def _with_rate_profile(profile):
    """Pure synthetic steering-rate observations; no real CAN or identifiers."""
    states, controls, curves, outputs = route(reversing=False)
    states = [(*row[:5], profile(i), *row[6:]) for i, row in enumerate(states)]
    return states, controls, curves, outputs


def test_long_neutral_rate_does_not_bridge_separated_direction_bouts():
    # 1 s positive, 3.5 s nearly stationary wheel, then 1.5 s negative.
    # Previously the detector counted one false sustained reversal despite
    # the long no-rate interval being a valid uninterrupted exposure.
    out = analyze_segment(*_with_rate_profile(
        lambda i: 2.5 if i < 100 else -2.5 if i >= 450 else 0.0
    ))
    assert out["eligibleSamples"] == 600
    assert out["longestEligibleRunSeconds"] == 5.99
    assert out["stableSignReversals"] == 0


def test_isolated_same_sign_spikes_cannot_fake_250ms_directional_dwell():
    # Only two isolated positive rate samples (3.5 s apart); an otherwise
    # sustained negative portion must not convert them into a reversal.
    out = analyze_segment(*_with_rate_profile(
        lambda i: 2.5 if i in (0, 350) else -2.5 if i >= 400 else 0.0
    ))
    assert out["eligibleSamples"] == 600
    assert out["stableSignReversals"] == 0


def test_short_zero_crossing_between_confirmed_opposing_bouts_is_detected():
    # A short 200 ms neutral-rate crossing may separate two actual
    # directional bouts. Both have >250 ms of consecutive valid samples.
    out = analyze_segment(*_with_rate_profile(
        lambda i: 2.5 if i < 150 else -2.5 if i >= 170 else 0.0
    ))
    assert out["eligibleSamples"] == 600
    assert out["stableSignReversals"] == 1
    assert out["sustainedReversalObservability"] == "limited_proxy_only"
    assert "Neutral steering-rate" in str(combine([out])["limitations"])
