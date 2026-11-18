"""Offline-only parameter proposal must not become an unreviewed vehicle write."""
import copy
import json
from pathlib import Path

import pytest

from ramtools.parameter_proposal import validate


@pytest.fixture
def proposal():
    p=Path(__file__).resolve().parents[1]/"proposed_parameters.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_complete_exact_truck_baseline_is_noninstallable(proposal):
    result=validate(proposal)
    assert result["candidateSlots"]==7
    assert result["vehicleWriteReady"] is False
    assert result["status"]=="validated_offline_proposal_only"


@pytest.mark.parametrize("id,value",[
    ("latAccelFactor",2.3),("friction",.08),
    ("steerActuatorDelaySeconds",.32),
    ("followingGapSeconds",1.0),
    ("steeringTorqueAndRateLimits",{"steerMax":400}),
    ("lowSpeedLkasEnable",{"minEnableSpeedMps":0}),
    ("laneChangeComfortTuning",{"aggressiveness":1.25}),
])
def test_unvalidated_values_cannot_become_writeable(proposal,id,value):
    trial=copy.deepcopy(proposal)
    next(x for x in trial["candidateParameters"] if x["id"]==id)["value"]=value
    with pytest.raises(ValueError,match="null"):
        validate(trial)


@pytest.mark.parametrize("field,value",[
    ("epsFirmware","68466110AA"),
    ("installedSunnypilotCommit","unknown"),
    ("vehicleSoftwareModified",True),
])
def test_wrong_source_or_false_claim_rejected(proposal,field,value):
    trial=copy.deepcopy(proposal)
    trial["source"][field]=value
    with pytest.raises(ValueError):
        validate(trial)


@pytest.mark.parametrize("key,value",[
    ("minEnableSpeedMps",0),
    ("minSteerSpeedMps",0),
    ("openpilotLongitudinalControl",True),
    ("liveTorqueParametersEnabled",True),
    ("latAccelFactor",3.0)
])
def test_baseline_cannot_be_silently_modified(proposal,key,value):
    trial=copy.deepcopy(proposal)
    trial["observedBaseline"][key]=value
    with pytest.raises(ValueError,match="baseline"):
        validate(trial)


def test_missing_safety_gate_and_unsafe_modes_rejected(proposal):
    trial=copy.deepcopy(proposal)
    trial["releaseGates"]["currentlyReadyForVehicleWrite"]=True
    with pytest.raises(ValueError):
        validate(trial)
    trial=copy.deepcopy(proposal)
    trial["releaseGates"]["independentPeerReview"]="SKIPPED"
    with pytest.raises(ValueError):
        validate(trial)
    trial=copy.deepcopy(proposal)
    next(x for x in trial["candidateParameters"] if x["id"]=="lowSpeedLkasEnable")["mode"]="APPLY"
    with pytest.raises(ValueError):
        validate(trial)


def test_missing_candidate_or_duplicate_rejected(proposal):
    trial=copy.deepcopy(proposal)
    trial["candidateParameters"].pop()
    with pytest.raises(ValueError):
        validate(trial)
    trial=copy.deepcopy(proposal)
    trial["candidateParameters"][0]=copy.deepcopy(trial["candidateParameters"][1])
    with pytest.raises(ValueError):
        validate(trial)
