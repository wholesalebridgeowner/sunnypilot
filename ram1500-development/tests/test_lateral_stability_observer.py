"""Fixture tests for cautious steering reversal observer, not actual lane-centering."""
from ramtools.lateral_stability_observer import analyze_segment, combine, REVERSAL_QUALIFICATION
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

def test_long_neutral_dwell_does_not_bridge_unrelated_turns():
    states,cc,curve,out=route(reversing=False)
    changed=[]
    for i,row in enumerate(states):
        rate = 2.5 if i<100 else 0.0 if i<350 else -2.5
        changed.append(row[:5]+(rate,)+row[6:])
    result=analyze_segment(changed,cc,curve,out)
    assert result["eligibleSamples"]==600
    assert result["stableSignReversals"]==0
    assert result["measurementQualification"]==REVERSAL_QUALIFICATION


def test_brief_neutral_dwell_preserves_real_sign_transition():
    states,cc,curve,out=route(reversing=False)
    changed=[]
    for i,row in enumerate(states):
        rate = 2.5 if i<100 else 0.0 if i<110 else -2.5
        changed.append(row[:5]+(rate,)+row[6:])
    result=analyze_segment(changed,cc,curve,out)
    assert result["stableSignReversals"]==1


def test_equal_clock_lateral_conflict_quarantines_not_order_dependent():
    states,cc,curve,out=route(reversing=False)
    timestamp=2*NS
    contradictory=[(timestamp, False)]
    for controls in (cc+contradictory, contradictory+cc):
        result=analyze_segment(states,controls,curve,out)
        assert result["eligibleSamples"]==599
        assert result["rejections"]["lateral_not_verified"]==1
    # Harmless exact duplicates do not alter the metric.
    assert analyze_segment(states,cc+[(timestamp,True)],curve,out)["eligibleSamples"]==600


def test_equal_clock_output_conflict_quarantines_and_future_cannot_backfill():
    states,cc,curve,out=route(reversing=False)
    timestamp=2*NS
    for outputs in (out+[(timestamp,320)],[(timestamp,320)]+out):
        result=analyze_segment(states,cc,curve,outputs)
        assert result["eligibleSamples"]==599
        assert result["rejections"]["torque_limit_or_unavailable"]==1


def test_incompatible_old_measurement_cannot_be_combined():
    result=analyze_segment(*route())
    bad=dict(result,measurementQualification="old_metric_v1")
    with pytest.raises(ValueError):
        combine([result,bad])


def test_malformed_source_timestamp_fails_closed():
    states,cc,curve,out=route()
    with pytest.raises(ValueError):
        analyze_segment(states,cc+[(True,True)],curve,out)


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
