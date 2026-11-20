"""Generate OFFLINE steering-delay sensitivity experiments, never vehicle configs.

Hypotheses interpolate the observed configured 0.20 s and separately learned
0.32 s values. They are not measured improvements, safe controller values,
or a list of commands for the truck. Fails closed on any source mismatch.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from .parameter_proposal import validate

EXPERIMENT_STATUS="OFFLINE_HYPOTHESIS_NOT_INSTALLABLE"


def delay_hypotheses(proposal, *, steps=4):
    validate(proposal)
    if not isinstance(steps,int) or isinstance(steps,bool) or not 2 <= steps <= 20:
        raise ValueError("steps must be an integer 2..20")
    actual=proposal["observedBaseline"]
    configured=Decimal(str(actual["steerActuatorDelaySeconds"]))
    learned=Decimal(str(actual["observedLearnedLateralDelaySeconds"]))
    if not Decimal("0") < configured <= Decimal("1") or not Decimal("0") < learned <= Decimal("1"):
        raise ValueError("unreasonable observed latency")
    if abs(learned-configured)>Decimal(".5"):
        raise ValueError("observed delay discrepancy requires source review")
    probes=[
      {"parameter":"steerActuatorDelaySeconds",
       "hypothesisSeconds":float(configured+(learned-configured)*Decimal(i)/Decimal(steps)),
       "trialType":"read_only_offline_model_sensitivity"}
      for i in range(steps+1)
    ]
    return {
      "status":EXPERIMENT_STATUS,
      "installedBuild":proposal["source"]["installedSunnypilotCommit"],
      "epsFirmware":proposal["source"]["epsFirmware"],
      "configuredBaselineSeconds":float(configured),
      "observedLearnedLatencySeconds":float(learned),
      "hypotheses":probes,
      "release":False,
      "acceptance": {
        "multipleIndependentRoutes":"REQUIRED",
        "excludeDriverOverrideAndEPSLimiter":"REQUIRED",
        "causalControllerPhaseEvidence":"REQUIRED",
        "outOfSampleLatencyVsTrackingTradeoffs":"REQUIRED",
        "independentSafetyReview":"REQUIRED",
      },
      "limitations":[
        "The learned lateral delay is an observed diagnostic, not an identified actuator latency.",
        "Steering software can be phase-ambiguous and torque-rate limited; exclude these data.",
        "The intermediate values are linear sensitivity samples, not optimized numeric writes.",
        "No on-device config, CAN message, EPS/Panda safety or installer is generated.",
      ]
    }


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposal",type=Path)
    parser.add_argument("--steps",type=int,default=4)
    args=parser.parse_args()
    obj=json.loads(args.proposal.read_text(encoding="utf8"))
    print(json.dumps(delay_hypotheses(obj,steps=args.steps),indent=2))


if __name__=="__main__":
    main()
