"""Reconstruct event *episodes* from onroadEvents messages.

In the drive build, selfdrived publishes onroadEvents every 1.00 s AND whenever the event list changes
(selfdrive/selfdrived/selfdrived.py:538-544 at 1f7233cb), and qlogs keep every onroadEvents message
(decimation 1). Therefore:

* an episode of event N starts at the first message that contains N after one that did not;
* it ends at the next message that does not contain N -- that message exists because the change itself
  forces a publish, so the end time is exact to the 10 ms control frame;
* "sample counts" mix the 1 Hz repeats with change messages, so they are neither incident counts nor seconds.

If consecutive onroadEvents messages are more than `gap_warn_s` apart (expected <= 1.0 s), data are missing
(e.g. an absent segment) and any episode spanning that gap is flagged.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

NS = 1e9


@dataclass
class Episode:
  name: str
  start_ns: int
  last_seen_ns: int
  end_ns: int | None          # first later message without the event; None if the log ends first
  samples: int
  start_censored: bool        # event already present in the first message we have
  end_censored: bool          # event still present in the last message we have
  spans_gap: bool             # a message gap > gap_warn_s occurred inside the episode

  @property
  def duration_s(self) -> float | None:
    return None if self.end_ns is None else (self.end_ns - self.start_ns) / NS

  def as_dict(self, t0_ns: int | None = None) -> dict:
    d = asdict(self)
    d["duration_s"] = self.duration_s
    if t0_ns is not None:
      d["start_s"] = (self.start_ns - t0_ns) / NS
      d["end_s"] = None if self.end_ns is None else (self.end_ns - t0_ns) / NS
    return d


def event_messages(records: list[dict], service: str = "onroadEvents") -> list[tuple[int, frozenset]]:
  return [(r["t"], frozenset(r.get("names", ()))) for r in records if r["service"] == service]


def build_episodes(msgs: list[tuple[int, frozenset]], gap_warn_s: float = 1.5) -> dict[str, list[Episode]]:
  msgs = sorted(msgs, key=lambda m: m[0])
  out: dict[str, list[Episode]] = defaultdict(list)
  open_eps: dict[str, Episode] = {}
  prev_t = None
  for idx, (t, names) in enumerate(msgs):
    gap = prev_t is not None and (t - prev_t) / NS > gap_warn_s
    if gap:
      for ep in open_eps.values():
        ep.spans_gap = True
    for name in list(open_eps):
      if name not in names:
        ep = open_eps.pop(name)
        ep.end_ns = t
        out[name].append(ep)
    for name in names:
      if name in open_eps:
        open_eps[name].samples += 1
        open_eps[name].last_seen_ns = t
      else:
        open_eps[name] = Episode(name=name, start_ns=t, last_seen_ns=t, end_ns=None, samples=1,
                                 start_censored=(idx == 0), end_censored=False, spans_gap=False)
    prev_t = t
  for name, ep in open_eps.items():
    ep.end_censored = True
    out[name].append(ep)
  for eps in out.values():
    eps.sort(key=lambda e: e.start_ns)
  return dict(out)


def message_gaps(msgs: list[tuple[int, frozenset]], gap_warn_s: float = 1.5) -> list[dict]:
  ts = sorted(t for t, _ in msgs)
  return [{"after_ns": a, "before_ns": b, "gap_s": (b - a) / NS} for a, b in zip(ts, ts[1:], strict=False)
          if (b - a) / NS > gap_warn_s]


def summarize(episodes: dict[str, list[Episode]]) -> dict[str, dict]:
  summary = {}
  for name, eps in sorted(episodes.items()):
    durs = [e.duration_s for e in eps if e.duration_s is not None]
    summary[name] = {
      "samples": sum(e.samples for e in eps),
      "episodes": len(eps),
      "total_duration_s": round(sum(durs), 3),
      "max_duration_s": round(max(durs), 3) if durs else None,
      "median_duration_s": round(sorted(durs)[len(durs) // 2], 3) if durs else None,
      "censored": sum(e.start_censored or e.end_censored for e in eps),
      "spanning_gaps": sum(e.spans_gap for e in eps),
    }
  return summary
