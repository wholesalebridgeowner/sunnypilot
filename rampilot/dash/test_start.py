import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix", "the device entry point uses bash")
class StartStepTests(unittest.TestCase):
  def test_only_explicit_offroad_start_runs_the_service(self):
    script = Path(__file__).with_name("start.sh")
    with tempfile.TemporaryDirectory() as tmp:
      directory = Path(tmp)
      calls = directory / "calls"
      python = directory / "python3"
      python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_CALLS"\n')
      python.chmod(0o700)
      env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], PARAMS_DIR=tmp, TEST_CALLS=str(calls))

      def run(*args):
        return subprocess.run(["bash", str(script), *args], env=env, capture_output=True).returncode

      self.assertNotEqual(run(), 0)
      self.assertNotEqual(run("127.0.0.1"), 0)
      for contents in (b"1", b"", b"0\n"):
        (directory / "IsOnroad").write_bytes(contents)
        self.assertNotEqual(run("127.0.0.1"), 0)
      self.assertFalse(calls.exists())
      (directory / "IsOnroad").write_bytes(b"0")
      self.assertEqual(run("127.0.0.1"), 0)
      self.assertEqual(calls.read_text().splitlines(), [
        "-m rampilot.dash.server serve --init-token --bind 127.0.0.1",
      ])
