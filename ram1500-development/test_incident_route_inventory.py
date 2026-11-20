"""Offline tests of incident route lookup, not vehicle telemetry."""
from datetime import datetime, timezone
import json
import unittest
from incident_route_inventory import report_inventory, route_start

ROUTE="masked|2026-11-20--02-08-00"

def obj(name, seg, typ):
    return {"Key":f"logs/{name}/{seg}/{typ}.zst","Size":1024}

class IncidentInventoryTests(unittest.TestCase):
    def test_picks_only_route_covering_2019(self):
        results=report_inventory([
            obj(ROUTE,11,"qlog"),obj(ROUTE,11,"rlog"),obj(ROUTE,10,"qlog"),
            obj("masked|2026-11-20--01-00-00",1,"qlog")])
        self.assertEqual(results["candidateCount"],1)
        self.assertEqual(results["candidates"][0]["expectedSegmentBySixtySecondEstimate"],11)
        self.assertEqual(results["candidates"][0]["rlogWithinTwoSegments"],1)
        self.assertFalse(results["eventConfirmed"])
        self.assertNotIn("masked",json.dumps(results))
    def test_missing_route_or_incomplete_segments_not_claimed(self):
        x=report_inventory([obj(ROUTE,1,"qlog"),obj("UNKNOWN",0,"qlog")])
        self.assertEqual(x["candidateCount"],0)
        self.assertEqual(x["unparsableRouteCount"],1)
    def test_invalid_calendar_format_fails_closed(self):
        self.assertIsNone(route_start("masked|2026-02-30--02-19-00"))
        self.assertIsNone(route_start("masked|2026-11-20--29-19-00"))
        self.assertIsNotNone(route_start(ROUTE))
if __name__=="__main__":
    unittest.main()
