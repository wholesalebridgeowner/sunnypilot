"""Unit tests for UTC incident route metadata/S3 correlation; no live calls."""
from datetime import datetime, timezone, timedelta
import json
import unittest
from incident_route_metadata import route_meta, parse_time, clean

BASE=datetime(2026,11,20,2,0,tzinfo=timezone.utc)
TARGET=datetime(2026,11,20,2,19,tzinfo=timezone.utc)

def row(route, seg,kind):
    return {"Key":f"logs/{clean(route)}/{seg}/{kind}.zst","Size":100}

class Tests(unittest.TestCase):
    def test_utc_metadata_and_private_s3_match(self):
        route="privatedongle|opaque 2026 route"
        obs=[row(route,18,"rlog"),row(route,19,"qlog"),row(route,19,"rlog")]
        meta=[{"fullname":route,"start_time":BASE.isoformat(),
               "end_time":(BASE+timedelta(minutes=25)).isoformat(),"number":19}]
        out=route_meta(obs,meta,TARGET)
        self.assertEqual(out["candidateCount"],1)
        self.assertEqual(out["candidates"][0]["secondsAfterRecordedStart"],1140)
        self.assertEqual(out["candidates"][0]["estimatedWarningSegment"],19)
        self.assertEqual(out["candidates"][0]["rlogWithinTwoEstimatedSegments"],2)
        self.assertFalse(out["eventInLogConfirmed"])
        self.assertNotIn("privatedongle",json.dumps(out))
    def test_old_route_and_wrong_UTC_time_excluded(self):
        route="private|early"
        obs=[row(route,1,"rlog")]
        meta=[{"canonical_route_name":route,
               "start_time":(BASE-timedelta(hours=1)).isoformat(),
               "end_time":(BASE+timedelta(minutes=1)).isoformat()}]
        self.assertEqual(route_meta(obs,meta,TARGET)["candidateCount"],0)
    def test_epoch_millis_seconds_and_iso(self):
        ts=BASE.timestamp()
        self.assertEqual(parse_time(int(ts*1000)),BASE)
        self.assertEqual(parse_time(ts),BASE)
        self.assertEqual(parse_time(BASE.isoformat()),BASE)
        self.assertIsNone(parse_time("unparseable"))
if __name__=="__main__":
    unittest.main()
