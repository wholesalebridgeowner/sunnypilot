#!/usr/bin/env python3
"""Offline forensic investigation of one comma route, decoded with the pinned drive-build schema.

Read-only: reads local log files, writes analysis files to --out. No vehicle, panda, Athena or comma API
access. Outputs contain no VIN, dongle id, GPS position or raw CAN payloads, but they are still derived from
private driving data, so --out must be OUTSIDE this git repository (enforced unless --allow-repo-output).

Example (PowerShell or bash, from the repository root):
  python ram1500-development/investigate_route.py --fetch-schema \
      --schema-dir ~/.cache/ram1500/schema-1f7233cb --out ~/ram-analysis/000003cc--27a46b2116 \
      ~/comma-logs/000003cc--27a46b2116--*/qlog.zst

Requires: pycapnp, zstandard (pip install pycapnp zstandard). The analysis itself is pure Python.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ramtools import constants as C
from ramtools import episodes as E
from ramtools import faults as F
from ramtools import saturation as S
from ramtools import summary as SM
from ramtools.logio import load_records, segment_of
from ramtools.schema import fetch_pinned_schema, load_log_schema, verify_schema_dir
from ramtools.timeline import Index, window_rows, write_csv

NS = 1e9
REPO_ROOT = HERE.parent


def expand(paths: list[str]) -> list[Path]:
  out = []
  for p in paths:
    hits = glob.glob(str(Path(p).expanduser()))
    out.extend(Path(h) for h in (hits or [p]))
  return sorted(set(out), key=lambda p: (segment_of(p)[1] if segment_of(p)[1] is not None else 10**9, str(p)))


def inside_repo(path: Path) -> bool:
  try:
    path.resolve().relative_to(REPO_ROOT)
    return True
  except ValueError:
    return False


def investigate(records: list[dict], read_results: list, windows=(15.0, 30.0), out: Path | None = None) -> dict:
  index = Index(records)
  t0, t_end = index.first_t(), index.last_t()
  msgs = E.event_messages(records)
  eps = E.build_episodes(msgs)
  anchors = SM.time_anchors(index)

  result = {
    "drive_build_assumed": C.DRIVE_BUILD_COMMIT,
    "provenance": SM.provenance(index),
    "files": [vars(r) for r in read_results],
    "segments_present": sorted({r.segment for r in read_results if r.segment is not None}),
    "time_base": {"t0_logMonoTime_ns": t0, "definition": "route_s = (logMonoTime - first message in the supplied files) / 1e9",
                  "duration_s": None if t0 is None else round((t_end - t0) / NS, 3), "anchors": anchors},
    "onroadEvents_gaps": E.message_gaps(msgs),
    "event_summary": E.summarize(eps),
    "car": SM.carparams_summary(index),
  }
  segs = result["segments_present"]
  if segs:
    result["segments_missing"] = sorted(set(range(segs[0], segs[-1] + 1)) - set(segs))

  ctrl_rows = [r for r in records if r["service"] == "controlsState"]
  mode_rows = [{f"controlsState.{k}": v for k, v in r.items()} for r in ctrl_rows]
  result["controller_mode"] = S.infer_controller_mode(mode_rows)
  result["saturation_route_stats"] = S.route_saturation_stats(index)

  sat_out = []
  for n, ep in enumerate(eps.get("steerSaturated", [])):
    entry = {"n": n, **ep.as_dict(t0), **S.analyze_saturation_episode(index, ep.start_ns, ep.end_ns)}
    entry["start_unix_ms"] = SM.mono_to_unix_ms(ep.start_ns, anchors)
    if out is not None:
      for w in windows:
        rows = window_rows(index, ep.start_ns, w, w, t0_ns=t0)
        p = out / f"steerSaturated_{n}_pm{int(w)}s.csv"
        write_csv(rows, p)
        entry.setdefault("timeline_files", []).append(p.name)
    sat_out.append(entry)
  result["steerSaturated"] = sat_out

  fault_rows = F.classify_fault_episodes(index, eps, t0, t_end) if t0 is not None else []
  for fr in fault_rows:
    fr["route_s"] = round((fr["start_ns"] - t0) / NS, 2)
  result["faults"] = fault_rows
  result["error_logs"] = SM.error_log_summary(index, t0) if t0 is not None else {}

  if out is not None:
    ep_rows = [e.as_dict(t0) for name in sorted(eps) for e in eps[name]]
    write_csv(ep_rows, out / "episodes.csv")
    write_csv(fault_rows, out / "faults.csv")
    (out / "summary.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    (out / "findings.md").write_text(render_findings(result))
  return result


def render_findings(r: dict) -> str:
  L = ["# Route findings (auto-generated, read-only)", ""]
  prov = r.get("provenance", {})
  L.append(f"- Build in log: {prov.get('version')} commit {prov.get('gitCommit')} dirty={prov.get('dirty')} " +
           f"(pinned drive build match: {prov.get('matches_pinned_drive_build')})")
  L.append(f"- Duration {r['time_base']['duration_s']} s; segments present {len(r['segments_present'])}; " +
           f"missing {r.get('segments_missing', [])}; onroadEvents gaps > 1.5 s: {len(r['onroadEvents_gaps'])}")
  cm = r.get("controller_mode", {})
  L.append(f"- Lateral controller mode inferred from PID terms: **{cm.get('mode')}** (median ratio {cm.get('median_ratio')}, n={cm.get('n')})")
  ltp = r.get("car", {}).get("liveTorqueParameters")
  if ltp:
    L.append(f"- liveTorqueParameters useParams counts: {ltp['useParams_counts']} (False = learned values not applied)")
  L += ["", "## Event episodes (samples are not incidents)", "", "| event | samples | episodes | total s | max s |", "|---|---|---|---|---|"]
  for name, s in r["event_summary"].items():
    L.append(f"| {name} | {s['samples']} | {s['episodes']} | {s['total_duration_s']} | {s['max_duration_s']} |")
  L += ["", "## steerSaturated episodes", ""]
  for e in r["steerSaturated"]:
    ev = e["evidence"]
    L.append(f"- #{e['n']} at route {e.get('start_s'):.2f} s, duration {e.get('duration_s')} s: mechanism **{e['mechanism']}** " +
             f"({e['confidence']}); |output| max {ev['abs_output_max']}, |EPS motor| max {ev['abs_eps_motor_torque_max_raw']} raw, " +
             f"|CAN torque sent| max {ev['abs_can_torque_sent_max_raw']} raw, speed {ev['speed_ms_min_max']} m/s, " +
             f"desired |lat accel| max {ev['desired_lat_accel_abs_max']} vs actual {ev['actual_lat_accel_abs_at_desired_peak']} m/s^2. " +
             f"{e.get('eps_note') or ''}")
  near = r["saturation_route_stats"]["near_saturation_runs"]
  L.append(f"- Near-saturation runs (|output| >= 0.95 for >= 0.3 s) over the route: {len(near)}")
  L += ["", "## Fault episodes", "", "| route s | event | dur s | class | severity | v max m/s | latActive | hints |", "|---|---|---|---|---|---|---|---|"]
  for f in r["faults"]:
    L.append(f"| {f['route_s']} | {f['event']} | {f['duration_s']} | {f['classification']} | {f['severity']} | " +
             f"{f['vEgo_max_ms']} | {f['latActive_any']} | {'; '.join(f['hints'])} |")
  L += ["", "## Error-log groups", ""]
  for k, g in list(r["error_logs"].items())[:25]:
    L.append(f"- `{k}` x{g['count']} first {g['first_s']} s last {g['last_s']} s persisted={g['persisted']} {g.get('note', '')}")
  L.append("")
  return "\n".join(L)


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("logs", nargs="+", help="qlog/rlog files (.zst/.bz2/raw); globs allowed")
  ap.add_argument("--schema-dir", type=Path, required=True, help="directory holding the pinned drive-build schema")
  ap.add_argument("--fetch-schema", action="store_true", help="download + verify the pinned schema into --schema-dir if needed")
  ap.add_argument("--out", type=Path, required=True, help="output directory (outside the repo)")
  ap.add_argument("--window", type=float, action="append", help="timeline half-window in seconds (default 15 and 30)")
  ap.add_argument("--allow-repo-output", action="store_true", help="permit --out inside the git repository")
  args = ap.parse_args(argv)

  out = args.out.expanduser()
  if inside_repo(out) and not args.allow_repo_output:
    ap.error(f"--out {out} is inside the repository; private-log derivatives must stay outside git")
  out.mkdir(parents=True, exist_ok=True)

  schema_dir = args.schema_dir.expanduser()
  if args.fetch_schema:
    fetch_pinned_schema(schema_dir)
  status = verify_schema_dir(schema_dir)
  if any(v != "ok" for v in status.values()):
    ap.error(f"schema dir is not the pinned drive-build schema: {status} (use --fetch-schema)")
  log_schema = load_log_schema(schema_dir)

  files = expand(args.logs)
  records, results = load_records(files, log_schema)
  if not records:
    print("no records decoded", file=sys.stderr)
    return 2
  res = investigate(records, results, windows=tuple(args.window or (15.0, 30.0)), out=out)
  bad = [f for f in res["files"] if f["error"]]
  print(f"decoded {len(records)} kept records from {len(files)} files ({len(bad)} with read errors); wrote {out}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
