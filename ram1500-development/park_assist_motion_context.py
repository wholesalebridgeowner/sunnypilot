#!/usr/bin/env python3
"""Read-only vehicle-motion context for supervised OEM park-assist capture.

The separate CAN comparison only identifies traffic changes. This module
measures reported speed, steering-wheel motion and openpilot lateral activity
from already recorded, pinned-schema R-log messages. It cannot determine
whether OEM park assist was activated without a reliable driver annotation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import io
import json
from pathlib import Path
from statistics import median

from park_assist_phase_windows import parse_window

NS = 1_000_000_000


def finite_number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    import math
    return float(v) if math.isfinite(v) else None


def describe(records, normal, parking):
    a, b = normal
    c, d = parking
    if not (a < b <= c < d or c < d <= a < b):
        raise ValueError("windows must not overlap")
    # Input rows: (monoNs, service, selected privacy-safe scalar dict).
    anchor = None
    stats = {"normal": [], "factoryParking": []}
    last_time = None
    for t, service, values in records:
        t = int(t)
        if last_time is not None and t < last_time:
            raise ValueError("records must be chronological")
        last_time = t
        if anchor is None:
            # Same anchor as park_assist_phase_windows: the first valid CAN event.
            if service not in ("can", "sendcan"):
                continue
            anchor = t
        seconds = (t - anchor) / NS
        if a <= seconds < b:
            stats["normal"].append((service, values))
        elif c <= seconds < d:
            stats["factoryParking"].append((service, values))
    def summarize(rows):
        cs = [r for service, r in rows if service == "carState"]
        commands = [r for service, r in rows if service == "carControl"]
        outputs = [r for service, r in rows if service == "carOutput"]
        speeds = [finite_number(r.get("vEgo")) for r in cs]
        speeds = [v for v in speeds if v is not None]
        angles = [finite_number(r.get("steeringAngleDeg")) for r in cs]
        angles = [x for x in angles if x is not None]
        eps = [finite_number(r.get("steeringTorqueEps")) for r in cs]
        eps = [abs(x) for x in eps if x is not None]
        request = [finite_number(r.get("torqueOutputCan")) for r in outputs]
        request = [abs(x) for x in request if x is not None]
        active = Counter(str(r.get("latActive")) for r in commands)
        return {
            "carStateSamples": len(cs),
            "speedSamples": len(speeds),
            "reportedStopSamples": sum(x <= .3 for x in speeds),
            "medianSpeedMps": round(median(speeds), 3) if speeds else None,
            "minSpeedMps": round(min(speeds), 3) if speeds else None,
            "maxSpeedMps": round(max(speeds), 3) if speeds else None,
            "steeringAngleSampleCount": len(angles),
            "steeringAngleRangeDeg": round(max(angles)-min(angles), 2) if angles else None,
            "maxAbsEPSFeedbackRaw": round(max(eps), 2) if eps else None,
            "carControlLateralActiveCounts": dict(active),
            "outputSamples": len(request),
            "maxAbsCommaTorqueCommandNative": round(max(request), 2) if request else None,
        }
    return {"normal": summarize(stats["normal"]),
            "factoryParking": summarize(stats["factoryParking"]),
            "timeReference": "first message of supplied chronological R-logs; NOT wall clock",
            "caveats": [
                "FactoryParking is a driver-annotated window, NOT an inferred OEM parking state.",
                "Steering angle changing at reported zero speed demonstrates motion, not which ECU commanded it.",
                "EPS motor feedback and comma LKAS native torque are distinct; do not subtract them.",
                "Reported vEgo <= 0.3 m/s is a diagnostic stopped band; not proof wheels were exactly still.",
                "No raw CAN frames, GPS positions, VIN, absolute timestamps or identifiers exported.",
                "Do not replay captured OEM parking messages or bypass steering authorization.",
            ]}


def iter_scalar_events(files, schema):
    import capnp
    import zstandard
    p = Path(schema)
    sch = capnp.load(str(p), imports=[str(p.parent)])
    for file in files:
        with Path(file).open("rb") as infile:
            with zstandard.ZstdDecompressor().stream_reader(infile) as reader:
                data = reader.read()
        for e in sch.Event.read_multiple_bytes(data):
            if not e.valid:
                continue
            svc = e.which()
            if svc == "carState":
                x = e.carState
                yield int(e.logMonoTime),svc,{"vEgo":x.vEgo,"steeringAngleDeg":x.steeringAngleDeg,
                                            "steeringTorqueEps":x.steeringTorqueEps}
            elif svc == "carControl":
                x = e.carControl
                yield int(e.logMonoTime),svc,{"latActive":x.latActive}
            elif svc == "carOutput":
                x = e.carOutput
                yield int(e.logMonoTime),svc,{"torqueOutputCan":x.actuatorsOutput.torqueOutputCan}
            elif svc in ("can", "sendcan") and len(getattr(e,svc)):
                # Anchor both analysis tools to the first valid CAN-bearing event.
                yield int(e.logMonoTime),svc,{}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", type=Path, required=True)
    ap.add_argument("--logs", type=Path, nargs="+", required=True)
    ap.add_argument("--normal", type=parse_window, required=True)
    ap.add_argument("--oem-parking", type=parse_window, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if not args.schema.is_file() or not all(p.is_file() for p in args.logs):
        ap.error("missing pinned schema or R-log")
    if args.out.resolve().is_relative_to(Path(__file__).resolve().parent.parent):
        ap.error("private-derived summary must not be written to GitHub repository")
    try:
        result = describe(iter_scalar_events(args.logs, args.schema), args.normal, args.oem_parking)
    except ValueError as exc:
        ap.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+"\n",encoding="utf8")
    print(json.dumps({"normalSamples":result["normal"]["carStateSamples"],
                      "parkingSamples":result["factoryParking"]["carStateSamples"]}))


if __name__ == "__main__":
    main()
