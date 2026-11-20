"""Validate a source-pinned Ram parameter proposal strictly as an offline artifact.

This tool never imports vehicle control code, connects to CAN, writes to an
ECU, or produces an installer or an on-device configuration file.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

EXPECTED_EPS_FW = "68466110AB"
EXPECTED_SOURCE = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
LOCKED = {"steeringTorqueAndRateLimits", "lowSpeedLkasEnable"}
ALLOWED = {"latAccelFactor","friction","steerActuatorDelaySeconds",
           "laneChangeComfortTuning","followingGapSeconds",*LOCKED}
BASELINE = {
    "latAccelFactor":2.0,
    "friction":0.05,
    "steerActuatorDelaySeconds":0.2,
    "minEnableSpeedMps":14.5,
    "minSteerSpeedMps":0.5,
    "openpilotLongitudinalControl":False,
    "pcmCruise":True,
    "liveTorqueParametersEnabled":False,
}


def validate(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValueError("proposal must be an object")
    if proposal.get("schemaVersion") != 1:
        raise ValueError("unsupported schema")
    if proposal.get("status") != "OFFLINE_PROPOSAL_NOT_INSTALLABLE":
        raise ValueError("proposal is not explicitly offline")
    src=proposal.get("source")
    if not isinstance(src,dict) or src.get("epsFirmware")!=EXPECTED_EPS_FW or src.get("installedSunnypilotCommit")!=EXPECTED_SOURCE:
        raise ValueError("source/EPS firmware differs from recorded truck")
    if src.get("vehicleSoftwareModified") is not False:
        raise ValueError("cannot claim software changed")
    observed=proposal.get("observedBaseline")
    if not isinstance(observed,dict):
        raise ValueError("baseline missing")
    for name,value in BASELINE.items():
        if observed.get(name) != value:
            raise ValueError(f"source-pinned baseline mismatch: {name}")
    candidates=proposal.get("candidateParameters")
    if not isinstance(candidates,list) or len(candidates)!=len(ALLOWED):
        raise ValueError("candidate list incomplete")
    found=set()
    for p in candidates:
        if not isinstance(p,dict):
            raise ValueError("candidate must be object")
        name=p.get("id")
        if name not in ALLOWED or name in found:
            raise ValueError("unknown or repeated candidate")
        found.add(name)
        if p.get("value") is not None:
            raise ValueError("candidate write value must remain null until evidence/review")
        mode=p.get("mode")
        if name in LOCKED and mode not in ("LOCKED_SAFETY_LIMITS","LOCKED_OEM_AUTHORIZATION"):
            raise ValueError("safety parameter not locked")
        if name=="followingGapSeconds" and mode!="MEASURE_FACTORY_ACC_ONLY":
            raise ValueError("factory ACC authority must remain unchanged")
        if name in {"latAccelFactor","friction","steerActuatorDelaySeconds"} and mode!="OBSERVE_AND_FIT_ONLY":
            raise ValueError("calibration cannot be applied before independent fitting")
        if name=="laneChangeComfortTuning" and mode!="ANALYZE_ONLY":
            raise ValueError("lane change parameter not ready")
        if not isinstance(p.get("evidenceNeeded"),str) or len(p["evidenceNeeded"])<30:
            raise ValueError("candidate missing necessary evidence")
        if not isinstance(p.get("guard"),str) or not p["guard"]:
            raise ValueError("candidate missing safety guard")
    gates=proposal.get("releaseGates")
    if not isinstance(gates,dict) or gates.get("currentlyReadyForVehicleWrite") is not False:
        raise ValueError("cannot release as vehicle-writable")
    required=[
        "offlinePinnedSourceChecks","multiRouteIndependentValidation",
        "comparativeReplayAndSafetyRegression","independentPeerReview",
        "driverApprovedParkedDeviceReview","controlledVehicleSafetyValidation"
    ]
    if any(gates.get(k)!="REQUIRED" for k in required):
        raise ValueError("missing required gate")
    return {
        "verifiedSource":EXPECTED_SOURCE[:8],
        "firmware":EXPECTED_EPS_FW,
        "candidateSlots":len(found),
        "vehicleWriteReady":False,
        "status":"validated_offline_proposal_only",
    }


def main():
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("proposal",type=Path)
    args=ap.parse_args()
    report=validate(json.loads(args.proposal.read_text(encoding="utf-8")))
    print(json.dumps(report,sort_keys=True))


if __name__=="__main__":
    main()
