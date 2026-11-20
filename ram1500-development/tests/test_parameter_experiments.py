"""Offline steering delay sensitivity grid tests; no driving-control writes."""
import copy
import json
from pathlib import Path

import pytest
from ramtools.parameter_experiments import delay_hypotheses


@pytest.fixture
def proposal():
    p=Path(__file__).resolve().parents[1]/"proposed_parameters.json"
    return json.loads(p.read_text(encoding="utf8"))


def test_explicit_hypotheses_are_only_offline_and_include_current_baseline(proposal):
    r=delay_hypotheses(proposal)
    assert r["status"]=="OFFLINE_HYPOTHESIS_NOT_INSTALLABLE"
    assert r["release"] is False
    assert [round(x["hypothesisSeconds"],3) for x in r["hypotheses"]]==[.2,.23,.26,.29,.32]
    assert all(x["trialType"]=="read_only_offline_model_sensitivity" for x in r["hypotheses"])
    assert r["acceptance"]["independentSafetyReview"]=="REQUIRED"


@pytest.mark.parametrize("steps",[0,1,21,2.5,True,"4"])
def test_invalid_trial_grid_rejected(proposal,steps):
    with pytest.raises(ValueError):
        delay_hypotheses(proposal,steps=steps)


def test_no_candidate_parameter_is_written(proposal):
    result=delay_hypotheses(proposal,steps=2)
    assert all(x["value"] is None for x in proposal["candidateParameters"])
    assert "vehicleWriteReady" not in result
    assert "OFFLINE" in result["status"]


def test_mismatched_eps_firmware_and_baseline_rejected(proposal):
    altered=copy.deepcopy(proposal)
    altered["source"]["epsFirmware"]="WRONG"
    with pytest.raises(ValueError):
        delay_hypotheses(altered)
    altered=copy.deepcopy(proposal)
    altered["observedBaseline"]["steerActuatorDelaySeconds"]=0
    with pytest.raises(ValueError):
        delay_hypotheses(altered)


def test_unreasonable_learned_latency_rejected(proposal):
    altered=copy.deepcopy(proposal)
    altered["observedBaseline"]["observedLearnedLateralDelaySeconds"]=.95
    with pytest.raises(ValueError,match="discrepancy"):
        delay_hypotheses(altered)
