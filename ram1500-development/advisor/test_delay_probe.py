"""The delay probe: does a stored fixed steering delay reach steering on the build that is running?"""
import hashlib
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import delay_probe
from delay_probe import DelayProbe

# The logic of the helper on the build this advisor was written for: with learning switched ON it hands back
# the cached value, with learning OFF the learned value it was given. A stored fixed delay never gets through.
HELPER_THIS_BUILD = '''from openpilot.common.params import Params


def get_lat_delay(params: Params, stock_lat_delay: float) -> float:
  if params.get_bool("LagdToggle"):
    return float(params.get("LagdValueCache", return_default=True))

  return stock_lat_delay
'''
# The same helper with the upstream fix: the two return statements swapped.
HELPER_FIXED = '''from openpilot.common.params import Params


def get_lat_delay(params: Params, stock_lat_delay: float) -> float:
  if params.get_bool("LagdToggle"):
    return stock_lat_delay

  return float(params.get("LagdValueCache", return_default=True))
'''
REPO_HELPER = Path(__file__).resolve().parents[2] / "sunnypilot" / "livedelay" / "helpers.py"
THIS_BUILD_SHA = "1618abd491ef"        # SHA-256 of the helper file of the build, first 12 hex


def stand_ins():
    """Stand-ins for the packages the helper imports, so its text can be run off the Comma."""
    params = types.ModuleType("openpilot.common.params")
    params.Params = object
    return {"openpilot": types.ModuleType("openpilot"), "openpilot.common": types.ModuleType("openpilot.common"),
            "openpilot.common.params": params}


class Clock:
    def __init__(self, step=0.004):
        self.t, self.step = 50.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class ProbeFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.count = 0

    def module(self, text):
        """The helper text as an imported module with a file of its own."""
        self.count += 1
        path = self.folder / f"helpers_{self.count}.py"
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        spec = importlib.util.spec_from_file_location(f"advisor_test_helper_{self.count}", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, stand_ins()), mock.patch.object(sys, "dont_write_bytecode", True):
            spec.loader.exec_module(module)
        return module

    def probe(self, text, **kwargs):
        return DelayProbe(importer=lambda: self.module(text), **kwargs).run()


class ResultTests(ProbeFixture):
    def test_the_helper_of_this_build_is_inert(self):
        out = self.probe(HELPER_THIS_BUILD)
        self.assertEqual(out["result"], "inert")
        self.assertEqual(set(out), {"result", "helperSha", "probeMs"})

    def test_with_the_two_returns_swapped_it_is_effective(self):
        self.assertEqual(self.probe(HELPER_FIXED)["result"], "effective")

    def test_a_raising_import_is_unknown(self):
        def importer():
            raise ImportError("no such module")
        out = DelayProbe(importer=importer).run()
        self.assertEqual((out["result"], out["helperSha"]), ("unknown", None))
        self.assertIsInstance(out["probeMs"], int)

    def test_an_import_that_tries_to_end_the_process_is_unknown(self):
        for error in (SystemExit(3), KeyboardInterrupt(), MemoryError()):
            with self.subTest(error=type(error).__name__):
                def importer(error=error):
                    raise error
                self.assertEqual(DelayProbe(importer=importer).run(),
                                 {"result": "unknown", "helperSha": None, "probeMs": mock.ANY})

    def test_a_probe_that_takes_over_a_second_is_unknown(self):
        out = self.probe(HELPER_THIS_BUILD, monotonic=Clock(step=1.2))
        self.assertEqual((out["result"], out["probeMs"]), ("unknown", 1200))
        self.assertIsNotNone(out["helperSha"])                         # what was read is still reported
        self.assertEqual(self.probe(HELPER_THIS_BUILD, monotonic=Clock(step=1.0))["result"], "inert")   # not over
        self.assertEqual(self.probe(HELPER_THIS_BUILD, monotonic=Clock(step=1.001))["result"], "unknown")

    def test_the_time_it_took_is_reported_in_whole_milliseconds(self):
        out = self.probe(HELPER_THIS_BUILD, monotonic=Clock(step=0.0127))
        self.assertEqual((out["probeMs"], type(out["probeMs"])), (13, int))

    def test_a_helper_that_answers_anything_else_is_unknown(self):
        for body in ("return 0.3", "return None", "return 'inert'", "return stock_lat_delay + 0.0001",
                     "raise RuntimeError('changed')", "return float('nan')", "return True"):
            with self.subTest(body=body):
                text = "def get_lat_delay(params, stock_lat_delay):\n  " + body + "\n"
                out = self.probe(text)
                self.assertEqual(out["result"], "unknown")

    def test_a_module_without_the_helper_is_unknown(self):
        out = self.probe("def other(params, stock_lat_delay):\n  return stock_lat_delay\n")
        self.assertEqual((out["result"], out["helperSha"]), ("unknown", None))

    def test_a_helper_that_reads_the_settings_another_way_still_gets_the_stand_in_values(self):
        # The stand-in answers False to every yes/no question and 0.40 to every other read, whatever the arguments.
        text = ("def get_lat_delay(params, stock_lat_delay):\n"
                "  if params.get_bool('LagdToggle', False):\n    return stock_lat_delay\n"
                "  return float(params.get('LagdValueCache', block=False, return_default=True))\n")
        self.assertEqual(self.probe(text)["result"], "effective")

    def test_the_stand_in_values(self):
        self.assertEqual((delay_probe.PROBE_STOCK_S, delay_probe.PROBE_CACHE_S, delay_probe.PROBE_LIMIT_MS),
                         (0.55, 0.40, 1000))


class HelperShaTests(ProbeFixture):
    def test_first_twelve_hex_of_the_sha256_of_the_helper_file(self):
        out = self.probe(HELPER_THIS_BUILD)
        self.assertEqual(out["helperSha"], hashlib.sha256(HELPER_THIS_BUILD.encode("utf-8")).hexdigest()[:12])
        self.assertNotEqual(self.probe(HELPER_FIXED)["helperSha"], out["helperSha"])

    def test_a_helper_whose_file_cannot_be_read_is_unknown(self):
        module = self.module(HELPER_THIS_BUILD)
        module.__file__ = str(self.folder / "gone.py")
        self.assertEqual(DelayProbe(importer=lambda: module).run(),
                         {"result": "unknown", "helperSha": None, "probeMs": mock.ANY})

    @unittest.skipUnless(REPO_HELPER.is_file(), "the sunnypilot source is not beside the advisor")
    def test_the_helper_in_this_checkout_is_the_one_of_the_build_and_reads_inert(self):
        text = REPO_HELPER.read_bytes().replace(b"\r\n", b"\n")        # as the Comma holds it
        out = self.probe(text)
        self.assertEqual(out, {"result": "inert", "helperSha": THIS_BUILD_SHA, "probeMs": mock.ANY})


class ImportTests(ProbeFixture):
    def test_the_helper_is_imported_once_and_reused(self):
        calls = []
        module = self.module(HELPER_THIS_BUILD)

        def importer():
            calls.append(1)
            return module
        probe = DelayProbe(importer=importer)
        self.assertEqual([probe.run()["result"] for _ in range(3)], ["inert"] * 3)
        self.assertEqual(len(calls), 1)

    def test_a_failed_import_is_tried_again_at_the_next_drive(self):
        module = self.module(HELPER_THIS_BUILD)
        calls = []

        def importer():
            calls.append(1)
            if len(calls) == 1:
                raise ImportError("not there yet")
            return module
        probe = DelayProbe(importer=importer)
        self.assertEqual([probe.run()["result"] for _ in range(3)], ["unknown", "inert", "inert"])
        self.assertEqual(len(calls), 2)

    def test_it_imports_the_helper_by_the_name_the_build_gives_it(self):
        self.assertEqual(delay_probe.HELPER_MODULE, "openpilot.sunnypilot.livedelay.helpers")
        package = self.folder / "tree"
        for part in ("openpilot", "openpilot/common", "openpilot/sunnypilot", "openpilot/sunnypilot/livedelay"):
            (package / part).mkdir(parents=True)
            (package / part / "__init__.py").write_bytes(b"")
        (package / "openpilot" / "common" / "params.py").write_bytes(b"class Params:\n  pass\n")
        (package / "openpilot" / "sunnypilot" / "livedelay" / "helpers.py").write_bytes(HELPER_FIXED.encode("utf-8"))
        kept = {name: sys.modules.pop(name) for name in list(sys.modules)
                if name == "openpilot" or name.startswith("openpilot.")}
        sys.path.insert(0, str(package))
        try:
            out = DelayProbe().run()
        finally:
            sys.path.remove(str(package))
            for name in [n for n in sys.modules if n == "openpilot" or n.startswith("openpilot.")]:
                del sys.modules[name]
            sys.modules.update(kept)
        self.assertEqual(out["result"], "effective")
        self.assertEqual(out["helperSha"], hashlib.sha256(HELPER_FIXED.encode("utf-8")).hexdigest()[:12])

    def test_without_the_build_around_it_the_probe_is_unknown_and_harmless(self):
        with mock.patch.object(delay_probe, "HELPER_MODULE", "advisor_no_such_package.helpers"):
            self.assertEqual(DelayProbe().run(), {"result": "unknown", "helperSha": None, "probeMs": mock.ANY})


if __name__ == "__main__":
    unittest.main()
