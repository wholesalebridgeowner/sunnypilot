"""Offline synthetic tests: a parking CAN correlation needs independent activations."""
import pytest
from ramtools.park_replication import replication_audit


def records(states, *, gear="reverse", packet_hz=20):
    out=[]
    for start, active, n in states:
        for i in range(n):
            t=start+i/packet_hz
            data=bytes((i%2 if active else 0, 7, 3, 9, 0, 0))
            out.append((t,gear,active,data))
    return sorted(out,key=lambda r:r[0])


def test_one_real_parking_episode_does_not_count_as_repeated_trials():
    result=replication_audit(records([(0,False,60),(5,True,300),(30,False,60)]))
    reverse=result["gearResults"][0]
    assert reverse["activeIndependentRuns"]==1
    assert reverse["inactiveIndependentRuns"]==2
    assert reverse["replicated"] is False
    assert reverse["reason"]=="insufficient_independent_state_runs"
    assert reverse["activeMeanRunChangeFraction"]==1.0


def test_repeated_factory_activations_can_pass_only_repetition_not_causal_proof():
    cases=[(0,False,60),(5,True,60),(10,False,60),(15,True,60)]
    result=replication_audit(records(cases))
    assert result["gearResults"][0]["replicated"] is True
    assert result["status"]=="observational_not_an_actuator_decoder"
    assert "not prove" in " ".join(result["limitations"])


def test_gaps_in_one_active_state_cannot_become_fake_replicates():
    # A missing CAN interval does not indicate a second OEM parking activation.
    cases=[(0,False,60),(5,True,50),(12,True,50),(20,False,60)]
    result=replication_audit(records(cases))
    assert result["gearResults"][0]["activeIndependentRuns"]==1
    assert result["gearResults"][0]["replicated"] is False


def test_gear_switch_does_not_create_second_factory_activation():
    data=records([(0,False,60),(5,True,60)],gear="reverse")
    data += records([(10,True,60)],gear="drive")
    data += records([(15,False,60)],gear="drive")
    result=replication_audit(sorted(data))
    assert result["gearResults"][0]["activeIndependentRuns"]==1
    assert result["gearResults"][1]["activeIndependentRuns"]==1
    assert all(not row["replicated"] for row in result["gearResults"])


def test_invalid_nonfinite_time_and_unsorted_events_fail_closed():
    with pytest.raises(ValueError):
        replication_audit([(float("nan"),"reverse",True,b"123")])
    with pytest.raises(ValueError):
        replication_audit([(2,"reverse",False,b"123"),(1,"reverse",True,b"123")])
    with pytest.raises(ValueError):
        replication_audit([(1,"reverse",1,b"123")])
    with pytest.raises(ValueError):
        replication_audit([],min_replicates=1)


def test_short_or_one_frame_fragments_do_not_count():
    result=replication_audit(records([(0,False,2),(5,True,2),(10,False,2),(15,True,2)]))
    assert all(not x["replicated"] for x in result["gearResults"])
    assert result["gearResults"][0]["activeIndependentRuns"]==0
