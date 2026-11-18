#!/usr/bin/env python3
"""Offline passive OEM automatic-parking vs ordinary CAN traffic comparison.

Read only previously recorded private R-logs. Never transmits, decodes unknown
actuator commands, modifies vehicle software or emits CAN payloads. Uses the
exact recorded device build cereal schema (verified separately before use).
"""
from __future__ import annotations

import argparse
from collections import Counter
import io
import json
from pathlib import Path


def analyze(frames, *, duration=None):
    """frames: (timestamp_ns, service, bus, CAN address, payload bytes)."""
    counts, changes, comparisons = Counter(), Counter(), Counter()
    previous, times = {}, []
    rejected = 0
    for record in frames:
        try:
            ts, service, bus, address, data = record
            ts, bus, address = int(ts), int(bus), int(address)
            data = bytes(data)
            if (ts < 0 or service not in ("can", "sendcan") or
                not 0 <= bus <= 255 or not 0 <= address <= 0x1fffffff or
                len(data) > 64):
                raise ValueError("bad frame")
        except (ValueError, TypeError, OverflowError):
            rejected += 1
            continue
        key = (service, bus, address)
        counts[key] += 1
        times.append(ts)
        if key in previous and len(previous[key]) == len(data):
            comparisons[key] += 1
            if previous[key] != data:
                changes[key] += 1
        previous[key] = data
    if duration is None:
        duration = (max(times) - min(times)) / 1e9 if times else 0.
    if duration < 0:
        raise ValueError("negative observation duration")
    result = {
        key: {"frames": n,
              "hz": round(n / duration, 2) if duration else None,
              "changeFraction": round(changes[key] / comparisons[key], 3)
                                 if comparisons[key] else None}
        for key, n in counts.items()
    }
    return result, {"durationSeconds": round(duration, 3),
                    "frameCount": sum(counts.values()),
                    "busIdCount": len(result), "rejected": rejected}


def compare(normal, parking, *, minimum_frames=10):
    """Compare two observed phases, with no raw packet data in output."""
    if minimum_frames < 2:
        raise ValueError("minimum_frames must be >= 2")
    a, sa = analyze(normal)
    b, sb = analyze(parking)
    keys = sorted(set(a) | set(b))
    observed = []
    for key in keys:
        n, p = a.get(key), b.get(key)
        normal_n = n["frames"] if n else 0
        park_n = p["frames"] if p else 0
        if max(normal_n, park_n) < minimum_frames:
            continue
        hz0 = (n or {}).get("hz")
        hz1 = (p or {}).get("hz")
        c0 = (n or {}).get("changeFraction")
        c1 = (p or {}).get("changeFraction")
        if hz0 is None or hz1 is None:
            rate_ratio = None
        elif hz0 == 0:
            rate_ratio = None
        else:
            rate_ratio = round(hz1 / hz0, 3)
        observed.append({
            "stream": key[0], "bus": key[1], "idHex": f"0x{key[2]:X}",
            "normalFrames": normal_n, "parkingFrames": park_n,
            "normalHz": hz0, "parkingHz": hz1,
            "rateRatio": rate_ratio,
            "normalChangeFraction": c0, "parkingChangeFraction": c1,
            "changeFractionDelta": round(c1 - c0, 3) if c0 is not None and c1 is not None else None,
        })
    # Sorting prioritizes IDs appearing only in park, then large changes.
    observed.sort(key=lambda row: (
        0 if row["normalFrames"] == 0 else 1,
        -abs(row["changeFractionDelta"] or 0),
        -abs((row["rateRatio"] or 1) - 1),
        row["stream"], row["bus"], row["idHex"]))
    return {
        "normal": sa, "factoryParking": sb,
        "changes": observed,
        "caveats": [
            "Comparison of CAN IDs and anonymized byte-change rates, NOT a decoder for EPS steering commands.",
            "A new CAN ID or payload-change correlation does not establish its meaning, source ECU or authorization.",
            "Different traffic volume/vehicle speed/ignition state can cause apparent differences; use matched baselines.",
            "No vehicle control code or CAN injection. All raw frames stay private.",
        ],
    }


def iter_records(paths, schema_path):
    import capnp
    import zstandard
    schema_path = Path(schema_path)
    schema = capnp.load(str(schema_path), imports=[str(schema_path.parent)])
    for p in paths:
        path = Path(p)
        compressed = path.read_bytes()
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as stream:
            data = stream.read()
        for evt in schema.Event.read_multiple_bytes(data):
            if not evt.valid or evt.which() not in ("can", "sendcan"):
                continue
            service = evt.which()
            for frame in getattr(evt, service):
                yield int(evt.logMonoTime), service, int(frame.src), int(frame.address), bytes(frame.dat)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", required=True, type=Path,
                    help="Verified pinned device-build cereal/log.capnp with matching car.capnp")
    ap.add_argument("--normal", nargs="+", required=True, type=Path,
                    help="Private R-logs for ordinary low-speed steering, no factory parking")
    ap.add_argument("--parking", nargs="+", required=True, type=Path,
                    help="Private R-logs for an OEM automatic-parking maneuver")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    if not args.schema.is_file():
        ap.error("missing pinned log.capnp")
    if not all(p.is_file() for p in [*args.normal, *args.parking]):
        ap.error("missing private R-log input")
    normal = list(iter_records(args.normal, args.schema))
    parking = list(iter_records(args.parking, args.schema))
    result = compare(normal, parking)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Outputs contain CAN IDs and rates but no payload, route, identifier, timestamps or location.
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.out), "rankedCANIDs": len(result["changes"])}))


if __name__ == "__main__":
    main()
