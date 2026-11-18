from conftest import drive, events
from ramtools.episodes import build_episodes, event_messages
from ramtools.faults import classify, classify_fault_episodes
from ramtools.timeline import Index


def _run(recs):
  idx = Index(recs)
  eps = build_episodes(event_messages(recs))
  return classify_fault_episodes(idx, eps, idx.first_t(), idx.last_t())


def test_end_of_route_can_and_mismatch_are_shutdown_transients():
  recs = drive(300, end_shutdown=True)
  ev = [(float(t), []) for t in range(298)] + [(298.5, ["canError", "controlsMismatch"]), (299.0, ["canError", "controlsMismatch"])]
  out = _run(events(recs, ev))
  assert {o["event"] for o in out} == {"canError", "controlsMismatch"}
  for o in out:
    assert o["classification"] == "stationary_shutdown_or_parked", o
    assert o["severity"] == "low"
    assert o["vEgo_max_prior_10s_ms"] > 0  # it WAS driving shortly before: context, not an exoneration
  mm = next(o for o in out if o["event"] == "controlsMismatch")
  assert any("safetyRxChecksInvalid" in h for h in mm["hints"])
  ce = next(o for o in out if o["event"] == "canError")
  assert any("canValid false" in h for h in ce["hints"])


def test_in_motion_engaged_fault_is_high_severity():
  recs = drive(120)
  out = _run(events(recs, [(float(t), []) for t in range(120)] + [(60.5, ["canError"]), (60.8, [])]))
  assert out[0]["classification"] == "in_motion_engaged" and out[0]["severity"] == "high"


def test_in_motion_not_engaged():
  recs = drive(120, lat_active=False)
  out = _run(events(recs, [(float(t), []) for t in range(120)] + [(60.5, ["controlsMismatch"]), (60.8, [])]))
  assert out[0]["classification"] == "in_motion_not_engaged"


def test_missing_speed_is_never_called_benign():
  assert classify({"vEgo_max_ms": None}) == ("unclassified_missing_speed", "unknown")


def test_startup_vs_other_stationary():
  base = {"vEgo_max_ms": 0.0, "latActive_any": False, "gears": ["drive"], "ignition_any": True}
  assert classify({**base, "route_s": 5.0, "s_before_route_end": 3000.0})[0] == "stationary_startup"
  assert classify({**base, "route_s": 900.0, "s_before_route_end": 2000.0})[0] == "stationary_other"
  assert classify({**base, "route_s": 900.0, "s_before_route_end": 2000.0, "gears": ["park"]})[0] == "stationary_shutdown_or_parked"
