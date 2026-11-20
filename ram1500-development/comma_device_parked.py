#!/usr/bin/env python3
"""Phone/Termius one-shot PARKED Comma diagnostics install, executed ON Comma.

No shell from Railway is assumed. This is run via the owner's authenticated SSH
session. It can also be driven by a Raspberry Pi with private SSH reachability.
Only updates three passive diagnostic utilities under /data/ram-diag; NEVER
modifies /data/openpilot, steering/ACC controllers, Panda, or vehicle settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

SOURCE_COMMIT = "dc94a83a121d7e53d7605973cb2efae3719f140d"
EXPECTED_DEVICE_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
SOURCE_DIR = "https://raw.githubusercontent.com/wholesalebridgeowner/sunnypilot/" + SOURCE_COMMIT + "/ram1500-development/"
PINNED_FILES = {
    "device_telemetry.py": "af3e8e4d832bf378820026b41fc1eb96e563b0ed",
    "parked_capture_acceptance.py": "972dcef6eac3ee5345be7b31d5b5391e850daf40",
    "summarize_can_capture.py": "6c28788d3d7b4278c5e6f5b5e2cc7be6e700ac2a",
}
ROOT = Path("/data/ram-diag")
BACKUPS = ROOT / "backups"
BACKUP_MARKER = ROOT / "last-diagnostic-backup"
CAPTURES = Path("/data/ram-telemetry")
PYTHON = Path("/usr/local/venv/bin/python3")
MAX_SOURCE_BYTES = 400000


class NoGo(RuntimeError):
    pass


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def source_preflight(*, installed_commit: str, dirty: bool, arch: str, free_bytes: int) -> list[str]:
    issues = []
    if installed_commit != EXPECTED_DEVICE_COMMIT:
        issues.append("installed_build_mismatch")
    if dirty:
        issues.append("installed_git_tree_modified")
    if arch != "aarch64":
        issues.append("device_architecture_mismatch")
    if free_bytes < 1024 * 1024 * 1024:
        issues.append("insufficient_data_storage")
    return issues


def run_safe(args: list[str], *, env=None, timeout=15) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False,
                          timeout=timeout, env=env)


def preflight() -> dict:
    if not PYTHON.is_file() or not Path("/data/openpilot/.git").exists():
        raise NoGo("installed_device_runtime_unavailable")
    actual = run_safe(["git", "-C", "/data/openpilot", "rev-parse", "HEAD"])
    status = run_safe(["git", "--no-optional-locks", "-C", "/data/openpilot",
                       "status", "--porcelain"])
    if actual.returncode or status.returncode:
        raise NoGo("installed_version_read_failed")
    try:
        free = shutil.disk_usage("/data").free
    except OSError:
        raise NoGo("device_storage_unavailable")
    issues = source_preflight(
        installed_commit=actual.stdout.strip(),
        dirty=bool(status.stdout.strip()),
        arch=platform.machine(),
        free_bytes=free,
    )
    if issues:
        raise NoGo(";".join(issues))
    if ROOT.exists() and (ROOT.is_symlink() or not ROOT.is_dir()):
        raise NoGo("diagnostic_directory_unsafe")
    return {"status": "parked_device_preflight_pass",
            "installedSourceVerified": True,
            "freeStorageGB": round(free / (1024 ** 3), 1),
            "vehicleCodeChanged": False}


def fetch_pinned(name: str, expected_sha: str) -> bytes:
    if name not in PINNED_FILES or PINNED_FILES[name] != expected_sha:
        raise NoGo("unrecognized_pinned_diagnostic_file")
    try:
        with urlopen(SOURCE_DIR + name, timeout=25) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
    except Exception:
        raise NoGo("trusted_source_download_failed")
    if len(raw) > MAX_SOURCE_BYTES or git_blob_sha(raw) != expected_sha:
        raise NoGo("pinned_diagnostic_integrity_mismatch")
    try:
        compile(raw.decode("utf-8"), name, "exec")
    except (SyntaxError, UnicodeDecodeError):
        raise NoGo("invalid_diagnostic_python_source")
    return raw


def ensure_private_dir(directory: Path) -> None:
    if directory.is_symlink():
        raise NoGo("diagnostic_symlink_refused")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)


def install() -> dict:
    preflight()
    ensure_private_dir(ROOT)
    ensure_private_dir(BACKUPS)
    if BACKUP_MARKER.exists() or BACKUP_MARKER.is_symlink():
        raise NoGo("diagnostic_install_already_staged")
    # Download + validate all three scripts before touching existing scripts.
    files = {name: fetch_pinned(name, expected)
             for name, expected in PINNED_FILES.items()}
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=ROOT))
    backup = None
    try:
        for name, raw in files.items():
            target = staging / name
            with target.open("xb") as out:
                out.write(raw)
            target.chmod(0o600)
        backup = Path(tempfile.mkdtemp(prefix="backup-", dir=BACKUPS))
        for name in PINNED_FILES:
            old = ROOT / name
            if old.is_symlink():
                raise NoGo("existing_diagnostic_file_symlink_refused")
            if old.exists():
                shutil.copy2(old, backup / name)
            else:
                (backup / (name + ".was_absent")).touch(mode=0o600)
        # Persist the rollback path BEFORE replacing any file.
        BACKUP_MARKER.write_text(str(backup) + "\n", encoding="utf-8")
        BACKUP_MARKER.chmod(0o600)
        for name in PINNED_FILES:
            (staging / name).replace(ROOT / name)
    finally:
        # Cleanup never touches the original /data/openpilot tree.
        shutil.rmtree(staging, ignore_errors=True)
    return {"status": "diagnostic_files_installed",
            "pinnedSource": SOURCE_COMMIT,
            "diagnosticScripts": len(PINNED_FILES),
            "captureStarted": False, "vehicleCodeChanged": False,
            "rollbackAvailable": backup is not None}


def validate_capture_slots():
    sys.path.insert(0, str(ROOT))
    import device_telemetry as telemetry
    counts = telemetry.msgq_reader_counts(telemetry.SERVICES)
    bad = [name for name in telemetry.SERVICES
           if type(counts.get(name)) is not int or counts[name] >= telemetry.NUM_READERS]
    if bad:
        raise NoGo("subscriber_slots_unavailable_or_full")
    return len(counts)


def capture() -> dict:
    preflight()
    if not all((ROOT / name).is_file() for name in PINNED_FILES):
        raise NoGo("diagnostic_install_not_found")
    # No retries until reboot. This preflight creates NO subscribers.
    services = validate_capture_slots()
    ensure_private_dir(CAPTURES)
    session = Path(tempfile.mkdtemp(prefix="session-", dir=CAPTURES))
    env = dict(os.environ)
    env["PYTHONPATH"] = "/data/openpilot"
    command = [
        "timeout", "150", str(PYTHON), str(ROOT / "device_telemetry.py"),
        "--output-dir", str(session), "--minutes", "2", "--sample-hz", "2",
        "--max-mb", "8", "--keep-files", "2",
        "--max-cpu-pct", "15", "--max-private-mb", "150", "--drain-ms", "50",
    ]
    try:
        completed = run_safe(command, env=env, timeout=165)
    except subprocess.TimeoutExpired:
        raise NoGo("parked_trial_hard_timeout")
    if completed.returncode:
        raise NoGo("parked_trial_failed")
    files = sorted(session.glob("ram-telemetry-*.jsonl"))
    if not files:
        raise NoGo("parked_trial_no_local_capture")
    result = run_safe([str(PYTHON), str(ROOT / "parked_capture_acceptance.py"),
                       *(str(f) for f in files),
                       "--output", str(session / "acceptance.json")], timeout=30)
    try:
        summary = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        raise NoGo("parked_trial_acceptance_unreadable")
    if not isinstance(summary, dict):
        raise NoGo("parked_trial_acceptance_invalid")
    # Report only the allowlisted acceptance data; raw telemetry remains local.
    return {"status": "parked_trial_accepted" if result.returncode == 0 else "parked_trial_rejected",
            "subscriberServicesPrechecked": services,
            "acceptance": summary,
            "vehicleCodeChanged": False}


def rollback() -> dict:
    if not BACKUP_MARKER.is_file() or BACKUP_MARKER.is_symlink():
        raise NoGo("diagnostic_backup_marker_missing")
    backup = Path(BACKUP_MARKER.read_text(encoding="utf-8").strip())
    if not backup.is_relative_to(BACKUPS) or not backup.is_dir() or backup.is_symlink():
        raise NoGo("diagnostic_backup_path_invalid")
    # Validate backup completeness before modifying any currently installed script.
    for name in PINNED_FILES:
        if not (backup / name).is_file() and not (backup / (name + ".was_absent")).exists():
            raise NoGo("incomplete_diagnostic_backup")
    for name in PINNED_FILES:
        original, absent = backup / name, backup / (name + ".was_absent")
        target = ROOT / name
        if target.is_symlink():
            raise NoGo("diagnostic_symlink_refused")
        if original.exists():
            shutil.copy2(original, target)
        elif absent.exists():
            target.unlink(missing_ok=True)
    BACKUP_MARKER.replace(backup / "rollback-complete")
    return {"status": "diagnostic_files_restored",
            "note": "Subscriber slots may still require a parked reboot before another trial",
            "vehicleCodeChanged": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "install", "capture", "rollback"))
    parser.add_argument("--parked", action="store_true")
    parser.add_argument("--approve-diagnostic-install", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode != "preflight" and not (
            args.parked and args.approve_diagnostic_install):
            raise NoGo("parked_owner_approval_required")
        output = {
            "preflight": preflight, "install": install,
            "capture": capture, "rollback": rollback,
        }[args.mode]()
        print(json.dumps(output, separators=(",", ":"), allow_nan=False))
        return 0 if output.get("status") != "parked_trial_rejected" else 2
    except NoGo as exc:
        print(json.dumps({"status": "no_go", "reason": str(exc),
                          "vehicleCodeChanged": False}))
        return 2
    except Exception:
        # Never surface untrusted file/HTTP/CAN exceptions that could embed raw
        # vehicle identifiers or installation paths in the public chat.
        print(json.dumps({"status": "no_go", "reason": "unexpected_local_error",
                          "vehicleCodeChanged": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
