"""Offline fixtures for the incident-centered R-log controller evidence."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from auto_review import SCHEMA_COMMIT
from incident_route_inventory import TARGET
from incident_rlog_probe import read_rlog, InvalidRlog, WARNING_SECONDS_AFTER_MINUTE

class Msg:
    def __init__(self,kind,t,data):
        self.kind=kind
        self.logMonoTime=int(round(t*1e9))
        setattr(self,kind,data)
    def which(self):return self.kind

class State:
    def which(self):return "torqueState"
    torqueState=SimpleNamespace(saturated=True, output=1.0)

def header(commit=SCHEMA_COMMIT):
    wall=int((TARGET+timedelta(seconds=WARNING_SECONDS_AFTER_MINUTE)-timedelta(seconds=100)).timestamp()*1e9)
    return Msg("initData",100,SimpleNamespace(gitCommit=commit,wallTimeNanos=wall))

class ProbeTests(unittest.TestCase):
    def test_native_can_command_and_saturation_near_event(self):
        def reader(_):
            yield header()
            yield Msg("carState",200,SimpleNamespace(vEgo=11.0,steeringTorqueEps=250,
                    steeringTorque=33,steeringRateDeg=10.5,steeringAngleDeg=12,
                    steeringPressed=False))
            yield Msg("carControl",200.01,SimpleNamespace(latActive=True,
                    actuators=SimpleNamespace(torque=1.0)))
            yield Msg("carOutput",200.02,SimpleNamespace(
                    actuatorsOutput=SimpleNamespace(torqueOutputCan=280,torque=1.0)))
            yield Msg("controlsState",200.03,SimpleNamespace(lateralControlState=State()))
        summary=read_rlog([Path("38/rlog.zst")],reader)
        self.assertEqual(summary["status"],"event_centered_private_rlog_observation")
        self.assertEqual(summary["latActiveTrue"],1)
        self.assertEqual(summary["controllerSaturatedTrue"],1)
        self.assertEqual(summary["normalizedRequestTorque"]["maxAbs"],1.0)
        self.assertEqual(summary["nativeCommandTorqueField"]["maxAbs"],280.0)
        self.assertEqual(summary["epsSteeringTorqueSensor"]["maxAbs"],250.0)
        self.assertNotIn("vin",str(summary).lower())
    def test_cap_context_integrates_but_requires_live_health_fields(self):
        def reader(_):
            yield header()
            yield Msg("carState", 200, SimpleNamespace(
                vEgo=10.0, steeringTorqueEps=200, steeringTorque=0,
                steeringRateDeg=10, steeringAngleDeg=4, steeringPressed=False,
                canValid=True, steerFaultTemporary=False, steerFaultPermanent=False))
            yield Msg("carControl", 200.001, SimpleNamespace(
                latActive=True, actuators=SimpleNamespace(torque=1.0)))
            yield Msg("controlsState", 200.002, SimpleNamespace(
                lateralControlState=State()))
            yield Msg("carOutput", 200.003, SimpleNamespace(
                actuatorsOutput=SimpleNamespace(torqueOutputCan=350, torque=1.0)))
        result = read_rlog([Path("38/rlog.zst")], reader)
        context = result["nativeCeilingExposure"]
        self.assertEqual(context["status"], "qualified_observational_coverage")
        self.assertEqual(context["qualifiedCommaLateralAtCeilingSamples"], 1)
        self.assertEqual(context["qualifiedWithControllerSaturated"], 1)
        self.assertEqual(context["nativeCommandCeilingSourceReference"], 350)
        self.assertTrue(context["noVehicleWrite"])

    def test_wrong_source_rejected(self):
        def reader(_):
            yield header("wrong")
            yield Msg("carControl",200,SimpleNamespace(latActive=True))
        with self.assertRaises(InvalidRlog):
            read_rlog([Path("38/rlog.zst")],reader)
    def test_no_source_header_rejected(self):
        def reader(_):
            yield Msg("carControl",200,SimpleNamespace(latActive=True))
        with self.assertRaises(InvalidRlog):
            read_rlog([Path("38/rlog.zst")],reader)
if __name__=="__main__":
    unittest.main()
