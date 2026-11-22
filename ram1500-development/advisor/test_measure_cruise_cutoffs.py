from types import SimpleNamespace as NS
import unittest

from measure_cruise_cutoffs import CruiseCutoffs
from report_body import report_lines


class CutoffTests(unittest.TestCase):
    def put(self, m, t, *edge):
        m.on_sample(None, NS(sample=NS(t=t, cruise_observation=edge or None)))

    def test_observed_causes_and_precedence(self):
        for label, cause in [("bothPedals",(True,True,True,True)),("brake",(False,True,False,False)),
                             ("gas",(True,False,False,False)),("hostCancel",(False,False,True,True)),
                             ("button",(False,False,False,True)),("unavailable",(False,False,False,False))]:
            m = CruiseCutoffs()
            self.put(m,0,True,True,*cause)
            self.put(m,.1,False,False,False,False,False,False)
            self.put(m,.2,False,False,False,False,False,False)
            self.assertEqual(m.window_fields(),{"cruiseCutoffs":{label:1}})
            self.assertEqual(m.window_fields(),{})

    def test_no_edge_across_missing_state_or_gap(self):
        for missing in (True,False):
            m = CruiseCutoffs()
            self.put(m,0,True,True,False,False,False,False)
            if missing:
                self.put(m,.05)
            self.put(m,.1 if missing else .26,False,False,False,False,False,False)
            self.assertEqual(m.window_fields(),{})

    def test_unknown_and_window_boundary(self):
        m = CruiseCutoffs()
        self.put(m,0,True,True,False,False,False,False)
        self.put(m,.1,True,True,False,False,False,False)
        self.assertEqual(m.window_fields(),{"cruiseCutoffs":{}})
        self.put(m,.2,False,True,False,False,None,None)
        self.assertEqual(m.window_fields(),{"cruiseCutoffs":{"unresolved":1}})

    def test_report_counts_deduplicated_windows_only(self):
        row = {"w":0,"cruiseCutoffs":{"gas":1,"unresolved":2}}
        text = " ".join(report_lines([{"summaries":[row,row]},{"summaries":[{"w":0}]}],"end"))
        self.assertIn("1 gas pedal",text)
        self.assertIn("2 cause unresolved",text)
        self.assertIn("full CAN log",text)
        self.assertNotIn("Factory cruise cutoffs"," ".join(report_lines([{"summaries":[{}]}],"end")))


class AuditedReportTests(unittest.TestCase):
    def test_raw_truck_request_and_midnight_are_distinct_from_unavailable(self):
        from datetime import date
        from audited_cruise_report import audited_counts
        event = {"ns":1,"kind":"ESP request","central":"2026-11-23T05:59:59+00:00"}
        unavailable = {**event,"ns":2,"kind":"availability lost"}
        tomorrow = {**event,"ns":3,"central":"2026-11-23T06:00:00+00:00"}
        counts = audited_counts({"route":{"events":[event,event,unavailable,tomorrow]}},date(2026,11,22))
        self.assertEqual(counts,{"truck request":1,"cruise unavailable":1})
