import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rampilot.dash import server, shared


class SharedTests(unittest.TestCase):
  def setUp(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.directory = Path(tmp.name)
    self.now = 100_000_000
    self.source = self.enterContext(shared.SharedSource(self.directory, lambda: self.now))
    self.sender = self.enterContext(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
    self.advisor = {"carState": [self.now, 30., 32.], "carControl": [self.now, True],
                    "controlsState": [self.now, False], "drivingModelData": [self.now, "off"]}
    self.screen = {"deviceState": [self.now, True, self.now - 5_000_000], "carParams": [self.now, True],
                   "carStateSP": [self.now, 31.2928, None, {}]}

  def send(self, who, fields, stamp=None):
    packet = {"v": 1, "source": who, "at_us": self.now if stamp is None else stamp, "fields": fields}
    self.sender.sendto(json.dumps(packet).encode(), str(self.directory / (who + ".sock")))

  def both(self):
    self.send("screen", self.screen)
    self.send("advisor", self.advisor)
    return self.source.snapshot()

  def test_real_datagrams_and_source_expiry(self):
    self.assertEqual(self.source.snapshot(), shared.empty_snapshot())
    row = self.both()
    self.assertEqual(row["speed_mph"], 30 * shared.MPH_PER_MS)
    self.assertEqual(row["steering_limited"], False)
    self.assertAlmostEqual(row["speed_limit_mph"], 70.)
    self.now += shared.RELAY_MAX_AGE_US
    self.assertEqual(self.source.snapshot(), shared.empty_snapshot())

  def test_partial_source_and_old_envelopes(self):
    self.both()
    self.now += 200_000
    self.advisor["carState"][0] = self.now
    self.send("advisor", self.advisor)
    self.send("screen", self.screen)
    row = self.source.snapshot()
    self.assertFalse(row["stale"])
    self.assertIsNone(row["steering_active"])
    self.assertIsNone(row["speed_limit_mph"])
    self.assertEqual(row["regen"], "Unknown")
    self.assertIs(row["blindspot_available"], True)

  def test_drive_boundary_and_reordered_packet(self):
    self.both()
    self.now += 1
    self.screen = {"deviceState": [self.now, True, self.now]}
    self.send("screen", self.screen)
    self.assertEqual(self.source.snapshot(), shared.empty_snapshot())
    self.now += 1
    self.advisor["carState"] = [self.now, 15, None]
    self.send("advisor", self.advisor)
    self.assertFalse(self.source.snapshot()["stale"])
    self.send("advisor", {}, stamp=self.now - 1)
    row = self.source.snapshot()
    self.assertEqual(row["speed_mph"], 15 * shared.MPH_PER_MS)
    self.assertIsNone(row["steering_active"])
    self.assertIsNone(row["blindspot_available"])

  def test_expired_startup_configuration_clears_while_rest_is_fresh(self):
    self.screen["deviceState"][2] = 1
    self.screen["carParams"][0] = self.now - shared.TOPIC_AGE_US["carParams"]
    row = self.both()
    self.assertFalse(row["stale"])
    self.assertIsNone(row["blindspot_available"])

  def test_regen_requires_validation_and_a_fresh_source(self):
    from types import ModuleType
    chrysler = ModuleType("opendbc.car.chrysler")
    flags = SimpleNamespace(REGEN_VALIDATED=False, FUEL_LEVEL_VALIDATED=False)
    chrysler.ram_dt_diesel = flags
    with patch.dict(sys.modules, {"opendbc.car.chrysler": chrysler}):
      self.screen["carStateSP"][2:] = [True, {"fuel_us_gal": 15.}]
      row = self.both()
      self.assertEqual((row["regen"], row["diesel"]), ("Unknown", {}))
      flags.REGEN_VALIDATED = flags.FUEL_LEVEL_VALIDATED = True
      self.assertEqual(self.source.snapshot()["regen"], "Active")
      self.assertEqual(self.source.snapshot()["diesel"], {"fuel_us_gal": 15.})
      self.now += 150_000
      self.advisor["carState"][0] = self.now
      row = self.both()
      self.assertEqual((row["regen"], row["diesel"]), ("Unknown", {}))

  def test_new_invalid_offroad_and_missing_sources(self):
    self.both()
    self.now += 1
    self.screen["deviceState"][1] = False
    self.assertEqual(self.both(), shared.empty_snapshot())
    self.now += 1
    self.screen["deviceState"][1] = True
    self.send("screen", self.screen)
    self.send("advisor", {})
    self.assertEqual(self.source.snapshot(), shared.empty_snapshot())
    self.now += 1
    self.both()
    self.sender.sendto(b"broken", str(self.directory / "screen.sock"))
    self.assertEqual(self.source.snapshot(), shared.empty_snapshot())

  def test_missing_companion_cannot_start(self):
    with patch.dict(os.environ, {"ADVISOR_HOME": str(self.directory)}):
      with self.assertRaisesRegex(RuntimeError, "requires"):
        server.require_companion()

  def test_refuse_insecure_relay_directory(self):
    unsafe = self.directory / "shared"
    unsafe.mkdir()
    unsafe.chmod(0o755)
    with self.assertRaises(RuntimeError), shared.SharedSource(unsafe):
      pass
