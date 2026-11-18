from conftest import NS
from ramtools.episodes import build_episodes, message_gaps, summarize


def msgs(*items):
  return [(int(t * NS), frozenset(n)) for t, n in items]


def test_single_sample_is_one_short_episode_with_exact_duration():
  # 1 Hz ticks at 170/171/172 and on-change messages at 170.55 (appears) and 171.20 (disappears)
  m = msgs((170.0, []), (170.55, ["steerSaturated"]), (171.0, ["steerSaturated"]), (171.2, []), (172.0, []))
  ep = build_episodes(m)["steerSaturated"]
  assert len(ep) == 1
  assert ep[0].samples == 2  # change message + one 1 Hz repeat
  assert abs(ep[0].duration_s - 0.65) < 1e-9
  assert not ep[0].start_censored and not ep[0].end_censored


def test_samples_are_not_incidents():
  # one 5-second override produces ~6 samples but is one episode
  items = [(t, ["steerOverride"]) for t in (10.0, 11.0, 12.0, 13.0, 14.0)] + [(14.3, []), (9.0, [])]
  ep = build_episodes(msgs(*items))["steerOverride"]
  assert len(ep) == 1 and ep[0].samples == 5 and abs(ep[0].duration_s - 4.3) < 1e-9
  s = summarize({"steerOverride": ep})["steerOverride"]
  assert s == {"samples": 5, "episodes": 1, "total_duration_s": 4.3, "max_duration_s": 4.3,
               "median_duration_s": 4.3, "censored": 0, "spanning_gaps": 0}


def test_censoring_at_log_edges():
  m = msgs((0.0, ["controlsMismatch"]), (1.0, ["controlsMismatch"]), (1.5, []), (2999.0, []), (3001.9, ["canError"]))
  eps = build_episodes(m)
  assert eps["controlsMismatch"][0].start_censored
  assert eps["canError"][0].end_censored and eps["canError"][0].duration_s is None


def test_gap_flags_episode_and_is_reported():
  m = msgs((0.0, ["steerOverride"]), (1.0, ["steerOverride"]), (65.0, ["steerOverride"]), (65.4, []))
  ep = build_episodes(m)["steerOverride"][0]
  assert ep.spans_gap
  gaps = message_gaps(m)
  assert len(gaps) == 1 and abs(gaps[0]["gap_s"] - 64.0) < 1e-9


def test_reappearing_event_gives_two_episodes_and_order_is_irrelevant():
  m = msgs((3.0, []), (1.0, ["canError"]), (2.0, []), (0.0, []), (4.0, ["canError"]), (4.5, []))
  eps = build_episodes(m)["canError"]
  assert [round(e.duration_s, 3) for e in eps] == [1.0, 0.5]
