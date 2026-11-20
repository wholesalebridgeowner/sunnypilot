"""Offline operator-bootstrap regression tests (never contact a Comma)."""
from contextlib import redirect_stdout
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import comma_device_parked as app


class DeviceOperatorTests(unittest.TestCase):
    def test_git_object_verification(self):
        data = b"print('hello')\n"
        expected = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        self.assertEqual(app.git_blob_sha(data), expected)

    def test_preflight_refuses_dirty_or_different_installed_source(self):
        accepted = app.source_preflight(
            installed_commit=app.EXPECTED_DEVICE_COMMIT,
            dirty=False, arch="aarch64", free_bytes=2 * 1024 ** 3)
        self.assertEqual(accepted, [])
        for kwargs, issue in (
            ({"dirty": True}, "installed_git_tree_modified"),
            ({"installed_commit": "wrong"}, "installed_build_mismatch"),
            ({"arch": "x86_64"}, "device_architecture_mismatch"),
            ({"free_bytes": 1}, "insufficient_data_storage"),
        ):
            current = {"installed_commit": app.EXPECTED_DEVICE_COMMIT,
                       "dirty": False, "arch": "aarch64", "free_bytes": 2 * 1024**3}
            current.update(kwargs)
            self.assertIn(issue, app.source_preflight(**current))

    def test_download_requires_matching_pinned_git_object(self):
        good = b"print('safe')\n"
        sha = app.git_blob_sha(good)
        with patch.object(app, "PINNED_FILES", {"safe.py": sha}):
            with patch.object(app, "urlopen", return_value=BytesIO(good)):
                self.assertEqual(app.fetch_pinned("safe.py", sha), good)
            with patch.object(app, "urlopen", return_value=BytesIO(b"evil")):
                with self.assertRaisesRegex(app.NoGo, "integrity_mismatch"):
                    app.fetch_pinned("safe.py", sha)
            with self.assertRaisesRegex(app.NoGo, "unrecognized"):
                app.fetch_pinned("unknown.py", sha)

    def test_install_and_rollback_only_touch_diagnostic_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "diag"
            backup = root / "backups"
            mark = root / "last-diagnostic-backup"
            root.mkdir()
            (root / "a.py").write_text("original", encoding="utf-8")
            originals = {"a.py": b"print('updated a')\n",
                         "b.py": b"print('new b')\n"}
            pins = {name: app.git_blob_sha(data) for name, data in originals.items()}
            with patch.object(app, "ROOT", root), patch.object(app, "BACKUPS", backup), \
                 patch.object(app, "BACKUP_MARKER", mark), \
                 patch.object(app, "PINNED_FILES", pins), \
                 patch.object(app, "preflight", return_value={"ok": True}), \
                 patch.object(app, "fetch_pinned",
                              side_effect=lambda name, _: originals[name]):
                result = app.install()
                self.assertFalse(result["captureStarted"])
                self.assertEqual((root / "a.py").read_bytes(), originals["a.py"])
                self.assertEqual((root / "b.py").read_bytes(), originals["b.py"])
                self.assertTrue(mark.is_file())
                with self.assertRaisesRegex(app.NoGo, "already_staged"):
                    app.install()
                restored = app.rollback()
                self.assertEqual(restored["status"], "diagnostic_files_restored")
                self.assertEqual((root / "a.py").read_text(), "original")
                self.assertFalse((root / "b.py").exists())
                self.assertFalse(mark.exists())

    def test_explicit_approval_required_for_install_or_rollback(self):
        for mode in ("install", "capture", "rollback"):
            out = StringIO()
            with patch("sys.argv", ["comma_device_parked.py", mode]), \
                 redirect_stdout(out), patch.object(app, "preflight",
                                                    side_effect=AssertionError("must not run")):
                status = app.main()
            self.assertEqual(status, 2)
            reply = json.loads(out.getvalue())
            self.assertEqual(reply["reason"], "parked_owner_approval_required")

    def test_refuse_unexpected_file_links(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "real"
            target.mkdir()
            symlink = Path(folder) / "link"
            symlink.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(app.NoGo, "symlink"):
                app.ensure_private_dir(symlink)


if __name__ == "__main__":
    unittest.main()
