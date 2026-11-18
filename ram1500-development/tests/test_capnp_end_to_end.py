"""End-to-end: real Cap'n Proto messages built with the PINNED drive-build schema -> zstd qlogs -> analysis.

Skipped unless RAM1500_SCHEMA_DIR points at a verified schema dir and pycapnp + zstandard are installed.
"""
import json

from conftest import drive

DEV_ROUTE = "000003cc--27a46b2116"


def _set(obj, dotted, value):
  parts = dotted.split(".")
  for p in parts[:-1]:
    obj = getattr(obj, p)
  setattr(obj, parts[-1], value)


def to_event(log, r):
  e = log.Event.new_message()
  e.logMonoTime = r["t"]
  e.valid = True
  svc = r["service"]
  fields = {k: v for k, v in r.items() if k not in ("t", "service", "valid", "segment")}
  if svc == "onroadEvents":
    lst = e.init("onroadEvents", len(fields["names"]))
    for i, n in enumerate(fields["names"]):
      lst[i].name = n
    return e
  if svc == "pandaStates":
    ps = e.init("pandaStates", 1)
    for k, v in fields.items():
      _set(ps[0], k.removeprefix("panda0."), v)
    return e
  msg = e.init(svc)
  if svc == "controlsState":
    ts = msg.lateralControlState.init("torqueState")
    for k, v in fields.items():
      if k.startswith("torqueState."):
        _set(ts, k.removeprefix("torqueState."), v)
      elif k != "lateralControlState":
        _set(msg, k, v)
    return e
  for k, v in fields.items():
    _set(msg, k, v)
  return e


def header_events(log, t):
  init = log.Event.new_message(logMonoTime=t, valid=True)
  ini = init.init("initData")
  ini.wallTimeNanos = 1_763_166_593_000_000_000
  ini.version = "0.10.1"
  ini.gitCommit = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
  ini.dirty = True
  ents = ini.params.init("entries", 3)
  for i, (k, v) in enumerate([("NeuralNetworkLateralControl", b"0"), ("DongleId", b"PRIVATEDONGLE"), ("Mads", b"1")]):
    ents[i].key, ents[i].value = k, v
  cpe = log.Event.new_message(logMonoTime=t + 1, valid=True)
  cp = cpe.init("carParams")
  cp.carFingerprint = "RAM_1500_5TH_GEN"
  cp.brand = "chrysler"
  cp.carVin = "1C6SRFFT0PRIVATEVIN"
  cp.minEnableSpeed, cp.minSteerSpeed, cp.steerLimitTimer = 14.5, 0.5, 0.4
  tq = cp.lateralTuning.init("torque")
  tq.latAccelFactor, tq.friction, tq.kp, tq.ki, tq.kf = 2.0, 0.05, 1.0, 0.3, 1.0
  fw = cp.init("carFw", 1)
  fw[0].ecu, fw[0].fwVersion, fw[0].address = "eps", b"68312176AE", 0x75A
  gps = log.Event.new_message(logMonoTime=t + 2, valid=True)
  g = gps.init("gpsLocationExternal")
  g.latitude, g.longitude, g.unixTimestampMillis, g.hasFix = 41.123456, -96.654321, 1_763_166_594_000, True
  err = log.Event.new_message(logMonoTime=t + 3, valid=True)
  err.errorLogMessage = json.dumps({"msg": {"event": "selfdrived.initialized", "timeout": False, "canValid": True,
                                            "invalid": [], "not_alive": [], "not_freq_ok": []},
                                    "ctx": {"dongle_id": "PRIVATEDONGLE"}, "level": "ERROR"})
  return [init, cpe, gps, err]


def write_route(tmp_path, log, zstandard, records, seg_len_s=60):
  t_first = records[0]["t"]
  by_seg = {}
  for r in records:
    by_seg.setdefault(int((r["t"] - t_first) / 1e9 // seg_len_s), []).append(r)
  paths = []
  for seg, recs in sorted(by_seg.items()):
    evts = header_events(log, recs[0]["t"] - 10) if seg == 0 else []
    evts += [to_event(log, r) for r in recs]
    data = b"".join(e.to_bytes() for e in evts)
    d = tmp_path / "logs" / f"{DEV_ROUTE}--{seg}"
    d.mkdir(parents=True)
    p = d / "qlog.zst"
    p.write_bytes(zstandard.ZstdCompressor().compress(data))
    paths.append(p)
  return paths


def test_pinned_decode_and_investigation(tmp_path, schema_dir):
  import zstandard
  from conftest import events
  from investigate_route import investigate
  from ramtools.logio import load_records
  from ramtools.schema import load_log_schema

  log = load_log_schema(schema_dir)
  recs = drive(180, sat_windows=[(170.4, 171.3)], eps_sat=320.0, end_shutdown=True)
  ev = [(float(t), []) for t in range(178) if t != 171] + [(170.9, ["steerSaturated"]), (171.0, ["steerSaturated"]),
                                                           (171.3, []), (178.5, ["canError", "controlsMismatch"])]
  paths = write_route(tmp_path, log, zstandard, events(recs, ev))
  # truncate the last segment mid-stream: must be reported, not crash
  last = paths[-1]
  raw = zstandard.ZstdDecompressor().decompress(last.read_bytes(), max_output_size=1 << 26)
  last.write_bytes(zstandard.ZstdCompressor().compress(raw[:-37]))

  records, results = load_records(paths, log)
  assert [r.segment for r in results] == [0, 1, 2]
  assert results[-1].truncated and not results[0].truncated

  out = tmp_path / "out"
  out.mkdir()
  res = investigate(records, results, out=out)
  sat = res["steerSaturated"]
  assert len(sat) == 1 and sat[0]["mechanism"] == "A_torque_authority"
  assert abs(sat[0]["duration_s"] - 0.4) < 0.02
  assert res["controller_mode"]["mode"] == "stock_torque"
  faults = {f["event"]: f for f in res["faults"]}
  assert faults["canError"]["classification"] == "stationary_shutdown_or_parked"
  assert res["provenance"]["matches_pinned_drive_build"] is True
  assert res["provenance"]["control_params"] == {"NeuralNetworkLateralControl": "0", "Mads": "1"}
  assert res["car"]["checks"]["latAccelFactor_is_placeholder_2.0"] is True
  assert res["car"]["checks"]["brand_in_torqued_allowlist"] is False
  assert res["time_base"]["anchors"]["gps"]["unix_ms"] == 1_763_166_594_000
  assert res["error_logs"]["selfdrived.initialized"]["note"].startswith("always logged")

  blob = "".join(p.read_text() for p in out.iterdir())
  for secret in ("PRIVATEVIN", "PRIVATEDONGLE", "41.123456", "-96.654321"):
    assert secret not in blob, secret
  assert (out / "steerSaturated_0_pm15s.csv").exists() and (out / "steerSaturated_0_pm30s.csv").exists()
  assert (out / "findings.md").read_text().count("A_torque_authority") == 1
