"""Tests for CAN capture summary without requiring the truck or Comma hardware."""
import importlib.util
import json
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("can_summary",Path(__file__).with_name("summarize_can_capture.py"))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Tests(unittest.TestCase):
  def test_observed_rate_and_gap(self):
    lines=[
      json.dumps({"monoTimeNs":1000000000,"service":"can","packets":[{"bus":0,"id":49,"dataHex":"01"}]}),
      json.dumps({"monoTimeNs":1010000000,"service":"can","packets":[{"bus":0,"id":49,"dataHex":"02"}]}),
      json.dumps({"monoTimeNs":1030000000,"service":"can","packets":[{"bus":0,"id":49,"dataHex":"03"}]})]
    x=m.summarize(lines)
    f=x["canFrameDiagnostics"][0]
    self.assertEqual(f["idHex"],"0x31")
    self.assertEqual(f["frames"],3)
    self.assertAlmostEqual(f["maxGapMs"],20)
    self.assertEqual(x["malformedRecords"],0)
    self.assertAlmostEqual(x["totalDurationSeconds"],.03)
  def test_invalid_frames_are_not_counted(self):
    lines=[json.dumps({"monoTimeNs":10,"service":"can","packets":[{"bus":300,"id":49,"dataHex":"xyz"}]}),
           "not-json"]
    s=m.summarize(lines)
    self.assertEqual(s["malformedRecords"],2)
    self.assertEqual(s["canFrameDiagnostics"],[])
  def test_empty(self):
    self.assertIsNone(m.summarize([])["totalDurationSeconds"])

if __name__=="__main__":
  unittest.main()
