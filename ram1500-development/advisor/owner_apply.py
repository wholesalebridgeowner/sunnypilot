"""Apply the exact settings that the owner approved, with a durable record of the old values."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from approval import ApprovalError, authenticate_approval, check_approval_lifetime
from journal import _atomic_write
from owner_settings import OWNER_NATIVE_LOCKS, PROTECTIVE_VALUES, validate_owner_change
from settings_guard import GuardError


def _sync_directory(directory):
    # The device runs Linux. Sync the rename itself as well as the file contents.
    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class OwnerApplier:
    """Single writer under agent.lock. Keep each completed approval and any interrupted transaction."""

    def __init__(self, params, directory, key, trial_journal):
        self.params = params
        self.directory = Path(directory)
        self.key = key
        self.trial_journal = trial_journal
        self.pending = self.directory / "pending.json"

    def _receipt_path(self, nonce):
        return self.directory / (hashlib.sha256(nonce.encode()).hexdigest() + ".json")

    def authenticate(self, approval):
        fields = authenticate_approval(self.key, approval)
        if fields["mode"] != "owner":
            raise ApprovalError("owner_approval_required")
        if not re.fullmatch(r"[0-9a-f]{16}", fields["suggestion_id"]):
            raise ApprovalError("malformed")
        return fields

    def _locked(self, name):
        lock = OWNER_NATIVE_LOCKS.get(name)
        return lock is not None and self.params.stored(lock) is True

    def _prepare_directory(self):
        if not self.directory.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            _sync_directory(self.directory.parent)

    def _remember_refusal(self, fields, reason):
        self._prepare_directory()
        status = "refused:" + reason
        _atomic_write(self._receipt_path(fields["nonce"]), {**fields, "status": status})
        _sync_directory(self.directory)
        return status

    def _finish(self, entry, status):
        entry["status"] = status
        _atomic_write(self.pending, entry)
        os.replace(self.pending, self._receipt_path(entry["nonce"]))
        _sync_directory(self.directory)
        return status

    def recover(self, safe_to_write):
        if not self.pending.exists():
            return None
        entry = json.loads(self.pending.read_text(encoding="utf-8"))
        if "status" in entry:
            return self._finish(entry, entry["status"])
        if not safe_to_write():
            return "refused:not_parked"
        if all(self.params.stored(k) == v for k, v in entry["change"].items()):
            return self._finish(entry, "applied")
        # Only undo our values. Preserve any separate change made on the screen.
        retained = []
        for name, wanted in entry["change"].items():
            if not safe_to_write():
                return "refused:not_parked"
            current = self.params.stored(name)
            previous = entry["previous"][name]
            if current != wanted or current == previous:
                continue
            if self._locked(name) or name in PROTECTIVE_VALUES:
                # A failed group must never reinstate a less protective setting
                # or write through a lock the owner has just enabled locally.
                retained.append(name)
                continue
            if previous is None:
                self.params.remove(name)
            else:
                self.params.put(name, previous)
            if self.params.stored(name) != previous:
                raise OSError("owner rollback did not persist")
        if retained:
            entry["retained"] = retained
        return self._finish(entry, "refused:partial_owner_change" if retained else "refused:interrupted_owner_change")

    def apply(self, approval, safe_to_write, *, now, delay_probe="unknown"):
        try:
            fields = self.authenticate(approval)
        except ApprovalError as exc:
            return f"refused:{exc}"
        path = self._receipt_path(fields["nonce"])
        if self.pending.exists():
            self.recover(safe_to_write)
            if self.pending.exists():
                return "refused:not_parked"
        if path.exists():
            # A lost response can cause redelivery. Acknowledge the stored outcome
            # without applying again, even if the screen value has since changed.
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry["suggestion_id"] != fields["suggestion_id"] or entry["change"] != fields["change"]:
                return "refused:already_used"
            return entry["status"]
        try:
            check_approval_lifetime(fields, now)
            validate_owner_change(fields["change"])
        except ApprovalError as exc:
            return self._remember_refusal(fields, str(exc))
        except GuardError:
            return self._remember_refusal(fields, "invalid_owner_change")
        if not safe_to_write():
            return "refused:not_parked"
        if self.trial_journal.is_open():
            return self._remember_refusal(fields, "trial_already_active")
        change = fields["change"]
        if any(self._locked(name) for name in change):
            return self._remember_refusal(fields, "native_setting_locked")
        if ("LagdToggleDelay" in change or "LagdToggle" in change) and delay_probe != "effective":
            return self._remember_refusal(fields, "delay_inert_on_this_build")
        self._prepare_directory()
        previous = {name: self.params.stored(name) for name in change}
        entry = {**fields, "previous": previous}
        _atomic_write(self.pending, entry)
        _sync_directory(self.directory)
        try:
            for name, value in change.items():
                if not safe_to_write():
                    return "refused:not_parked"
                if self._locked(name):
                    return self.recover(safe_to_write)
                self.params.put(name, value)
                if self.params.stored(name) != value:
                    raise OSError("owner setting did not persist")
        except Exception:
            return self.recover(safe_to_write)
        return self._finish(entry, "applied")
