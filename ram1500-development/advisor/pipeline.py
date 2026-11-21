"""The one per-drive object behind the live agent and the replay tool.

The agent builds batches from sockets, the replay tool builds them from log
files; everything after the batch happens here, once: carState-last ordering,
joining the streams, the held model message, the shared flags, the measures,
the 60 s windows and the event records. A number that differs between a live
drive and its replay can then only come from the inputs.

    pipeline = Pipeline(PipelineConfig())
    messages = pipeline.ingest_tick(batch)      # batch: {stream: (logMonoTime_ns, message)}
    messages = pipeline.end()                   # drive end: flush what waits, send the last window
    fields = pipeline.drive_end_fields()        # for the drive_end message

A batch holds the newest message of each stream that arrived since the
previous batch. Messages come back as (type, data); the caller adds the
envelope. Nothing crosses drives: build a new Pipeline for every drive.
"""
from __future__ import annotations

from dataclasses import dataclass

import measure_set
from gates import GateTracker
from measure_api import Context, Measure
from samples import JOINED, StreamJoiner
from shared_rules import DT_CAP_S, FRESH_LEAD_QLOG_S, FRESH_MODEL_QLOG_S, FRESH_S, WINDOW_S

STREAMS = ("carState", "carControl", "controlsState", "carOutput", "radarState", "drivingModelData",
           "onroadEvents", "liveDelay")
_OTHER_STREAMS = frozenset(STREAMS) - {"carState"}
_STALE_KEYS = tuple(short for _, short in JOINED)
_STALE_STEER_KEYS = ("ctl", "co", "dmd", "ev")       # the streams the steering lines of the report depend on
_WINDOW_NS = int(round(WINDOW_S * 1e9))
_KEEP_BEFORE_FIRST_NS = 1_000_000_000                # messages up to 1.0 s older than the first carState are kept
_HOOKS = ("on_stream", "on_sample", "on_model", "on_evaluate", "on_gap", "on_drive_end")


@dataclass(frozen=True)
class PipelineConfig:
    fresh_model_s: float = FRESH_S          # freshness limit of the model stream
    fresh_lead_s: float = FRESH_S           # ... of the lead stream
    t0_mono_s: float | None = None          # window grid start; None = the first Sample. Earlier messages are dropped
    measures: tuple | None = None           # measure factories; None = the measures of this build (measure_set)
    health_source: object = None            # callable giving the agent's own health values (a dict) for a closing
                                            # window; when it raises or gives no dict, the values are left out

    @classmethod
    def for_decimated_log(cls, **kwargs) -> "PipelineConfig":
        """Limits for a decimated log, where the model and lead streams are slow."""
        return cls(fresh_model_s=FRESH_MODEL_QLOG_S, fresh_lead_s=FRESH_LEAD_QLOG_S, **kwargs)


class _Window:
    __slots__ = ("w", "n", "window_s", "unobserved_s", "tick_max_s", "gap_n", "model_gap_n", "stale", "stale_steer")

    def __init__(self, w: int):
        self.w = w
        self.n = self.gap_n = self.model_gap_n = 0
        self.window_s = self.unobserved_s = self.tick_max_s = 0.0
        self.stale = dict.fromkeys(_STALE_KEYS, 0.0)
        self.stale_steer = dict.fromkeys(_STALE_STEER_KEYS, 0.0)


class Pipeline:
    def __init__(self, config: PipelineConfig | None = None):
        self._config = config = config or PipelineConfig()
        self._joiner = StreamJoiner(fresh_model_s=config.fresh_model_s, fresh_lead_s=config.fresh_lead_s)
        self._gates = GateTracker()
        self._ctx = Context()
        self._t0_ns = None if config.t0_mono_s is None else int(round(config.t0_mono_s * 1e9))
        self._floor_ns = self._t0_ns            # nothing older than this is taken
        self._win = None
        self._ended = False
        self._total_window_s = 0.0
        self._total_unobserved_s = 0.0
        self.windows_sent = 0
        self._failed = []                       # names of the measures switched off, in the order they failed
        self._off = set()
        self._measures = self._build_measures()
        self._bound = {hook: [(m, getattr(m, hook)) for m in self._measures if _has_own(m, hook)]
                       for hook in _HOOKS}
        self._raw_wanted = {}
        for m, call in self._bound["on_stream"]:
            for stream in getattr(m, "streams", ()):
                self._raw_wanted.setdefault(stream, []).append((m, call))
        self._raw_done_ns = {}

    def _build_measures(self) -> list:
        factories = self._config.measures
        if factories is None:
            factories = measure_set.MEASURES
        built = []
        for factory in factories:
            try:
                built.append(factory())
            except Exception:
                self._failed.append(str(getattr(factory, "name", "") or "unknown"))
        return sorted(built, key=lambda m: not m.owner_facing)      # owner-facing first, order otherwise kept

    # --- what the caller reads ----------------------------------------------
    @property
    def measure_names(self) -> list:
        """Names of the measures of this drive, in run order."""
        return [m.name for m in self._measures]

    @property
    def failed(self) -> list:
        """Names of the measures switched off after an exception."""
        return list(self._failed)

    def drive_end_fields(self) -> dict:
        """What the pipeline knows for the drive_end message: windows, failed, totals, and the measures' own fields."""
        totals = {"windowS": round(self._total_window_s, 1), "unobservedS": round(self._total_unobserved_s, 1)}
        data = {"windows": self.windows_sent, "totals": totals}
        for m in self._measures:
            self._merge(m, "drive_totals", totals)
            self._merge(m, "drive_end_fields", data)
        data["failed"] = list(self._failed)
        return data

    # --- batches in ---------------------------------------------------------
    def ingest_tick(self, batch: dict) -> list:
        """Take one batch; returns the messages to send, as (type, data)."""
        if self._ended:
            return []
        out = self._ctx.out = []
        car = batch.get("carState")
        if self._floor_ns is None and car is not None:
            # First carState of the drive: forget what was left over from before it.
            self._floor_ns = car[0] - _KEEP_BEFORE_FIRST_NS
            self._joiner.forget_before(self._floor_ns)
        floor = self._floor_ns
        for stream, (t_ns, msg) in batch.items():
            if stream in _OTHER_STREAMS and (floor is None or t_ns >= floor):
                self._joiner.offer(stream, t_ns, msg)
        sample = None
        if car is not None and car[0] >= floor:
            sample = self._joiner.sample(car[0], car[1])       # carState goes last
        if sample is None:
            self._joiner.models_after(None)
        else:
            self._on_sample(sample, car[0])
        return out

    def end(self) -> list:
        """Drive end: evaluate what waits, let every measure close its episodes, send the last window."""
        if self._ended:
            return []
        self._ended = True
        out = self._ctx.out = []
        if self._win is None:
            return out                          # the drive gave no Sample
        ctx = self._ctx
        evaluate = self._bound["on_evaluate"]
        for released in self._gates.end():
            for m, call in evaluate:
                self._run(m, call, ctx, released)
        for m, call in self._bound["on_drive_end"]:
            self._run(m, call, ctx)
        self._close_window()
        self._win = None
        return out

    # --- one Sample ---------------------------------------------------------
    def _on_sample(self, sample, t_ns: int) -> None:
        if self._t0_ns is None:
            self._t0_ns = t_ns
        w = (t_ns - self._t0_ns) // _WINDOW_NS
        win = self._win
        if win is None or w > win.w:
            if win is not None:
                self._close_window()            # 1. close every window that ended; open the next
            win = self._win = _Window(w)
        ctx = self._ctx
        ctx.w, ctx.t = w, sample.t

        g, released = self._gates.add(sample)
        dt = sample.dt
        win.n += 1
        win.window_s += dt
        self._total_window_s += dt
        if sample.raw_dt > win.tick_max_s:
            win.tick_max_s = sample.raw_dt
        if sample.gap:
            win.gap_n += 1
            win.unobserved_s += sample.raw_dt - DT_CAP_S
            self._total_unobserved_s += sample.raw_dt - DT_CAP_S
        for key in sample.missing:
            win.stale[key] += dt
            if g.lat_on and key in win.stale_steer:
                win.stale_steer[key] += dt

        evaluate = self._bound["on_evaluate"]
        if sample.gap:
            # Everything that waited is evaluated with the data that exists, the
            # measures reset their run state, and the gap Sample starts the new run.
            for r in released:
                for m, call in evaluate:
                    self._run(m, call, ctx, r)
            for m, call in self._bound["on_gap"]:
                self._run(m, call, ctx)
            released = ()

        for stream, wanted in self._raw_wanted.items():     # 2. what books to the Sample's own window
            held = self._joiner.latest(stream)
            if held is not None and held[0] > self._raw_done_ns.get(stream, -1):
                self._raw_done_ns[stream] = held[0]
                for m, call in wanted:
                    self._run(m, call, ctx, stream, held[0] / 1e9, held[1])
        for m, call in self._bound["on_sample"]:
            self._run(m, call, ctx, g)
        for model_msg in self._joiner.models_after(t_ns):
            if model_msg.gap:
                win.model_gap_n += 1
            for m, call in self._bound["on_model"]:
                self._run(m, call, ctx, model_msg, g)

        for r in released:                                  # 3. the evaluations this Sample triggers
            for m, call in evaluate:
                self._run(m, call, ctx, r)

    # --- windows ------------------------------------------------------------
    def _close_window(self) -> None:
        win = self._win
        # The agent's values are read first: once a measure has handed over its
        # sums they are gone, so nothing that can go wrong may come after that.
        health = self._agent_health()
        fields = {}
        for m in self._measures:
            self._merge(m, "window_fields", fields)
        data = {"w": win.w, "tMonoS": round((self._t0_ns + win.w * _WINDOW_NS) / 1e9, 1), "n": win.n,
                "windowS": round(win.window_s, 1), "unobservedS": round(win.unobserved_s, 1),
                "tickMaxS": round(win.tick_max_s, 2)}
        for key, value in fields.items():
            data.setdefault(key, value)         # a measure cannot replace the pipeline's own fields
        health.pop("failed", None)
        health.update({"gapN": win.gap_n, "modelGapN": win.model_gap_n,
                       "staleS": _non_zero(win.stale), "staleSteerS": _non_zero(win.stale_steer)})
        if self._failed:
            health["failed"] = list(self._failed)
        data["health"] = health
        self._ctx.out.append(("summary", data))
        self.windows_sent += 1

    def _agent_health(self) -> dict:
        """The agent's own health values for the closing window; empty when it has none or the read fails."""
        source = self._config.health_source
        if source is None:
            return {}
        try:
            values = source()
        except Exception:
            return {}
        return dict(values) if isinstance(values, dict) else {}

    # --- one measure must not take the others down ---------------------------
    def _run(self, measure, call, *args) -> None:
        if id(measure) in self._off:
            return
        try:
            call(*args)
        except Exception:
            self._switch_off(measure)

    def _merge(self, measure, hook: str, into: dict) -> None:
        if id(measure) in self._off:
            return
        try:
            fields = getattr(measure, hook)()
            if fields:
                into.update(fields)
        except Exception:
            self._switch_off(measure)

    def _switch_off(self, measure) -> None:
        self._off.add(id(measure))
        self._failed.append(str(measure.name))


def _has_own(measure, hook: str) -> bool:
    """True when the measure defines this hook itself (the base class hooks do nothing)."""
    return getattr(type(measure), hook) is not getattr(Measure, hook)


def _non_zero(seconds: dict) -> dict:
    return {key: round(value, 1) for key, value in seconds.items() if round(value, 1) > 0}
