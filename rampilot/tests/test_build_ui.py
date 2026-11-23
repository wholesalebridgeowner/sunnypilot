"""Exercise the build helper on Linux with local Git, synthetic inputs and a stand-in for SCons."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / "ui" / "build_ui_on_device.sh"


def sha(path):
  return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(sys.platform.startswith("linux"), "the build helper targets Linux")
class BuildUiTests(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.root = Path(temp.name)
    self.tools = self.root / "tools"
    self.tools.mkdir()
    patches = self.root / "patches"
    patches.mkdir()
    self.helper = patches / HELPER.name
    shutil.copyfile(HELPER, self.helper)
    self.work = self.root / "work"
    self.stage = self.root / "staged"
    self.params = self.root / "params"
    self.params.mkdir()
    self.onroad = self.params / "IsOnroad"
    self.onroad.write_bytes(b"0")
    self.source = self.root / "source"
    self.source.mkdir()
    self.op = self.root / "installed"
    self.env = {
      "PATH": f"{self.tools}:{os.environ['PATH']}", "HOME": str(self.root), "LC_ALL": "C",
      "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "file",
      "OP_DIR": str(self.op), "WORK": str(self.work), "STATE_DIR": str(self.stage),
      "PARAMS_DIR": str(self.params), "SOURCE_URL": self.source.as_uri(),
      "RAMPILOT_VENV_BIN": str(self.root / "venv/bin"),
      "BUILDER_LOG": str(self.root / "builder-invocations"),
    }
    self.git(self.source, "init", "-q")
    (self.source / "cereal").mkdir()
    (self.source / "cereal/custom.capnp").write_text("upstream schema\n")
    (self.source / "opendbc").mkdir()
    (self.source / "opendbc/car.capnp").write_text("upstream car schema\n")
    (self.source / "cereal/car.capnp").symlink_to("../opendbc/car.capnp")
    (self.source / "widget.txt").write_text("original\n")
    self.git(self.source, "add", ".")
    self.git(self.source, "-c", "user.name=Fixture", "-c", "user.email=test@example.invalid",
             "commit", "-qm", "source")
    self.commit = self.git(self.source, "rev-parse", "HEAD").strip()
    self.env["SOURCE_COMMIT"] = self.commit
    self.patch = patches / "0001-widget.patch"
    (self.source / "widget.txt").write_text("patched\n")
    self.patch.write_text(self.git(self.source, "diff", "--", "widget.txt"))
    self.git(self.source, "restore", "widget.txt")
    self.git(self.root, "clone", "-q", self.source.as_uri(), str(self.op))
    (self.op / "widget.txt").write_text("keep local changes\n")
    (self.op / "cereal/custom.capnp").write_text("installed schema\n")
    (self.op / "cereal/car.capnp").write_text("installed car schema\n")
    (self.op / "cereal/extra.capnp").write_text("extra installed schema\n")
    self.stub("df", "printf 'Filesystem MB Used Available %% Mounted\\nstub 10000 0 10000 0%% /\\n'")
    self.stub("clang++", "test \"$*\" = '--version'\necho 'fixture-compiler 9.8.7'")
    self.stub("qmake", "test \"$*\" = '-query QT_VERSION'\necho '5.15.42'")
    self.stub("scons", """test "$(cat widget.txt)" = patched
test "$(cat cereal/custom.capnp)" = 'installed schema'
test -L cereal/car.capnp
test "$(cat cereal/car.capnp)" = 'installed car schema'
test "$(cat cereal/extra.capnp)" = 'extra installed schema'
printf 'built\n' >> "$BUILDER_LOG"
mkdir -p selfdrive/ui
printf 'fixture screen\n' > selfdrive/ui/ui
chmod 755 selfdrive/ui/ui""")

  def git(self, cwd, *args):
    return subprocess.check_output(["git", "-C", str(cwd), *args], env=self.env, text=True)

  def stub(self, name, body):
    path = self.tools / name
    path.write_text("#!/bin/sh\nset -eu\n" + body + "\n")
    path.chmod(0o755)

  def build(self, work=None):
    return subprocess.run(["bash", str(self.helper)], env={**self.env, "WORK": str(work or self.work)},
                          cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

  def refused(self, work=None):
    result = self.build(work)
    self.assertNotEqual(result.returncode, 0, result.stdout)
    self.assertIn("REFUSED:", result.stdout)
    self.assertFalse((self.root / "builder-invocations").exists())
    self.assertEqual((self.op / "widget.txt").read_text(), "keep local changes\n")
    self.assertEqual((self.op / "cereal/custom.capnp").read_text(), "installed schema\n")

  def test_venv_scons_wins_in_non_login_shell(self):
    venv = Path(self.env["RAMPILOT_VENV_BIN"])
    venv.mkdir(parents=True)
    (self.tools / "scons").rename(venv / "scons")
    self.stub("scons", "exit 88")
    result = self.build()
    self.assertEqual(result.returncode, 0, result.stdout)
    self.assertEqual((self.root / "builder-invocations").read_text(), "built\n")

  def test_missing_scons_exits_before_workspace_creation(self):
    (self.tools / "scons").unlink()
    for tool in ("bash", "realpath", "dirname", "cmp"):
      (self.tools / tool).symlink_to(shutil.which(tool))
    self.env["PATH"] = str(self.tools)
    result = self.build()
    self.assertEqual(result.returncode, 2, result.stdout)
    self.assertIn("scons missing", result.stdout)
    self.assertFalse(self.work.exists())

  def test_requires_known_offroad_state(self):
    for value in (None, b"", b"unknown", b"1"):
      with self.subTest(value=value):
        if value is None:
          self.onroad.unlink()
        else:
          self.onroad.write_bytes(value)
        self.refused()

  def test_refuses_existing_unrelated_checkout(self):
    self.git(self.root, "clone", "-q", self.source.as_uri(), str(self.work))
    (self.work / "widget.txt").write_text("another project\n")
    self.refused()
    self.assertEqual((self.work / "widget.txt").read_text(), "another project\n")

  def test_refuses_nonempty_unmarked_directory(self):
    self.work.mkdir()
    note = self.work / "owner-notes.txt"
    note.write_text("preserve\n")
    self.refused()
    self.assertEqual(set(self.work.iterdir()), {note})
    self.assertEqual(note.read_text(), "preserve\n")

  def test_refuses_paths_inside_or_equal_to_installed_checkout(self):
    link = self.root / "link-to-installed"
    link.symlink_to(self.op, target_is_directory=True)
    for work in (self.op, self.op / "build", link / "build"):
      with self.subTest(work=work):
        self.refused(work)
    self.assertFalse((self.op / "build").exists())

  def test_fresh_build_and_marked_repeat_record_provenance(self):
    self.work.mkdir()
    for expected_runs in (1, 2):
      result = self.build()
      self.assertEqual(result.returncode, 0, result.stdout)
      self.assertTrue((self.work / ".rampilot-ui-work-v1").is_file())
      self.assertEqual((self.root / "builder-invocations").read_text().splitlines(), ["built"] * expected_runs)
      manifests = list(self.stage.glob("ui-build-*/manifest.txt"))
      self.assertEqual(len(manifests), 1)
      manifest = manifests[0]
      self.assertEqual(manifest.parent.name, "ui-build-" + sha(manifest))
      lines = manifest.read_text().splitlines()
      self.assertIn("source_commit=" + self.commit, lines)
      self.assertIn("patch_sha256=" + sha(self.patch) + "  " + self.patch.name, lines)
      for name in ("car.capnp", "custom.capnp", "extra.capnp"):
        self.assertIn("schema_sha256=" + sha(self.op / "cereal" / name) + "  cereal/" + name, lines)
      self.assertIn("compiler=fixture-compiler 9.8.7", lines)
      self.assertIn("qt_version=5.15.42", lines)
      output_sha = hashlib.sha256(b"fixture screen\n").hexdigest()
      self.assertIn("output_sha256=" + output_sha, lines)
      binary = manifest.parent / ("ui-" + output_sha[:12])
      self.assertEqual(sha(binary), output_sha)
      self.assertTrue(os.access(binary, os.X_OK))
      self.assertEqual((self.op / "widget.txt").read_text(), "keep local changes\n")


if __name__ == "__main__":
  unittest.main()
