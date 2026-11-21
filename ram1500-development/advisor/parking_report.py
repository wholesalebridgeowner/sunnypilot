#!/usr/bin/env python3
"""EXPERIMENTAL offline parking report: raw counts of the two candidate parking messages in full logs.

Not installed on the Comma. Reads rlog files of ONE drive, in segment order, with the installed build's
schema, and prints one JSON record (see parking_raw.py for what the numbers do and do not mean). Prints
numbers and fixed tokens only: no file name, route name, place or clock time.

  python3 parking_report.py --schema-dir SCHEMA_DIR seg0/rlog.zst [seg1/rlog.zst ...]
"""
from __future__ import annotations

import argparse
import io
import json
import os

from parking_raw import ADDR_E9, ADDR_F6, ParkingLog

_WANTED = (ADDR_F6, ADDR_E9)


def drive_record(paths, schema_dir: str) -> dict:
    import capnp
    import zstandard
    log = capnp.load(os.path.join(schema_dir, "log.capnp"), imports=[schema_dir])
    out = ParkingLog()
    unreadable = 0
    invalid_can = 0
    for path in paths:
        with open(path, "rb") as fh:
            raw = fh.read()
        data = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read() if path.endswith(".zst") else raw
        events = []
        try:
            for e in log.Event.read_multiple_bytes(data, traversal_limit_in_words=2**62):
                k = e.which()
                if k == "carState":
                    events.append((e.logMonoTime, 0, (str(e.carState.gearShifter), float(e.carState.vEgo),
                                                      bool(e.valid))))
                elif k == "can":
                    if not e.valid:
                        invalid_can += 1
                        events.append((e.logMonoTime, 2, None))
                        continue
                    frames = [(int(f.src), int(f.address), bytes(f.dat)) for f in e.can if f.address in _WANTED]
                    if frames:
                        events.append((e.logMonoTime, 1, frames))
        except Exception:
            unreadable += 1          # a truncated file: keep what was read before the break
        events.sort(key=lambda x: (x[0], x[1]))
        for t_ns, kind, item in events:
            if kind == 0:
                gear, v, valid = item
                out.car_state(t_ns / 1e9, gear, v, valid)
            elif kind == 1:
                for bus, address, payload in item:
                    out.frame(t_ns / 1e9, bus, address, payload)
            else:
                out.break_counter_continuity()
    rec = out.record()
    rec["files"] = len(paths)
    rec["filesWithReadError"] = unreadable
    rec["invalidCanEvents"] = invalid_can
    return rec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", required=True)
    parser.add_argument("logs", nargs="+")
    args = parser.parse_args()
    print(json.dumps(drive_record(args.logs, args.schema_dir), sort_keys=True))


if __name__ == "__main__":
    main()
