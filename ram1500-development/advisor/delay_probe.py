"""The delay probe: does a stored fixed steering delay reach steering on the
build that is running?

The build has one small helper that picks the steering delay. The probe calls
it with stand-in settings that say "learning is switched off, the stored total
is 0.40 s" and a learned delay of 0.55 s, and looks at which of the two comes
back:

    inert       0.55: the learned delay is used either way. The stored fixed
                delay and the learning switch do not change steering.
    effective   0.40: the stored value would steer. A build with the upstream fix.
    unknown     anything else, any failure, or a probe that took over a second.

It reads and runs that one helper and nothing else: no setting is read or
written, and the stand-in is handed to the helper in place of the real
settings. The result is recorded for every drive, feeds two notices of the
nightly report, and is the build gate of the applier (lock 3).

The probe runs at each drive start, after the wait before listening, never at
boot. The agent switches bytecode writing off before anything is imported from
the build, so importing the helper leaves no file behind.
"""
from __future__ import annotations

import hashlib
import importlib
import time

HELPER_MODULE = "openpilot.sunnypilot.livedelay.helpers"
PROBE_STOCK_S = 0.55          # handed over as the learned delay
PROBE_CACHE_S = 0.40          # what the stand-in settings give for every read
PROBE_LIMIT_MS = 1000         # a slower probe is not believed
INERT, EFFECTIVE, UNKNOWN = "inert", "effective", "unknown"


class _StandInSettings:
    """Learning switched off, every other read 0.40."""

    def get_bool(self, key, *args, **kwargs) -> bool:
        return False

    def get(self, key, *args, **kwargs) -> float:
        return PROBE_CACHE_S


def _import_helper():
    return importlib.import_module(HELPER_MODULE)


class DelayProbe:
    """One per agent process: the helper is imported by the first run that succeeds and reused after that."""

    def __init__(self, importer=None, monotonic=time.monotonic):
        self._importer = importer or _import_helper
        self._monotonic = monotonic
        self._module = None

    def run(self) -> dict:
        """{"result": inert / effective / unknown, "helperSha": 12 hex or None, "probeMs": int}."""
        started = self._monotonic()
        try:
            if self._module is None:
                self._module = self._importer()
            module = self._module
            answer = module.get_lat_delay(_StandInSettings(), PROBE_STOCK_S)
            if isinstance(answer, bool):
                result = UNKNOWN
            else:
                result = INERT if answer == PROBE_STOCK_S else EFFECTIVE if answer == PROBE_CACHE_S else UNKNOWN
            with open(module.__file__, "rb") as handle:
                helper_sha = hashlib.sha256(handle.read()).hexdigest()[:12]
        except BaseException:           # nothing the build's code does may stop the agent
            result, helper_sha = UNKNOWN, None
        probe_ms = int(round(1000.0 * (self._monotonic() - started)))
        if probe_ms > PROBE_LIMIT_MS:
            result = UNKNOWN
        return {"result": result, "helperSha": helper_sha, "probeMs": probe_ms}
