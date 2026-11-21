"""Pipeline: the one per-drive object behind the live agent and the replay tool."""
import random
import unittest

import synth
from measure_api import Measure
from pipeline import STREAMS, Pipeline, PipelineConfig
from synth import MS, SEC

T0 = 1_000 * SEC


class Probe(Measure):
    """Sums what the first real measures will sum and records the order of the calls."""
    name = "probe"
    owner_facing = True
    KEYS = ("latOnS", "steeringS", "forcedZeroS", "fzFallbackS", "scoredS")

    def __init__(self):
        self.win = dict.fromkeys(self.KEYS, 0.0)
        self.total = dict.fromkeys(self.KEYS, 0.0)
        self.immediate = []        # (Sample time, window) at on_sample
        self.evaluated = []        # (Sample time, window, clean, straight_eligible) at on_evaluate
        self.models = []           # (model message time, time of the Sample it came with)
        self.log = []
        self.emit_at = {}          # Sample index -> event type to emit from on_sample

    def _add(self, key, dt):
        self.win[key] += dt
        self.total[key] += dt

    def on_sample(self, ctx, g):
        s = g.sample
        self.log.append("sample")
        kind = self.emit_at.get(len(self.immediate))
        self.immediate.append((s.t, ctx.w))
        if kind:
            ctx.emit(kind, {"w": ctx.w})
        if g.lat_on:
            self._add("latOnS", s.dt)
            if s.alerts is None:
                self._add("fzFallbackS", s.dt)
        if g.steering:
            self._add("steeringS", s.dt)
        if g.forced_zero:
            self._add("forcedZeroS", s.dt)

    def on_model(self, ctx, m, g):
        self.log.append("model")
        self.models.append((m.t_m, g.sample.t))

    def on_evaluate(self, ctx, g):
        self.log.append("evaluate")
        self.evaluated.append((g.sample.t, ctx.w, g.clean, g.straight_eligible))
        if g.straight_eligible:
            self._add("scoredS", g.sample.dt)

    def on_gap(self, ctx):
        self.log.append("gap")

    def on_drive_end(self, ctx):
        self.log.append("end")
        ctx.emit("probe_end", {"w": ctx.w})

    def window_fields(self):
        out = {key: round(value, 1) for key, value in self.win.items()}
        self.win = dict.fromkeys(self.KEYS, 0.0)
        return out

    def drive_totals(self):
        return {"steeringS": round(self.total["steeringS"], 1)}

    def drive_end_fields(self):
        return {"probeSamples": len(self.immediate)}


class LogOnly(Measure):
    """A log-only block that counts evaluated Samples, and can be told to fail."""
    name = "lane"

    def __init__(self, fail_in=None, fail_at=100):
        self.fail_in, self.fail_at = fail_in, fail_at
        self.calls = {"on_sample": 0, "on_evaluate": 0, "on_model": 0, "on_gap": 0, "on_drive_end": 0,
                      "window_fields": 0}
        self.n = 0

    def _hit(self, hook):
        self.calls[hook] += 1
        if self.fail_in == hook and self.calls[hook] >= self.fail_at:
            raise RuntimeError("broken measure")

    def on_sample(self, ctx, g):
        self._hit("on_sample")
        self.n += 1

    def on_evaluate(self, ctx, g):
        self._hit("on_evaluate")

    def on_model(self, ctx, m, g):
        self._hit("on_model")

    def on_gap(self, ctx):
        self._hit("on_gap")

    def on_drive_end(self, ctx):
        self._hit("on_drive_end")

    def window_fields(self):
        self._hit("window_fields")
        out, self.n = {"lane": {"n": self.n}}, 0
        return out


def make(*measures, **config):
    """A Pipeline over ready-made measure objects, so a test can look inside them afterwards."""
    return Pipeline(PipelineConfig(measures=tuple((lambda m=m: m) for m in measures), **config))


def stalled(first_n, stall_ms, then_n, period_ms=100):
    """Tick times with one stall: the tick after the first `first_n` comes `stall_ms` after the one before it."""
    head = synth.ticks(first_n, period_ms, T0)
    return head + synth.ticks(then_n, period_ms, head[-1] + stall_ms * MS)


class WindowGridTests(unittest.TestCase):
    def test_fully_engaged_minutes_at_a_slow_loop(self):
        probe = Probe()
        out = synth.summaries(synth.run(make(probe), synth.drive(synth.ticks(6000, 103, T0))))
        self.assertEqual([s["w"] for s in out], list(range(11)))
        for s in out[:10]:
            with self.subTest(w=s["w"]):
                self.assertIn(s["n"], (582, 583))
                self.assertAlmostEqual(s["windowS"], 60.0, delta=0.11)
                self.assertAlmostEqual(s["latOnS"], 60.0, delta=0.11)
                self.assertEqual(s["unobservedS"], 0.0)
                self.assertEqual(s["health"]["gapN"], 0)
                self.assertEqual(s["tMonoS"], 1000.0 + 60 * s["w"])
                self.assertAlmostEqual(s["tickMaxS"], 0.10, delta=0.011)
        self.assertLess(out[10]["windowS"], 30.0)                      # the last, partial window says how long it was
        self.assertAlmostEqual(sum(s["windowS"] for s in out), 6000 * 0.103, delta=0.6)

    def test_sample_exactly_on_the_boundary_opens_the_next_window(self):
        probe = Probe()
        p = make(probe)
        batches = synth.drive(synth.ticks(601, 100, T0))
        for b in batches[:600]:
            self.assertEqual(p.ingest_tick(b), [])
        out = p.ingest_tick(batches[600])                              # stamped T0 + 60.0 s exactly
        self.assertEqual([kind for kind, _ in out], ["summary"])
        self.assertEqual(out[0][1]["n"], 600)
        self.assertEqual(out[0][1]["windowS"], 60.0)
        self.assertEqual(probe.immediate[599][1], 0)
        self.assertEqual(probe.immediate[600][1], 1)

    def test_long_stall_skips_the_empty_window(self):
        times = stalled(551, 70_000, 1750)                             # stall from 55.0 s to 125.0 s
        p = make(Probe())
        out = synth.summaries(synth.run(p, synth.drive(times)))
        self.assertEqual([s["w"] for s in out], [0, 2, 3, 4])
        for s in out:
            self.assertEqual(s["tMonoS"], 1000.0 + 60 * s["w"])
        self.assertEqual(out[0]["n"], 551)
        self.assertEqual([s["health"]["gapN"] for s in out], [0, 1, 0, 0])
        self.assertEqual(out[1]["unobservedS"], 69.75)
        self.assertAlmostEqual(out[1]["tickMaxS"], 70.0, delta=0.11)
        self.assertEqual([s["unobservedS"] for s in (out[0], out[2], out[3])], [0.0, 0.0, 0.0])
        end = p.drive_end_fields()
        self.assertEqual(end["windows"], 4)
        self.assertAlmostEqual(end["totals"]["unobservedS"], 69.75, delta=0.051)
        self.assertAlmostEqual(end["totals"]["windowS"], sum(s["windowS"] for s in out), delta=0.21)

    def test_one_second_stall_is_one_gap_sample(self):
        out = synth.summaries(synth.run(make(), synth.drive(stalled(200, 1_000, 200))))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["health"]["gapN"], 1)
        self.assertEqual(out[0]["unobservedS"], 0.75)
        self.assertAlmostEqual(out[0]["tickMaxS"], 1.0, delta=0.11)
        self.assertAlmostEqual(out[0]["windowS"], 399 * 0.1 + 0.25, delta=0.051)

    def test_short_stall_on_a_straight_costs_seven_seconds_of_scoring(self):
        times = stalled(300, 300, 300)
        probe = Probe()
        out = synth.summaries(synth.run(make(probe), synth.drive(times)))
        self.assertEqual(sum(s["health"]["gapN"] for s in out), 1)
        self.assertEqual([s["unobservedS"] for s in out], [0.05, 0.0])     # two decimals: a short stall shows
        gap_t = times[300] / 1e9
        scored = [t for t, _, _, ok in probe.evaluated if ok]
        self.assertFalse([t for t in scored if gap_t - 2.0 <= t <= gap_t + 5.0])
        self.assertTrue([t for t in scored if t < gap_t - 3.5])
        self.assertTrue([t for t in scored if t > gap_t + 5.0])
        self.assertEqual(len(probe.evaluated), 600)                    # every Sample is evaluated exactly once

    def test_no_sample_no_summary(self):
        p = make(Probe())
        self.assertEqual(p.ingest_tick({"carControl": (T0, synth.car_control())}), [])
        self.assertEqual(p.ingest_tick({"carState": (T0, synth.car_state(v=float("nan")))}), [])
        self.assertEqual(p.end(), [])
        self.assertEqual(p.drive_end_fields()["windows"], 0)

    def test_final_partial_window_is_sent_at_the_end(self):
        p = make(Probe())
        batches = synth.drive(synth.ticks(250, 100, T0))
        for b in batches:
            self.assertEqual(p.ingest_tick(b), [])
        out = p.end()
        self.assertEqual([kind for kind, _ in out], ["probe_end", "summary"])
        self.assertEqual(out[1][1]["n"], 250)
        self.assertAlmostEqual(out[1][1]["windowS"], 25.0, delta=0.051)
        self.assertEqual(p.end(), [])                                  # ending twice sends nothing more
        self.assertEqual(p.ingest_tick(synth.batch(T0 + 30 * SEC)), [])  # and no Sample is taken afterwards

    def test_given_start_time_fixes_the_grid(self):
        probe = Probe()
        p = make(probe, t0_mono_s=1000.32)
        out = synth.summaries(synth.run(p, synth.drive(synth.ticks(1300, 100, T0))))
        self.assertAlmostEqual(probe.immediate[0][0], 1000.4, places=6)    # everything before the start is dropped
        self.assertEqual([s["w"] for s in out], [0, 1, 2])
        self.assertEqual([s["tMonoS"] for s in out], [1000.3, 1060.3, 1120.3])
        self.assertEqual(out[0]["n"], 600)                             # 1000.4 .. 1060.3


class JoinTests(unittest.TestCase):
    def test_control_message_five_ms_after_car_state_at_every_phase(self):
        events = []
        for k in range(6000):                                          # 60 s at 100 Hz
            t = T0 + k * 10 * MS
            events.append((t, "carState", synth.car_state()))
            events.append((t + 5 * MS, "carControl", synth.car_control(lat=1000 <= k < 5000)))
        rng = random.Random(7)
        tick_times, t = [], T0 + 20 * MS
        while t < T0 + 60 * SEC:
            tick_times.append(t)
            t += 100 * MS + int(rng.uniform(0.5, 3.0) * MS)            # the tick walks through the 10 ms cycle
        probe = Probe()
        synth.run(make(probe), synth.tick_batches(events, tick_times))
        self.assertGreater(len({(t // (1 * MS)) % 10 for t in tick_times}), 8)
        self.assertGreaterEqual(probe.total["steeringS"], 0.99 * 40.0)
        self.assertLessEqual(probe.total["steeringS"], 1.01 * 40.0)

    def test_silent_control_stream_is_missing_not_false(self):
        def change(i, t_ns):
            return {"skip": ("carControl",)} if 200 <= i < 230 else None
        probe = Probe()
        out = synth.summaries(synth.run(make(probe), synth.drive(synth.ticks(400, 100, T0), change)))
        self.assertAlmostEqual(out[0]["health"]["staleS"]["cc"], 2.5, delta=0.2)   # fresh for 0.5 s, then missing
        self.assertAlmostEqual(out[0]["latOnS"], 40.0 - 2.5, delta=0.2)
        self.assertNotIn("cc", out[0]["health"]["staleSteerS"])

    def test_stream_silent_during_steering_is_counted(self):
        for stream, key in (("carOutput", "co"), ("controlsState", "ctl"), ("drivingModelData", "dmd")):
            for period_ms in (100, 102.4, 125):
                with self.subTest(stream=stream, period_ms=period_ms):
                    times = synth.ticks(500, period_ms, T0)
                    lo, hi = times[0] + 20 * SEC, times[0] + 23 * SEC
                    def change(i, t_ns):
                        return {"skip": (stream,)} if lo <= t_ns < hi else None
                    out = synth.summaries(synth.run(make(), synth.drive(times, change)))
                    self.assertAlmostEqual(out[0]["health"]["staleSteerS"][key], 2.5, delta=0.2)
                    self.assertAlmostEqual(out[0]["health"]["staleS"][key], 2.5, delta=0.2)
                    self.assertEqual(set(out[0]["health"]["staleS"]), {key})

    def test_stale_time_outside_steering_is_not_stale_steer_time(self):
        def change(i, t_ns):
            return {"carControl": dict(lat=False), "skip": ("carOutput",) if 100 <= i < 200 else ()}
        out = synth.summaries(synth.run(make(), synth.drive(synth.ticks(300, 100, T0), change)))
        self.assertAlmostEqual(out[0]["health"]["staleS"]["co"], 9.5, delta=0.2)
        self.assertEqual(out[0]["health"]["staleSteerS"], {})

    def test_alert_list_goes_missing_after_five_seconds(self):
        def change(i, t_ns):                                           # in Drive at 10 m/s after Park
            skip = ("onroadEvents",) if 100 <= i < 160 else ()
            if i < 20:
                return {"carState": dict(v=0.0, gear="park"), "carControl": dict(lat=False), "skip": skip}
            return {"carState": dict(v=10.0), "onroadEvents": ["belowSteerSpeed"], "skip": skip}
        probe = Probe()
        out = synth.summaries(synth.run(make(probe), synth.drive(synth.ticks(300, 100, T0), change)))
        self.assertAlmostEqual(out[0]["health"]["staleS"]["ev"], 1.0, delta=0.15)       # 6 s silent, trusted for 5 s
        self.assertAlmostEqual(out[0]["health"]["staleSteerS"]["ev"], 1.0, delta=0.15)
        self.assertAlmostEqual(out[0]["fzFallbackS"], 1.0, delta=0.15)
        self.assertAlmostEqual(out[0]["forcedZeroS"], 28.0, delta=0.15)                 # the speed latch fills in

    def test_alert_list_handed_over_as_decoded_counts_like_a_list(self):
        class Reader:                                                  # iterable, and not a list or a tuple
            def __init__(self, items):
                self.items = items

            def __iter__(self):
                return iter(self.items)

        def summary(wrap):
            def change(i, t_ns):
                if i < 20:
                    return {"carState": dict(v=0.0, gear="park"), "carControl": dict(lat=False)}
                return {"carState": dict(v=10.0), "onroadEvents": ["belowSteerSpeed"] if i < 200 else []}
            batches = synth.drive(synth.ticks(300, 100, T0), change)
            for b in batches:
                t_ns, events = b["onroadEvents"]
                b["onroadEvents"] = (t_ns, wrap(events))
            return synth.summaries(synth.run(make(Probe()), batches))[0]

        as_list = summary(list)
        self.assertAlmostEqual(as_list["forcedZeroS"], 18.0, delta=0.15)
        self.assertAlmostEqual(as_list["steeringS"], 10.0, delta=0.15)
        self.assertEqual(summary(Reader), as_list)
        self.assertEqual(as_list["health"]["staleS"], {})

    def test_mid_drive_start_before_the_first_alert_list(self):
        def change(i, t_ns):
            return {"skip": ("onroadEvents",)} if i < 20 else None
        probe = Probe()
        synth.run(make(probe), synth.drive(synth.ticks(300, 100, T0), change))
        self.assertEqual(probe.total["forcedZeroS"], 0.0)
        self.assertAlmostEqual(probe.total["fzFallbackS"], 2.0, delta=0.2)

    def test_park_then_a_long_hole_then_slow_driving_without_alerts(self):
        parked = synth.ticks(600, 100, T0)
        times = parked + synth.ticks(300, 100, parked[-1] + 600 * SEC)
        def change(i, t_ns):
            if i < 600:
                return {"carState": dict(v=0.0, gear="park"), "carControl": dict(lat=False),
                        "skip": ("onroadEvents",)}
            return {"carState": dict(v=10.0), "skip": ("onroadEvents",)}
        probe = Probe()
        synth.run(make(probe), synth.drive(times, change))
        self.assertEqual(probe.total["forcedZeroS"], 0.0)
        self.assertAlmostEqual(probe.total["steeringS"], 30.0, delta=0.3)

    def test_messages_from_before_the_drive_are_dropped(self):
        for age_ms, expect_forced in ((3_000, 0.0), (500, 4.5)):
            with self.subTest(age_ms=age_ms):
                probe = Probe()
                p = make(probe)
                p.ingest_tick({"onroadEvents": (T0 - age_ms * MS, synth.alerts("belowSteerSpeed"))})
                batches = synth.drive(synth.ticks(100, 100, T0), lambda i, t: {"skip": ("onroadEvents",)})
                synth.run(p, batches)
                self.assertAlmostEqual(probe.total["forcedZeroS"], expect_forced, delta=0.15)

    def test_old_message_in_the_first_batch_is_dropped_too(self):
        probe = Probe()
        batches = synth.drive(synth.ticks(100, 100, T0), lambda i, t: {"skip": ("onroadEvents",)})
        batches[0]["onroadEvents"] = (T0 - 3 * SEC, synth.alerts("belowSteerSpeed"))   # unread since before the drive
        synth.run(make(probe), batches)
        self.assertEqual(probe.total["forcedZeroS"], 0.0)
        self.assertAlmostEqual(probe.total["fzFallbackS"], 10.0, delta=0.15)

    def test_given_start_time_keeps_the_second_before_it_as_a_live_start_does(self):
        for age_ms, expect_forced in ((3_000, 0.0), (500, 4.5)):
            with self.subTest(age_ms=age_ms):
                probe = Probe()
                p = make(probe, t0_mono_s=T0 / 1e9)
                p.ingest_tick({"onroadEvents": (T0 - age_ms * MS, synth.alerts("belowSteerSpeed"))})
                batches = synth.drive(synth.ticks(100, 100, T0), lambda i, t: {"skip": ("onroadEvents",)})
                synth.run(p, batches)
                self.assertAlmostEqual(probe.total["forcedZeroS"], expect_forced, delta=0.15)

    def test_replay_from_a_given_start_time_equals_the_live_start_at_that_car_state(self):
        # As in a decimated log: the three control streams are stamped 95 ms before their carState.
        def batches(first_i):
            out = []
            for i in range(first_i, 700):
                b = synth.batch(T0 + i * 100 * MS, skip=("carControl", "controlsState", "carOutput"))
                earlier = T0 + i * 100 * MS - 95 * MS
                b.update(carControl=(earlier, synth.car_control()), controlsState=(earlier, synth.controls_state()),
                         carOutput=(earlier, synth.car_output()))
                out.append(b)
            return out

        live_probe, replay_probe = Probe(), Probe()
        live = synth.summaries(synth.run(make(live_probe), batches(0)))     # the agent's first carState is at T0
        replay = synth.summaries(synth.run(make(replay_probe, t0_mono_s=T0 / 1e9), batches(-20)))   # the log starts earlier
        self.assertEqual(replay_probe.immediate, live_probe.immediate)      # no Sample from before the start time
        self.assertEqual(replay, live)
        self.assertEqual(replay[0]["latOnS"], 60.0)                         # the first Sample has its control message
        self.assertEqual(replay[0]["health"]["staleS"], {})

    def test_unknown_stream_is_ignored(self):
        p = make(Probe())
        b = synth.batch(T0)
        b["somethingElse"] = (T0, object())
        self.assertEqual(p.ingest_tick(b), [])
        self.assertEqual(len(STREAMS), 8)
        self.assertIn("liveDelay", STREAMS)


class ModelPathTests(unittest.TestCase):
    def test_newer_model_message_waits_for_the_next_sample(self):
        probe = Probe()
        times = synth.ticks(50, 100, T0)
        batches = [synth.batch(t) for t in times]
        for t, b in zip(times, batches):
            b["drivingModelData"] = (t + 8 * MS, synth.model())        # newer than the carState of its batch
        synth.run(make(probe), batches)
        self.assertEqual(len(probe.models), 49)                        # the last one never met a later Sample
        for (t_m, t_sample), t in zip(probe.models, times):
            self.assertAlmostEqual(t_m, (t + 8 * MS) / 1e9, places=6)
            self.assertAlmostEqual(t_sample - t_m, 0.092, places=6)
        self.assertEqual(probe.log[:5], ["sample", "sample", "model", "sample", "model"])

    def test_newer_message_replaces_the_held_one_while_no_sample_comes(self):
        probe = Probe()
        p = make(probe)
        first = synth.batch(T0)
        first["drivingModelData"] = (T0 + 8 * MS, synth.model())       # held: newer than its carState
        p.ingest_tick(first)
        p.ingest_tick({"drivingModelData": (T0 + 58 * MS, synth.model())})   # a tick without a carState
        p.ingest_tick(synth.batch(T0 + 100 * MS, skip=("drivingModelData",)))
        self.assertEqual([round(t_m - T0 / 1e9, 3) for t_m, _ in probe.models], [0.058])

    def test_decimated_stream_each_message_once(self):
        probe = Probe()
        times = synth.ticks(300, 100, T0)
        batches = []
        for i, t in enumerate(times):
            b = synth.batch(t, skip=("drivingModelData",))
            newest = times[i - i % 5]                                  # 2 Hz, offered again with every carState
            b["drivingModelData"] = (newest, synth.model())
            batches.append(b)
        out = synth.summaries(synth.run(make(probe, fresh_model_s=1.25), batches))
        self.assertEqual(len(probe.models), 60)
        self.assertTrue(all(t_m == t_sample for t_m, t_sample in probe.models))
        self.assertEqual(out[0]["health"]["modelGapN"], 0)             # a slow stream: 0.5 s spacing is no gap
        self.assertEqual(out[0]["health"]["staleS"], {})

    def test_model_gaps_are_counted(self):
        def change(i, t_ns):
            return {"skip": ("drivingModelData",)} if i in (100, 101, 102, 103) else None
        out = synth.summaries(synth.run(make(), synth.drive(synth.ticks(300, 100, T0), change)))
        self.assertEqual(out[0]["health"]["modelGapN"], 1)

    def test_raw_stream_reaches_only_the_measures_that_ask(self):
        class Delay(Measure):
            name = "delay"
            streams = ("liveDelay",)

            def __init__(self):
                self.got = []

            def on_stream(self, ctx, stream, t, msg):
                self.got.append((stream, t, msg.lateralDelay, ctx.w))

        class Other(Measure):
            name = "other"

            def on_stream(self, ctx, stream, t, msg):
                raise AssertionError("not asked for")

        delay, times = Delay(), synth.ticks(700, 100, T0)
        def change(i, t_ns):
            newest = times[i - i % 3]                                  # a new message every third tick
            return {"liveDelay": synth.live_delay(delay=0.3 + 0.0001 * (i // 3))} if newest == t_ns else None
        p = make(delay, Other())
        synth.run(p, synth.drive(times, change))
        self.assertEqual(len(delay.got), 234)
        self.assertEqual(delay.got[0], ("liveDelay", times[0] / 1e9, 0.3, 0))
        self.assertEqual(delay.got[-1][3], 1)
        self.assertEqual(p.drive_end_fields()["failed"], [])


class BookingTests(unittest.TestCase):
    def test_late_evaluation_books_to_the_window_of_the_triggering_sample(self):
        probe = Probe()
        synth.run(make(probe), synth.drive(synth.ticks(700, 100, T0)))
        self.assertTrue(all(w == int((t - 1000.0 + 1e-6) // 60) for t, w in probe.immediate))
        booked = {round(t - 1000.0, 1): w for t, w, _, _ in probe.evaluated}
        self.assertEqual(booked[53.9], 0)        # evaluated by the Sample at 59.9 s
        self.assertEqual(booked[54.0], 1)        # evaluated by the Sample at 60.0 s: books to the next window
        self.assertEqual(booked[57.0], 1)
        self.assertEqual(len(probe.evaluated), 700)

    def test_closing_summary_comes_before_what_the_triggering_sample_causes(self):
        probe = Probe()
        probe.emit_at = {600: "brake"}
        p = make(probe)
        batches = synth.drive(synth.ticks(601, 100, T0))
        for b in batches[:600]:
            p.ingest_tick(b)
        out = p.ingest_tick(batches[600])
        self.assertEqual([kind for kind, _ in out], ["summary", "brake"])
        self.assertEqual(out[0][1]["w"], 0)
        self.assertEqual(out[1][1], {"w": 1})    # an event is counted in the window open when it is sent

    def test_order_of_work_on_an_ordinary_sample(self):
        probe = Probe()
        p = make(probe)
        batches = synth.drive(synth.ticks(62, 100, T0))
        for b in batches[:61]:
            p.ingest_tick(b)
        del probe.log[:]
        p.ingest_tick(batches[61])
        self.assertEqual(probe.log, ["sample", "model", "evaluate"])

    def test_gap_sample_releases_then_resets_then_starts_the_new_run(self):
        probe = Probe()
        p = make(probe)
        batches = synth.drive(stalled(100, 300, 5))
        for b in batches[:100]:
            p.ingest_tick(b)
        del probe.log[:]
        p.ingest_tick(batches[100])
        self.assertEqual(probe.log, ["evaluate"] * 60 + ["gap", "sample", "model"])
        self.assertEqual(len(probe.evaluated), 100)

    def test_drive_end_evaluates_what_waits_and_scores_nothing_from_the_last_two_seconds(self):
        probe = Probe()
        p = make(probe)
        times = synth.ticks(330, 100, T0)
        for b in synth.drive(times):
            p.ingest_tick(b)
        self.assertEqual(len(probe.evaluated), 270)
        del probe.log[:]
        out = p.end()
        self.assertEqual(probe.log, ["evaluate"] * 60 + ["end"])
        self.assertEqual([kind for kind, _ in out], ["probe_end", "summary"])
        end_t = times[-1] / 1e9
        last = [(t, clean, ok) for t, _, clean, ok in probe.evaluated if t > end_t - 2.0 + 1e-6]
        self.assertEqual(len(last), 20)
        self.assertFalse(any(clean or ok for _, clean, ok in last))
        before = [ok for t, _, _, ok in probe.evaluated if end_t - 4.0 < t <= end_t - 3.2]
        self.assertTrue(all(before))             # still scored: 8.0 s of road around them is known
        self.assertAlmostEqual(out[1][1]["windowS"], 33.0, delta=0.051)

    def test_owner_facing_measures_run_first(self):
        order = []

        class First(Measure):
            name = "first"
            owner_facing = True

            def on_sample(self, ctx, g):
                order.append(self.name)

        class Second(First):
            name = "second"
            owner_facing = False

        p = make(Second(), First())
        p.ingest_tick(synth.batch(T0))
        self.assertEqual(order, ["first", "second"])
        self.assertEqual(p.measure_names, ["first", "second"])

    def test_every_drive_gets_fresh_measures(self):
        made = []

        def factory():
            made.append(Probe())
            return made[-1]

        config = PipelineConfig(measures=(factory,))
        first, second = Pipeline(config), Pipeline(config)
        synth.run(first, synth.drive(synth.ticks(50, 100, T0)))
        synth.run(second, synth.drive(synth.ticks(20, 100, T0)))
        self.assertEqual([len(p.immediate) for p in made], [50, 20])

    def test_default_measures_are_the_code_constant(self):
        import measure_set
        self.assertIsInstance(measure_set.MEASURES, tuple)
        self.assertEqual(Pipeline().measure_names, [m.name for m in measure_set.MEASURES])


class IsolationTests(unittest.TestCase):
    def numbers(self, *log_only):
        probe = Probe()
        p = make(probe, *log_only)
        out = synth.summaries(synth.run(p, synth.drive(stalled(900, 300, 900))))
        return probe, p, out

    def test_failing_log_only_measure_does_not_touch_the_others(self):
        clean_probe, _, clean_out = self.numbers(LogOnly())
        faulty = LogOnly(fail_in="on_sample", fail_at=100)
        probe, p, out = self.numbers(faulty)
        self.assertEqual(probe.total, clean_probe.total)
        self.assertEqual(probe.evaluated, clean_probe.evaluated)
        strip = lambda s: {k: v for k, v in s.items() if k not in ("lane", "health")}
        self.assertEqual([strip(s) for s in out], [strip(s) for s in clean_out])
        self.assertEqual(faulty.calls["on_sample"], 100)               # switched off for the rest of the drive
        self.assertEqual(faulty.calls["on_evaluate"], 39)
        self.assertEqual(faulty.calls["window_fields"], 0)
        for s in out:
            self.assertNotIn("lane", s)
            self.assertEqual(s["health"]["failed"], ["lane"])
        self.assertTrue(all("lane" in s and "failed" not in s["health"] for s in clean_out))
        self.assertEqual(p.drive_end_fields()["failed"], ["lane"])

    def test_failure_in_any_hook_is_contained(self):
        for hook in ("on_evaluate", "on_model", "on_gap", "on_drive_end", "window_fields"):
            with self.subTest(hook=hook):
                faulty = LogOnly(fail_in=hook, fail_at=1)
                probe, p, out = self.numbers(faulty)
                self.assertEqual(p.drive_end_fields()["failed"], ["lane"])
                self.assertEqual(out[-1]["health"]["failed"], ["lane"])
                self.assertNotIn("lane", out[-1])
                self.assertAlmostEqual(probe.total["latOnS"], 180.15, delta=0.2)

    def test_block_is_present_until_the_failure(self):
        faulty = LogOnly(fail_in="on_sample", fail_at=700)             # fails during the second window
        _, _, out = self.numbers(faulty)
        self.assertEqual(out[0]["lane"], {"n": 600})
        self.assertNotIn("failed", out[0]["health"])
        self.assertNotIn("lane", out[1])
        self.assertEqual(out[1]["health"]["failed"], ["lane"])

    def test_measure_that_cannot_be_built_is_listed_as_failed(self):
        class Broken(Measure):
            name = "ay"

            def __init__(self):
                raise RuntimeError("cannot start")

        probe = Probe()
        p = Pipeline(PipelineConfig(measures=(Broken, lambda: probe)))
        out = synth.summaries(synth.run(p, synth.drive(synth.ticks(50, 100, T0))))
        self.assertEqual(out[0]["health"]["failed"], ["ay"])
        self.assertEqual(p.measure_names, ["probe"])
        self.assertEqual(len(probe.immediate), 50)


class SummaryTests(unittest.TestCase):
    def test_health_block_is_always_there(self):
        out = synth.summaries(synth.run(make(), synth.drive(synth.ticks(100, 100, T0))))
        self.assertEqual(out[0]["health"], {"gapN": 0, "modelGapN": 0, "staleS": {}, "staleSteerS": {}})
        self.assertEqual(set(out[0]), {"w", "tMonoS", "n", "windowS", "unobservedS", "tickMaxS", "health"})

    def test_measure_cannot_replace_the_pipelines_own_fields(self):
        class Greedy(Measure):
            name = "swings"

            def window_fields(self):
                return {"w": 99, "tMonoS": -1.0, "n": -1, "windowS": -1.0, "unobservedS": -1.0, "tickMaxS": -1.0,
                        "health": "x", "swings": {"n": 3}}

        p = make(Greedy())
        out = synth.summaries(synth.run(p, synth.drive(synth.ticks(50, 100, T0))))
        self.assertEqual(out[0], {"w": 0, "tMonoS": 1000.0, "n": 50, "windowS": 5.0, "unobservedS": 0.0,
                                  "tickMaxS": 0.1, "swings": {"n": 3},      # its own block is kept
                                  "health": {"gapN": 0, "modelGapN": 0, "staleS": {}, "staleSteerS": {}}})

    def test_agent_health_values_are_merged(self):
        calls = []

        def source():
            calls.append(1)
            return {"cpuPct": 3.1, "rssMb": 21.0, "queue": 0, "dropped": 0, "tickErrors": 0}

        out = synth.summaries(synth.run(make(health_source=source), synth.drive(synth.ticks(700, 100, T0))))
        self.assertEqual(len(calls), 2)                                # once per summary
        self.assertEqual(out[0]["health"]["cpuPct"], 3.1)
        self.assertEqual(out[1]["health"]["gapN"], 0)

    def test_failing_agent_health_source_changes_no_measure_field(self):
        def summaries_with(source):
            batches = synth.drive(stalled(900, 300, 400))              # steering on throughout, one short stall
            return synth.summaries(synth.run(make(Probe(), LogOnly(), health_source=source), batches))

        def once(fault):
            calls = []

            def source():
                calls.append(1)
                return fault() if len(calls) == 1 else {"cpuPct": 3.1}
            return source

        def raises():
            raise OSError("probe failed")

        strip = lambda s: {k: v for k, v in s.items() if k != "health"}
        good = summaries_with(lambda: {"cpuPct": 3.1})
        self.assertEqual([s["latOnS"] for s in good], [60.0, 60.0, 10.2])
        for name, fault in (("raises", raises), ("none", lambda: None), ("list", lambda: [("cpuPct", 3.1)]),
                            ("text", lambda: "cpuPct")):
            with self.subTest(fault=name):
                out = summaries_with(once(fault))                      # the fault hits the first window close
                self.assertEqual([strip(s) for s in out], [strip(s) for s in good])
                self.assertEqual(out[0]["health"], {"gapN": 0, "modelGapN": 0, "staleS": {}, "staleSteerS": {}})
                self.assertEqual([s["health"]["gapN"] for s in out], [0, 1, 0])    # no gap was made by the fault
                self.assertEqual(out[1]["health"]["cpuPct"], 3.1)      # the agent values are back in the next one

    def test_agent_health_values_cannot_replace_the_pipelines_own(self):
        source = lambda: {"gapN": 99, "modelGapN": 99, "staleS": "x", "staleSteerS": "x", "failed": ["x"], "queue": 4}
        out = synth.summaries(synth.run(make(health_source=source), synth.drive(stalled(100, 300, 100))))
        self.assertEqual(out[0]["health"], {"gapN": 1, "modelGapN": 0, "staleS": {}, "staleSteerS": {}, "queue": 4})

    def test_drive_end_fields(self):
        probe = Probe()
        p = make(probe)
        synth.run(p, synth.drive(synth.ticks(700, 100, T0)))
        self.assertEqual(p.drive_end_fields(), {
            "windows": 2, "failed": [], "probeSamples": 700,
            "totals": {"windowS": 70.0, "unobservedS": 0.0, "steeringS": 70.0}})


class LifecycleTests(unittest.TestCase):
    def test_second_drive_of_a_boot_starts_from_nothing(self):
        first = make(Probe(), LogOnly(fail_in="on_sample", fail_at=10))
        out1 = synth.summaries(synth.run(first, synth.drive(synth.ticks(700, 100, T0))))
        self.assertEqual(out1[0]["health"]["failed"], ["lane"])

        start2 = T0 + 70 * SEC + 20 * 60 * SEC                         # 20 minutes parked in between
        second = make(Probe(), LogOnly())
        out2 = synth.summaries(synth.run(second, synth.drive(synth.ticks(700, 100, start2))))
        self.assertEqual([s["w"] for s in out2], [0, 1])               # its own grid, from its own first Sample
        self.assertEqual(out2[0]["tMonoS"], start2 / 1e9)
        self.assertEqual(out2[0]["unobservedS"], 0.0)
        self.assertEqual(out2[0]["health"]["gapN"], 0)
        self.assertNotIn("failed", out2[0]["health"])                  # a measure switched off is back on
        self.assertEqual(out2[0]["lane"], {"n": 600})
        self.assertEqual(second.drive_end_fields()["failed"], [])

    def test_restart_in_mid_drive_scores_nothing_for_five_seconds(self):
        probe = Probe()
        times = synth.ticks(200, 100, T0)
        synth.run(make(probe), synth.drive(times))
        scored = [t for t, _, _, ok in probe.evaluated if ok]
        self.assertAlmostEqual(min(scored), times[0] / 1e9 + 5.1, places=6)
        self.assertEqual(probe.total["forcedZeroS"], 0.0)


if __name__ == "__main__":
    unittest.main()
