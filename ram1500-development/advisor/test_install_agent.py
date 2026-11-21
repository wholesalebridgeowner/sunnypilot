"""continue.sh hook: exactly one line, backup first, exact uninstall, refuses unknown files.
The agent's files: the Comma gets exactly what the agent needs, and the agent leaves no bytecode behind."""
import ast
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import install_agent
from install_agent import (AGENT_PAYLOAD, HOOK, HOOKED, ORIGINAL, START_SCRIPT, InstallError, extra_modules, hook_status,
                           install_hook, missing_payload, uninstall_hook, write_start_script)

ADVISOR = Path(install_agent.__file__).resolve().parent
# What the agent needs from the Comma itself; never part of the payload and only imported inside functions.
DEVICE_ONLY = {"cereal", "openpilot", "device_telemetry", "fcntl"}
IMPORT_CHECK = ("import sys; sys.path.insert(0, sys.argv[1]); import agent, pipeline; "
                "pipeline.Pipeline(); agent.Agent; "
                "print(' '.join(sorted(m.__name__ for m in list(sys.modules.values()) "
                "if getattr(m, '__file__', None) and m.__file__.startswith(sys.argv[1])))); "
                "print(' '.join(sorted(n for n in sys.modules if n.split('.')[0] in ('numpy', 'capnp', 'zstandard'))))")


class HookTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.cont = self.dir / "continue.sh"
        self.backup = self.dir / "continue.sh.orig"
        self.cont.write_bytes(ORIGINAL)

    def test_install_adds_one_line_before_exec(self):
        self.assertEqual(install_hook(self.cont, self.backup), "installed")
        lines = self.cont.read_bytes().splitlines(keepends=True)
        self.assertEqual(lines.count(HOOK), 1)
        self.assertEqual(lines.index(HOOK) + 1, lines.index(b"exec ./launch_openpilot.sh\n"))
        self.assertEqual(b"".join(l for l in lines if l != HOOK), ORIGINAL)

    def test_backup_written_and_install_is_idempotent(self):
        install_hook(self.cont, self.backup)
        self.assertEqual(self.backup.read_bytes(), ORIGINAL)
        self.assertEqual(install_hook(self.cont, self.backup), "already_installed")
        self.assertEqual(self.cont.read_bytes().count(HOOK), 1)

    def test_uninstall_restores_original_exactly(self):
        install_hook(self.cont, self.backup)
        self.assertEqual(uninstall_hook(self.cont, self.backup), "uninstalled")
        self.assertEqual(self.cont.read_bytes(), ORIGINAL)
        self.assertEqual(uninstall_hook(self.cont, self.backup), "not_installed")

    def test_unknown_continue_script_refused(self):
        self.cont.write_bytes(b"#!/usr/bin/bash\necho custom\nexec ./launch_openpilot.sh\n")
        with self.assertRaises(InstallError):
            install_hook(self.cont, self.backup)
        self.assertEqual(hook_status(self.cont), "unknown")

    def test_status(self):
        self.assertEqual(hook_status(self.cont), "original")
        install_hook(self.cont, self.backup)
        self.assertEqual(hook_status(self.cont), "installed")

    @unittest.skipUnless(shutil.which("bash"), "bash not available")
    def test_hooked_script_passes_bash_syntax_check(self):
        install_hook(self.cont, self.backup)
        start = self.dir / "start_agent.sh"
        write_start_script(start)
        for script in (self.cont, start):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class StartScriptTests(unittest.TestCase):
    def test_bytecode_writing_is_switched_off_before_the_agent_starts(self):
        lines = START_SCRIPT.decode("ascii").splitlines()
        self.assertIn("export PYTHONDONTWRITEBYTECODE=1", lines)
        launch = next(i for i, line in enumerate(lines) if line.startswith("exec "))
        self.assertLess(lines.index("export PYTHONDONTWRITEBYTECODE=1"), launch)
        self.assertEqual(launch, len(lines) - 1)                       # nothing runs after the agent is started
        self.assertIn("/data/ram-diag/advisor/agent.py", lines[launch])

    def test_the_rest_of_the_start_script_is_as_it_was(self):
        self.assertEqual(START_SCRIPT.replace(b"export PYTHONDONTWRITEBYTECODE=1\n", b""), (
            b"#!/usr/bin/bash\n"
            b"# Drive Advisor agent launcher, started in the background by /data/continue.sh.\n"
            # The log keeper's launch, as in the launcher installed on the truck (owner GO 2026-11-21);
            # review of 3098b5f: the installer must not drop it.
            b"# Also starts the log keeper when it is installed (owner GO 2026-11-21), detached, lowest priority.\n"
            b"[ -x /data/ram-diag/keeper/keep_logs.sh ] && ( setsid nice -n 19 /data/ram-diag/keeper/keep_logs.sh"
            b" </dev/null >/dev/null 2>&1 & )\n"
            b"cd /data/openpilot || exit 0\n"
            b"export PYTHONPATH=/data/openpilot:/data/ram-diag/advisor\n"
            b"exec setsid nice -n 10 /usr/local/venv/bin/python3 /data/ram-diag/advisor/agent.py"
            b" </dev/null >/dev/null 2>&1\n"))

    def test_the_agent_switches_it_off_itself_before_it_imports_anything_from_the_build(self):
        tree = ast.parse((ADVISOR / "agent.py").read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        switched_off = first_device_import = first_device_use = None
        for i, node in enumerate(main.body):
            if (isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "sys.dont_write_bytecode"
                    and isinstance(node.value, ast.Constant) and node.value.value is True):
                switched_off = i if switched_off is None else switched_off
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(name.split(".")[0] in DEVICE_ONLY - {"fcntl"} for name in names) and first_device_import is None:
                first_device_import = i
            if first_device_use is None and any(isinstance(sub, ast.Name) and sub.id == "DeviceParams"
                                                for sub in ast.walk(node)):
                first_device_use = i                                   # DeviceParams() imports the build's Params
        self.assertIsNotNone(switched_off)
        self.assertIsNotNone(first_device_import)
        self.assertLess(switched_off, first_device_import)
        self.assertLess(switched_off, first_device_use)


def module_level_imports(path: Path) -> set:
    """Top-level names of everything a file imports when it is imported (not what its functions import later)."""
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
    return found


def copy_payload(folder: Path, without=None) -> Path:
    for name in AGENT_PAYLOAD:
        if name != without:
            shutil.copyfile(ADVISOR / name, folder / name)
    return folder


def import_agent_from(folder: Path):
    """`import agent` and build a Pipeline in a fresh interpreter that sees this folder and nothing of the checkout."""
    return subprocess.run([sys.executable, "-I", "-B", "-c", IMPORT_CHECK, str(folder)], capture_output=True,
                          text=True, cwd=str(folder))


class PayloadTests(unittest.TestCase):
    """The start script discards the agent's output, so a missing module would make it exit without a trace."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(os.path.realpath(folder.name))

    def test_every_payload_file_is_a_module_of_the_advisor(self):
        self.assertEqual(len(set(AGENT_PAYLOAD)), len(AGENT_PAYLOAD))
        self.assertEqual(list(AGENT_PAYLOAD), sorted(AGENT_PAYLOAD))
        for name in AGENT_PAYLOAD:
            self.assertTrue((ADVISOR / name).is_file(), name)

    def test_the_agent_imports_and_builds_a_pipeline_from_a_folder_that_holds_only_the_payload(self):
        result = import_agent_from(copy_payload(self.dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded, heavy = result.stdout.splitlines()[0].split(), result.stdout.splitlines()[1:]
        # Every payload file was needed for it, and nothing else was found.
        self.assertEqual(sorted(name + ".py" for name in loaded), list(AGENT_PAYLOAD))
        self.assertEqual("".join(heavy).strip(), "")                   # no numpy, no log decoder
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), list(AGENT_PAYLOAD))    # and no bytecode folder

    def test_removing_any_one_file_fails(self):
        def without(name):
            folder = self.dir / name[:-3]
            folder.mkdir()
            result = import_agent_from(copy_payload(folder, without=name))
            return name, result.returncode, result.stderr
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(without, AGENT_PAYLOAD))
        for name, code, stderr in results:
            with self.subTest(removed=name):
                self.assertNotEqual(code, 0)
                self.assertIn(f"No module named '{name[:-3]}'", stderr)

    def test_nothing_that_is_not_for_the_comma_is_in_the_payload(self):
        for name in AGENT_PAYLOAD:
            self.assertFalse(name.startswith("test_"), name)
        not_for_the_comma = {"service_app.py", "service_store.py", "service_notify.py", "replay_report.py",
                             "npz_input.py", "quality_values.py", "port_adapters.py", "synth.py",
                             "acceptance_data.py", "schema_v2.py", "install_agent.py",
                             "parking_report.py"}     # experimental, offline only
        self.assertFalse(not_for_the_comma & set(AGENT_PAYLOAD))
        on_disk = {p.name for p in ADVISOR.glob("*.py") if not p.name.startswith("test_")}
        self.assertEqual(on_disk - set(AGENT_PAYLOAD), not_for_the_comma)       # every module is one or the other

    def test_a_payload_module_imports_only_the_standard_library_and_the_payload_when_it_is_loaded(self):
        own = {name[:-3] for name in AGENT_PAYLOAD}
        for name in AGENT_PAYLOAD:
            for imported in module_level_imports(ADVISOR / name) - own:
                with self.subTest(file=name, imports=imported):
                    self.assertIn(imported, sys.stdlib_module_names)
                    self.assertNotIn(imported, DEVICE_ONLY)

    def test_with_the_start_scripts_environment_no_bytecode_is_written(self):
        copy_payload(self.dir)
        env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        for setting, expected in (({"PYTHONDONTWRITEBYTECODE": "1"}, False), ({}, True)):
            with self.subTest(setting=setting):
                shutil.rmtree(self.dir / "__pycache__", ignore_errors=True)
                result = subprocess.run([sys.executable, "-c", "import agent"], capture_output=True, text=True,
                                        cwd=str(self.dir), env={**env, "PYTHONPATH": str(self.dir), **setting})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.dir / "__pycache__").exists(), expected)     # the second run shows the test bites

    def test_missing_and_extra_files_of_a_folder(self):
        copy_payload(self.dir)
        self.assertEqual((missing_payload(self.dir), extra_modules(self.dir)), ([], []))
        (self.dir / "wire.py").unlink()
        (self.dir / "gates.py").unlink()
        (self.dir / "metrics.py").write_bytes(b"")                     # left behind by an earlier version
        (self.dir / "install_agent.py").write_bytes(b"")               # the installer itself is no stale module
        (self.dir / "advisor.key").write_bytes(b"")
        self.assertEqual(missing_payload(self.dir), ["gates.py", "wire.py"])
        self.assertEqual(extra_modules(self.dir), ["metrics.py"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "advisor"
        self.home.mkdir()
        self.cont = Path(folder.name) / "continue.sh"
        self.cont.write_bytes(ORIGINAL)
        for target, value in (("HOME", self.home), ("CONTINUE", self.cont)):
            patcher = mock.patch.object(install_agent, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_command(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = install_agent.main(["install_agent.py", *args])
        return code, out.getvalue().splitlines()

    def test_install_with_every_file_in_place(self):
        copy_payload(self.home)
        self.assertEqual(self.run_command("install"), (0, ["installed"]))
        self.assertEqual(hook_status(self.cont), "installed")
        self.assertEqual((self.home / "start_agent.sh").read_bytes(), START_SCRIPT)
        self.assertEqual(self.run_command("status"), (0, ["installed"]))

    def test_install_is_refused_while_an_agent_file_is_missing(self):
        copy_payload(self.home, without="measure_delay.py")
        (self.home / "pipeline.py").unlink()
        code, lines = self.run_command("install")
        self.assertEqual((code, lines), (2, ["refused: missing_agent_files measure_delay.py pipeline.py"]))
        self.assertEqual(hook_status(self.cont), "original")           # nothing was touched
        self.assertFalse((self.home / "start_agent.sh").exists())

    def test_a_refused_install_leaves_the_installed_launcher_untouched(self):
        # Review of 3098b5f (Codex, 2026-11-21): the launcher was written before the hook was checked.
        copy_payload(self.home)
        installed = b"#!/usr/bin/bash\n# the launcher on the truck, which also starts the log keeper\n"
        (self.home / "start_agent.sh").write_bytes(installed)
        self.cont.write_bytes(b"#!/usr/bin/bash\necho something else\n")
        code, lines = self.run_command("install")
        self.assertEqual((code, lines), (2, ["refused: unknown_continue_script"]))
        self.assertEqual((self.home / "start_agent.sh").read_bytes(), installed)

    def test_the_launcher_keeps_starting_the_log_keeper(self):
        copy_payload(self.home)
        self.cont.write_bytes(HOOKED)                                   # already installed
        self.assertEqual(self.run_command("install"), (0, ["already_installed"]))
        text = (self.home / "start_agent.sh").read_text()
        keeper = "[ -x /data/ram-diag/keeper/keep_logs.sh ] && ( setsid nice -n 19 /data/ram-diag/keeper/keep_logs.sh"
        self.assertIn(keeper, text)
        self.assertLess(text.index(keeper), text.index("exec setsid"))  # started before the agent replaces the shell

    def test_install_names_modules_left_behind_by_an_earlier_version(self):
        copy_payload(self.home)
        (self.home / "metrics.py").write_bytes(b"")
        self.assertEqual(self.run_command("install"), (0, ["installed", "not_part_of_this_agent metrics.py"]))

    def test_payload_lists_the_files(self):
        self.assertEqual(self.run_command("payload"), (0, list(AGENT_PAYLOAD)))

    def test_uninstall_and_unknown_command(self):
        copy_payload(self.home)
        self.run_command("install")
        self.assertEqual(self.run_command("uninstall"), (0, ["uninstalled"]))
        self.assertEqual(self.cont.read_bytes(), ORIGINAL)
        self.assertEqual(self.run_command("explode")[0], 2)


if __name__ == "__main__":
    unittest.main()
