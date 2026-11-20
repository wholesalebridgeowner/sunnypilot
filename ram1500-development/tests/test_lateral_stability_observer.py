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
