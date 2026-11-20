"""Fail-closed, read-only sample attribution for a recorded Ram steering warning.

This is NOT an actuator controller or a torque-cap tuning tool. It computes
aggregate coverage around an already-verified incident; does not output a
route key, exact timestamp, location, raw frame, or vehicle identifier.
"""
from __future__ import annotations
import math

from auto_review import SCHEMA_COMMIT

# Documented historical installed-build Chrysler native command ceiling.
# This is a reference for observations, NOT a proposed new vehicle setting.
RECORDED_STEER_MAX = 350


def finite_number(value):
    return (type(value) in (int, float) and math.isfinite(value))


class NativeCapExposure:
    """Causal, same-segment snapshots; missing, stale or unknown = unqualified.

    Each carOutput sample is joined to latest at-or-before carControl,
    controlsState and carState with *independent* bounded ages. All data
    must be from one source-verified R-log segment.
    """
    def __init__(self, max_age_seconds=0.03, max_native=RECORDED_STEER_MAX):
        if (not finite_number(max_age_seconds) or max_age_seconds <= 0 or
                not finite_number(max_native) or max_native <= 0):
            raise ValueError("invalid observational bounds")
        self.age = max_age_seconds
        self.ceiling = max_native
        self.segments = 0
        self.clock_invalid = False
        self.over_ceiling = 0
        self.outputs = 0
        self.at_ceiling = 0
        self.qualified_cap = 0
        self.qualified_saturated = 0
        self.qualified_not_saturated = 0
        self.missing_or_unsafe = 0
        self.runs = 0
        self.longest_run_observed = 0.0
        self._reset()

    def _reset(self):
        self.last_time = None
        self.latest = {}
        self.run_start = None
        self.run_last = None

    def segment_boundary(self):
        self.segments += 1
        self._reset()

    def _fresh(self, kind, t):
        sample = self.latest.get(kind)
        if sample is None:
            return None
        stamp, data = sample
        return data if 0 <= t - stamp <= self.age else None

    def _end_run(self):
        if self.run_start is not None:
            self.longest_run_observed = max(
                self.longest_run_observed, self.run_last - self.run_start)
        self.run_start = self.run_last = None

    def observe(self, kind, t, data):
        if not self.segments or not finite_number(t) or not isinstance(data, dict):
            self.clock_invalid = True
            return
        if self.last_time is not None and t < self.last_time:
            # Never sort or borrow a later-arriving record to prove causality.
            self.clock_invalid = True
            return
        self.last_time = t
        if kind in ("carControl", "controlsState", "carState"):
            self.latest[kind] = (t, data)
            return
        if kind != "carOutput":
            return
        native = data.get("torqueOutputCan")
        if not finite_number(native):
            self._end_run()
            return
        self.outputs += 1
        if abs(native) > self.ceiling:
            self.over_ceiling += 1
        at_cap = abs(native) >= self.ceiling
        if not at_cap:
            self._end_run()
            return
        self.at_ceiling += 1
        cc = self._fresh("carControl", t)
        cs = self._fresh("controlsState", t)
        car = self._fresh("carState", t)
        eligible = (cc is not None and cc.get("latActive") is True
                    and cs is not None and type(cs.get("saturated")) is bool
                    and car is not None
                    and car.get("canValid") is True
                    and car.get("steerFaultTemporary") is False
                    and car.get("steerFaultPermanent") is False
                    and car.get("steeringPressed") is False)
        if not eligible:
            self.missing_or_unsafe += 1
            self._end_run()
            return
        self.qualified_cap += 1
        if cs["saturated"]:
            self.qualified_saturated += 1
        else:
            self.qualified_not_saturated += 1
        if self.run_start is None or t - self.run_last > self.age:
            self._end_run()
            self.runs += 1
            self.run_start = t
        self.run_last = t

    def report(self):
        self._end_run()
        valid = not self.clock_invalid and not self.over_ceiling and self.segments > 0
        return {
            "status": "qualified_observational_coverage" if valid else "rejected_source_clock_or_ceiling",
            "sourceCommit": SCHEMA_COMMIT,
            "rlogSegmentsSeparated": self.segments,
            "commandSamples": self.outputs,
            "atDocumentedNativeCeilingSamples": self.at_ceiling,
            "overDocumentedCeilingSamples": self.over_ceiling,
            "qualifiedCommaLateralAtCeilingSamples": self.qualified_cap if valid else None,
            "qualifiedWithControllerSaturated": self.qualified_saturated if valid else None,
            "qualifiedWithoutControllerSaturated": self.qualified_not_saturated if valid else None,
            "unqualifiedAtCeilingSamples": self.missing_or_unsafe,
            "qualifiedCapSampleRuns": self.runs if valid else None,
            "longestQualifiedRunObservedSeconds":
                round(self.longest_run_observed, 3) if valid and self.runs else None,
            "maxAsOfAgeMilliseconds": round(self.age * 1000, 2),
            "nativeCommandCeilingSourceReference": self.ceiling,
            "caution": (
                "Same-segment past-only snapshots, not proven synchronous actuator cycles. "
                "R-log cap duration is sampled exposure, not EPS acceptance or motor torque. "
                "Controller saturation and native ceiling can differ; no steering-limit change authorized."
            ),
            "noVehicleWrite": True,
        }
