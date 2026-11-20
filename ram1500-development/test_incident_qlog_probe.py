"""Synthetic tests of incident Q-log alignment; no private Comma data."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import unittest

from auto_review import SCHEMA_COMMIT
from incident_route_inventory import TARGET
from incident_qlog_probe import observe, private_candidate

class M:
    def __init__(self,kind,secs,**kwargs):
        self._kind=kind
        self.logMonoTime=int(secs*1e9)
        setattr(self,kind,SimpleNamespace(**kwargs))
    def which(self):
        return self._kind

def header():
    # At monotonic t=100, wall clock was 2026-11-20 02:17:20 UTC.
    return M("initData",100,gitCommit=SCHEMA_COMMIT,
             wallTimeNanos=int((TARGET-timedelta(seconds=100)).timestamp()*1e9))

class Tests(unittest.TestCase):
    def test_source_pinned_event_is_aligned_to_exact_user_clock(self):
        def reader(_):
            yield header()
            yield M("carState",198,vEgo=19.0,steeringRateDeg=2.5,steeringTorqueEps=57.0)
            yield M("carControl",199,latActive=True)
            yield M("onroadEvents",200)
        # Override the event list to simulate Cap'n Proto list semantics.
        def read(_):
            yield header()
            yield M("carState",198,vEgo=19.0,steeringRateDeg=2.5,steeringTorqueEps=57.0)
            yield M("carControl",199,latActive=True)
            e=SimpleNamespace(name="steerSaturated",warning=True,softDisable=False,immediateDisable=False)
            msg=M("onroadEvents",200)
            msg.onroadEvents=[e]
            yield msg
        out=observe([Path("38/qlog.zst")],read)
        self.assertEqual(out["segmentsVerified"],1)
        self.assertEqual(out["segmentsWithWallClockAnchor"],1)
        self.assertEqual(out["carStateSamples"],1)
        self.assertEqual(out["latActiveTrueSamples"],1)
        self.assertEqual(out["eventStatus"],"candidate_alert_present")
        self.assertEqual(out["events"][0]["secondsFromWarning"],0)
        self.assertIn("steerSaturated",out["eventSampleCounts"])
        self.assertNotIn("private",json.dumps(out))
    def test_bad_source_fails_closed(self):
        def bad(_):
            yield M("initData",100,gitCommit="wrong",wallTimeNanos=int(TARGET.timestamp()*1e9))
            yield M("carControl",200,latActive=True)
        out=observe([Path("38/qlog.zst")],bad)
        self.assertEqual(out["status"],"unmatched_source_header")
    def test_route_selection_uses_metadata_not_name_dates(self):
        route="privateDongle|opaque--route"
        from incident_route_metadata import clean
        objects=[{"Key":f"logs/{clean(route)}/38/qlog.zst","Size":100}]
        start=TARGET-timedelta(seconds=2301)
        end=TARGET+timedelta(seconds=220)
        rows=[{"fullname":route,"start_time":start.isoformat(),"end_time":end.isoformat()}]
        matched=private_candidate(objects,rows)
        self.assertEqual(len(matched),1)
        self.assertEqual(len(matched[0][0]),1)
if __name__=="__main__":
    unittest.main()
