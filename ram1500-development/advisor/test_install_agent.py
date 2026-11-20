"""continue.sh hook: exactly one line, backup first, exact uninstall, refuses unknown files."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from install_agent import HOOK, ORIGINAL, InstallError, hook_status, install_hook, uninstall_hook, write_start_script


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


if __name__ == "__main__":
    unittest.main()
