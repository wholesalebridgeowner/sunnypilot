#!/usr/bin/env python3
"""Install or remove the Drive Advisor startup hook on the Comma.

/data/continue.sh is the Comma's sunnypilot launcher. The hook is ONE line,
placed before `exec ./launch_openpilot.sh`, that starts the agent in the
background only if its launcher exists and is executable. A missing or failing
agent cannot stop sunnypilot from launching. The original file is backed up
first and restored byte-for-byte by `uninstall`. Any continue.sh that is not the
known original (or the known hooked version) is refused.

Usage on the Comma:  python3 install_agent.py install|uninstall|status
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
START_SCRIPT = b"""#!/usr/bin/bash
# Drive Advisor agent launcher, started in the background by /data/continue.sh.
cd /data/openpilot || exit 0
export PYTHONPATH=/data/openpilot:/data/ram-diag/advisor
exec setsid nice -n 10 /usr/local/venv/bin/python3 /data/ram-diag/advisor/agent.py </dev/null >/dev/null 2>&1
"""


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


def install_hook(continue_path: Path = CONTINUE, backup_path: Path = HOME / "continue.sh.orig") -> str:
    continue_path, backup_path = Path(continue_path), Path(backup_path)
    status = hook_status(continue_path)
    if status == "installed":
        return "already_installed"
    if status != "original":
        raise InstallError("unknown_continue_script")
    if not backup_path.exists():
        _atomic_write(backup_path, ORIGINAL, 0o644)
    if backup_path.read_bytes() != ORIGINAL:
        raise InstallError("backup_mismatch")
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


def main(argv: list) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    try:
        if command == "install":
            write_start_script()
            print(install_hook())
        elif command == "uninstall":
            print(uninstall_hook())
        elif command == "status":
            print(hook_status())
        else:
            print("usage: install_agent.py install|uninstall|status")
            return 2
    except InstallError as error:
        print(f"refused: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
