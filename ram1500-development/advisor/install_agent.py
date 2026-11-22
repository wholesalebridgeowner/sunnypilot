#!/usr/bin/env python3
"""Install or remove the Drive Advisor startup hook on the Comma.

/data/continue.sh is the Comma's sunnypilot launcher. The hook is ONE line,
placed before `exec ./launch_openpilot.sh`, that starts the agent in the
background only if its launcher exists and is executable. A missing or failing
agent cannot stop sunnypilot from launching. The original file is backed up
first and restored byte-for-byte by `uninstall`. Any continue.sh that is not the
known original (or the known hooked version) is refused.

The start script discards the agent's output, so an agent that cannot import
one of its modules would exit without a trace. AGENT_PAYLOAD therefore names
every file the agent needs beside it, `install` refuses while one of them is
missing, and `payload` prints the list for whoever copies the files.

Usage on the Comma:  python3 install_agent.py install|uninstall|status|payload
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CONTINUE = Path("/data/continue.sh")
HOME = Path("/data/ram-diag/advisor")
ORIGINAL = b"#!/usr/bin/bash\n\ncd /data/openpilot\nexec ./launch_openpilot.sh\n"
EXEC_LINE = b"exec ./launch_openpilot.sh\n"
HOOK = (b"( [ -x /data/ram-diag/advisor/start_agent.sh ] && exec /data/ram-diag/advisor/start_agent.sh )"
        b" </dev/null >/dev/null 2>&1 &\n")
HOOKED = ORIGINAL.replace(EXEC_LINE, HOOK + EXEC_LINE)
# The agent imports one helper of the build (the delay probe). With bytecode
# writing switched off it leaves no file under /data/openpilot, or anywhere else.
START_SCRIPT = b"""#!/usr/bin/bash
# Drive Advisor agent launcher, started in the background by /data/continue.sh.
# Also starts the log keeper when it is installed (owner GO 2026-11-21), detached, lowest priority.
[ -x /data/ram-diag/keeper/keep_logs.sh ] && ( setsid nice -n 19 /data/ram-diag/keeper/keep_logs.sh </dev/null >/dev/null 2>&1 & )
cd /data/openpilot || exit 0
export PYTHONPATH=/data/openpilot:/data/ram-diag/advisor
export PYTHONDONTWRITEBYTECODE=1
exec setsid nice -n 10 /usr/local/venv/bin/python3 /data/ram-diag/advisor/agent.py </dev/null >/dev/null 2>&1
"""
# Every file the agent needs on the Comma, and nothing else: no test, no service, no replay tool.
AGENT_PAYLOAD = (
    "agent.py",
    "applier.py",
    "approval.py",
    "dash_relay.py",
    "delay_probe.py",
    "device_params.py",
    "gates.py",
    "journal.py",
    "measure_api.py",
    "measure_brake.py",
    "measure_curve.py",
    "measure_delay.py",
    "measure_lane_change.py",
    "measure_lane_sway.py",
    "measure_lat_rms.py",
    "measure_limit.py",
    "measure_set.py",
    "measure_steering.py",
    "measure_steering_loss.py",
    "measure_swings.py",
    "messages.py",
    "owner_apply.py",
    "owner_receiver.py",
    "owner_settings.py",
    "parking_live.py",
    "parking_raw.py",
    "pipeline.py",
    "samples.py",
    "settings_guard.py",
    "settings_snapshot.py",
    "shared_rules.py",
    "uplink.py",
    "wire.py",
)
INSTALLER = "install_agent.py"


class InstallError(RuntimeError):
    pass


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".advisor-tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def hook_status(continue_path: Path = CONTINUE) -> str:
    data = Path(continue_path).read_bytes()
    if data == ORIGINAL:
        return "original"
    if data == HOOKED:
        return "installed"
    return "unknown"


def install_hook(continue_path: Path = CONTINUE, backup_path: Path = HOME / "continue.sh.orig",
                 *, launcher_path: Path | None = None) -> str:
    continue_path, backup_path = Path(continue_path), Path(backup_path)
    status = hook_status(continue_path)
    if status not in ("original", "installed"):
        raise InstallError("unknown_continue_script")
    if not backup_path.exists():
        _atomic_write(backup_path, ORIGINAL, 0o644)
    if backup_path.read_bytes() != ORIGINAL:
        raise InstallError("backup_mismatch")
    # Prepare the launcher only after the hook and rollback bytes have been checked.
    if launcher_path is not None:
        write_start_script(launcher_path)
    if status == "installed":
        return "already_installed"
    _atomic_write(continue_path, HOOKED, 0o755)
    if hook_status(continue_path) != "installed":
        raise InstallError("verify_failed")
    return "installed"


def uninstall_hook(continue_path: Path = CONTINUE, backup_path: Path = HOME / "continue.sh.orig") -> str:
    continue_path = Path(continue_path)
    status = hook_status(continue_path)
    if status == "original":
        return "not_installed"
    if status != "installed":
        raise InstallError("unknown_continue_script")
    _atomic_write(continue_path, ORIGINAL, 0o755)
    if hook_status(continue_path) != "original":
        raise InstallError("verify_failed")
    return "uninstalled"


def write_start_script(path: Path = HOME / "start_agent.sh") -> None:
    _atomic_write(Path(path), START_SCRIPT, 0o755)


def missing_payload(home: Path = HOME) -> list:
    """Names of the agent files that are not in `home`."""
    return sorted(name for name in AGENT_PAYLOAD if not (Path(home) / name).is_file())


def extra_modules(home: Path = HOME) -> list:
    """Python files in `home` that this agent does not use: left behind by an earlier version."""
    known = set(AGENT_PAYLOAD) | {INSTALLER}
    return sorted(path.name for path in Path(home).glob("*.py") if path.name not in known)


def main(argv: list) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    backup = HOME / "continue.sh.orig"
    try:
        if command == "install":
            missing = missing_payload(HOME)
            if missing:
                raise InstallError("missing_agent_files " + " ".join(missing))
            print(install_hook(CONTINUE, backup, launcher_path=HOME / "start_agent.sh"))
            extra = extra_modules(HOME)
            if extra:
                print("not_part_of_this_agent " + " ".join(extra))
        elif command == "uninstall":
            print(uninstall_hook(CONTINUE, backup))
        elif command == "status":
            print(hook_status(CONTINUE))
        elif command == "payload":
            print("\n".join(AGENT_PAYLOAD))
        else:
            print("usage: install_agent.py install|uninstall|status|payload")
            return 2
    except InstallError as error:
        print(f"refused: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
