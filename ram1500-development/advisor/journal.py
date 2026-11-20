"""Crash-safe records for the applier.

Journal: what a live trial replaced, so it can always be put back.
NonceLedger: approvals already used, so none can be replayed after a restart.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class JournalError(RuntimeError):
    """A second trial was started while one is open."""


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)

    def is_open(self) -> bool:
        return self.path.exists()

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def open_trial(self, *, suggestion_id: str, previous: dict, written: dict) -> None:
        if self.is_open():
            raise JournalError("trial_already_open")
        _atomic_write(self.path, {"suggestion_id": suggestion_id, "previous": previous, "written": written})

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


class NonceLedger:
    MAX = 500

    def __init__(self, path: Path):
        self.path = Path(path)

    def _items(self) -> list:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as handle:
            return list(json.load(handle))

    def used(self) -> set:
        return set(self._items())

    def consume(self, nonce: str) -> None:
        items = [n for n in self._items() if n != nonce] + [nonce]
        _atomic_write(self.path, items[-self.MAX:])
