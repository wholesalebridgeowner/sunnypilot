#!/usr/bin/env python3
"""Drive Advisor agent on the Comma.

Listens to existing sunnypilot messages (never publishes, never sends CAN) and
measures: on every drive it sends the settings in force, one summary per
minute, one record per event and a drive-end record. It changes no setting.
Applying owner-approved settings through the Applier is locked on this build
(applier.py); the one thing still written is what an earlier trial replaced,
put back at agent start and at drive end.

The agent only builds batches from its sockets: the newest message of each
stream since the previous tick. Everything after the batch happens in the
per-drive Pipeline (pipeline.py), which the replay tool uses too.

A drive, in order:

    drive_start    at once: settings snapshot and the learner's persisted delay
    (30 s wait, so that sunnypilot's own programs get their listener slots first)
    slot check, eight new sockets, the delay probe, then drive_ready
    summary, brake, lane_change, limit_event    from the pipeline, as they come
    settings_change    when a watched setting differs from the last snapshot sent
    listening_disabled when the slots were full, or the agent used too much CPU or memory
    drive_end      after the last, partial window

Nothing crosses drives: every drive gets its own Pipeline, its own sockets,
its own slot check and its own listening state. All device imports are inside
main(), so Agent is testable off the Comma with fakes.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import applier as apply_lock
from applier import Applier, VehicleState
from delay_probe import UNKNOWN, DelayProbe
from device_params import (DeviceParams, base_actuator_delay_s, cache_delay_s, nnlc_match, persisted_live_delay)
from journal import Journal, NonceLedger
from measure_delay import delay_record, trusted_delay_s
from messages import drive_end_data, drive_ready_data, drive_start_data, envelope, fit
from pipeline import STREAMS, Pipeline, PipelineConfig
from samples import get
from settings_snapshot import changed_keys, nnlc_model_token, read_settings, reread_watched
from uplink import Uplink

AGENT_VERSION = "2.0"
TICK_S = 0.1                  # sleep between two ticks
ERROR_BACKOFF_S = 1.0         # extra sleep after a tick that raised
SUBSCRIBE_DELAY_S = 30.0      # let sunnypilot's own programs subscribe first
STATE_FRESH_S = 1.0           # carState/carControl older than this = unknown
APPLY_RETRY_S = 1.0
HEALTH_WINDOW_S = 10.0        # the agent reads its own CPU time and memory this often
CPU_LIMIT_PCT = 15.0          # of one core, for two readings in a row
RSS_LIMIT_MB = 150.0
PROBE_NOT_AVAILABLE = {"result": UNKNOWN, "helperSha": None, "probeMs": 0}


class Agent:
    def __init__(self, *, params, applier, uplink, socket_factory, decode, slot_check,
                 car_params_decode=None, car_params_sp_decode=None, live_delay_decode=None, delay_probe=None,
                 wall_clock=time.time, monotonic=time.monotonic, resource_probe=None,
                 subscribe_delay_s=SUBSCRIBE_DELAY_S, on_tick_error=None):
        self.params = params
        self.applier = applier
        self.uplink = uplink
        self.socket_factory = socket_factory
        self.decode = decode
        self.slot_check = slot_check
        # Decoders of stored messages; None = the Comma's own (device_params.py).
        self._decoders = {"car": car_params_decode, "car_sp": car_params_sp_decode, "delay": live_delay_decode}
        self.delay_probe = delay_probe
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.resource_probe = resource_probe
        self.subscribe_delay_s = subscribe_delay_s
        self.on_tick_error = on_tick_error
        self.boot_id = uuid.uuid4().hex[:12]
        self.seq = 0
        self.onroad = False
        self.tick_errors = 0            # ticks that raised, since the agent started
        self.probe = None               # newest result of the delay probe; None = it has not run
        self.pending = {}
        self.next_apply_try = 0.0
        self._ticked = False
        self._clear_drive()

    def _clear_drive(self) -> None:
        """The state of one drive. Nothing of it crosses into the next."""
        self.drive_id = None
        self.onroad_since = None
        self.pipeline = None
        self.socks = None
        self.listening = True
        self.health = None              # (time, CPU seconds) of the newest reading
        self.cpu_breach = False
        self.settings = None            # the snapshot sent last
        self.drive_probe = None         # the probe result of this drive; None = the drive never reached it
        self.latest_state = {}
        self._cpu_window = [0.0, 0.0]   # CPU seconds and elapsed seconds of the readings since the last summary
        self._cpu_peak = None           # highest reading since the last summary
        self._rss_mb = None             # newest reading
        self._window_tick_errors = 0

    # --- events -------------------------------------------------------------
    def _event(self, kind: str, data: dict) -> None:
        self.seq += 1
        self.uplink.send(fit(envelope(kind, self.drive_id, f"{self.boot_id}-{self.seq}", data)))

    def _send(self, messages: list, watch: bool = True) -> None:
        """Send what the pipeline handed back. A closed window is also the moment to look at the settings again."""
        window_closed = False
        for kind, data in messages:
            self._event(kind, data)
            window_closed = window_closed or kind == "summary"
        if window_closed and watch:
            self._settings_changed(reread_watched(self.params, self.settings))

    def _settings_changed(self, now: dict) -> None:
        changed = changed_keys(self.settings, now)
        if changed:
            self.settings = now
            self._event("settings_change", {"settings": now, "changed": changed})

    # --- stored values (read-only) ----------------------------------------------
    def _decoded(self, reader, which: str):
        decode = self._decoders[which]
        return reader(self.params) if decode is None else reader(self.params, decode)

    def _with_stored_delays(self, delay: dict) -> dict:
        """A delay record with the two fields that do not come from the learner's message read in."""
        stored = delay_record(None, cache_s=cache_delay_s(self.params),
                              base_actuator_s=self._decoded(base_actuator_delay_s, "car"))
        return dict(delay, cacheS=stored["cacheS"], baseActuatorS=stored["baseActuatorS"])

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        status = self.applier.restore(onroad=self.params.onroad())
        self._event("agent_start", {"restore": status})

    def step(self) -> float:
        """One tick that cannot stop the agent. Returns how long to sleep before the next one."""
        try:
            self.tick()
        except Exception as error:      # never die on one bad tick; restore runs at drive end and at the next start
            self.tick_errors += 1
            self._window_tick_errors += 1
            if self.on_tick_error is not None:
                try:
                    self.on_tick_error(error)
                except Exception:
                    pass
            return TICK_S + ERROR_BACKOFF_S
        return TICK_S

    def tick(self) -> None:
        now = self.monotonic()
        onroad = self.params.onroad()
        first_tick, self._ticked = not self._ticked, True
        if onroad and not self.onroad:
            self._drive_start(now, resumed=first_tick)
        elif not onroad and self.onroad:
            self._drive_end()
        self.onroad = onroad
        if onroad:
            self._maybe_subscribe(now)
            if self.listening and self.socks:
                self._drain(now)
            self._check_health(now)
        if apply_lock.APPLY_ENABLED:
            self._handle_approvals(now)
            ended = self.applier.check_owner_override()
            if ended:
                self._event("trial_ended", {"reason": ended})

    def _drive_start(self, now: float, resumed: bool) -> None:
        self._clear_drive()
        self.drive_id = uuid.uuid4().hex
        self.onroad_since = now
        self.applier.new_drive()
        self.pipeline = Pipeline(PipelineConfig(health_source=self._health_values))
        self.uplink.set_onroad(True)
        self.settings = read_settings(self.params)
        delay = self._with_stored_delays(delay_record(self._decoded(persisted_live_delay, "delay")))
        self._event("drive_start", drive_start_data(
            resumed=resumed, settings=self.settings, delay=delay, version=AGENT_VERSION,
            measures=self.pipeline.measure_names, apply_enabled=apply_lock.APPLY_ENABLED))

    def _drive_end(self) -> None:
        self._send(self.pipeline.end(), watch=False)        # what waits is evaluated; the last, partial window
        status = self.applier.restore(onroad=False)
        self._settings_changed(read_settings(self.params))  # the full snapshot, model bundle included
        fields = self.pipeline.drive_end_fields()
        # The delay record comes from the learner's newest message of the drive (the pipeline holds it).
        delay = self._with_stored_delays(fields.get("delay") or delay_record(None))
        self._event("drive_end", drive_end_data(fields, listening=self.listening, restore=status,
                                                settings=self.settings, delay=delay, probe=self.drive_probe))
        self._close_sockets()
        self._clear_drive()
        self.uplink.set_onroad(False)

    # --- listening ----------------------------------------------------------
    def _maybe_subscribe(self, now: float) -> None:
        if self.socks is not None or not self.listening:
            return
        if now - self.onroad_since < self.subscribe_delay_s:
            return
        if not self.slot_check():
            self._stop_listening("subscriber_slots_full")
            return
        self.socks = {stream: self.socket_factory(stream) for stream in STREAMS}
        self.drive_probe = self.probe = dict(self.delay_probe()) if self.delay_probe else dict(PROBE_NOT_AVAILABLE)
        name, fuzzy = self._decoded(nnlc_match, "car_sp")
        self._event("drive_ready", drive_ready_data(probe=self.drive_probe, nnlc_model=nnlc_model_token(name),
                                                    nnlc_fuzzy=fuzzy))

    def _drain(self, now: float) -> None:
        """Empty every socket, decode the newest message of each stream, and hand the batch to the pipeline."""
        batch = {}
        for stream, sock in self.socks.items():
            newest = None
            while (raw := sock.receive(non_blocking=True)) is not None:
                newest = raw
            if newest is None:
                continue
            try:
                t_ns, msg = self.decode(stream, newest)
            except Exception:
                continue                # this stream is missing for this tick
            batch[stream] = (t_ns, msg)
            if stream in ("carState", "carControl"):
                self.latest_state[stream] = (now, msg)
        if batch:
            self._send(self.pipeline.ingest_tick(batch))

    def _stop_listening(self, reason: str, cpu_pct=None, rss_mb=None) -> None:
        """No further samples on this drive. What was measured so far is sent at once."""
        self.listening = False
        self._send(self.pipeline.end())
        self._close_sockets()
        self._event("listening_disabled", {"reason": reason, "cpuPct": _round1(cpu_pct), "rssMb": _round1(rss_mb)})

    def _close_sockets(self) -> None:
        socks, self.socks = self.socks, None
        for sock in (socks or {}).values():
            close = getattr(sock, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass                # dropping the socket frees its listener slot either way

    # --- the agent's own cost -------------------------------------------------
    def _check_health(self, now: float) -> None:
        if self.resource_probe is None or not self.listening:
            return
        if self.health is not None and now - self.health[0] < HEALTH_WINDOW_S:
            return
        cpu_s, rss_mb = self.resource_probe()
        self._rss_mb = rss_mb
        if self.health is None:
            self.health = (now, cpu_s)
            return
        elapsed, used = now - self.health[0], cpu_s - self.health[1]
        self.health = (now, cpu_s)
        cpu_pct = 100.0 * used / elapsed
        self._cpu_window[0] += used
        self._cpu_window[1] += elapsed
        if self._cpu_peak is None or cpu_pct > self._cpu_peak:
            self._cpu_peak = cpu_pct
        breach = cpu_pct > CPU_LIMIT_PCT
        if (breach and self.cpu_breach) or rss_mb > RSS_LIMIT_MB:
            self._stop_listening("self_limit", cpu_pct, rss_mb)
        self.cpu_breach = breach

    def _health_values(self) -> dict:
        """The agent's own values for the window that is closing (asked for by the pipeline)."""
        used, elapsed = self._cpu_window
        values = {"cpuPct": _round1(100.0 * used / elapsed) if elapsed > 0 else None,
                  "cpuPeakPct": _round1(self._cpu_peak), "rssMb": _round1(self._rss_mb),
                  "queue": self.uplink.queue_len(), "dropped": self.uplink.take_dropped(),
                  "tickErrors": self._window_tick_errors}
        self._cpu_window = [0.0, 0.0]
        self._cpu_peak = None
        self._window_tick_errors = 0
        return values

    # --- approvals (locked on this build) ------------------------------------------
    def vehicle_state(self) -> VehicleState:
        if not self.onroad:
            return VehicleState(in_park=True, speed_ms=0.0, engaged=False)   # ignition off
        now = self.monotonic()
        car = self.latest_state.get("carState")
        control = self.latest_state.get("carControl")
        if car is None or now - car[0] > STATE_FRESH_S:
            return VehicleState(in_park=False, speed_ms=99.0, engaged=True)  # unknown = not parked
        speed = get(car[1], "vEgo")
        engaged = control is None or now - control[0] > STATE_FRESH_S or get(control[1], "enabled") is not False
        return VehicleState(in_park=str(get(car[1], "gearShifter")) == "park",
                            speed_ms=float(speed) if isinstance(speed, (int, float)) else 99.0,
                            engaged=engaged)

    def _handle_approvals(self, now: float) -> None:
        if not apply_lock.APPLY_ENABLED:
            return
        for approval in self.uplink.approvals():
            if isinstance(approval, dict) and isinstance(approval.get("nonce"), str):
                self.pending[approval["nonce"]] = approval
        if not self.pending or now < self.next_apply_try:
            return
        self.next_apply_try = now + APPLY_RETRY_S
        # The learned delay comes from the learner's own message, never from the cached total.
        learned = trusted_delay_s(self._decoded(persisted_live_delay, "delay"))
        base = self._decoded(base_actuator_delay_s, "car")
        state = self.vehicle_state()
        for nonce, approval in list(self.pending.items()):
            result = self.applier.apply(approval, state, now=self.wall_clock(),
                                        learned_delay_s=learned, base_actuator_delay_s=base,
                                        delay_probe=(self.probe or {}).get("result", UNKNOWN))
            if result == "refused:not_parked":
                continue   # retried until parked or expired
            del self.pending[nonce]
            self._event("apply_result", {"suggestionId": approval.get("suggestion_id"), "status": result})


def _round1(value):
    return None if value is None else round(value, 1)


def _resource_probe():
    rss_kib = 0
    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("RssAnon:"):
                    rss_kib = int(line.split()[1])
    except OSError:
        pass
    return time.process_time(), rss_kib / 1024.0


def main() -> None:  # pragma: no cover - runs on the Comma
    import sys

    # Before anything is imported from the build: the agent leaves no file under /data/openpilot.
    sys.dont_write_bytecode = True

    import fcntl

    home = Path(os.environ.get("ADVISOR_HOME", "/data/ram-diag/advisor"))
    lock = open(home / "agent.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return   # another agent is already running this boot
    sys.path.insert(0, str(home.parent))   # device_telemetry.py (subscriber-slot reader)
    import device_telemetry
    from cereal import messaging

    key = bytes.fromhex((home / "advisor.key").read_text().strip())
    params = DeviceParams()
    applier = Applier(params, Journal(home / "trial.json"), NonceLedger(home / "nonces.json"), key)
    uplink = Uplink((home / "advisor.url").read_text().strip(), key)
    uplink.start()

    def decode(service, raw):
        event = messaging.log_from_bytes(raw)
        return event.logMonoTime, getattr(event, service)

    def slot_check():
        return not device_telemetry.slot_problems(device_telemetry.msgq_reader_counts(STREAMS))

    log_path = home / "agent.log"

    def log_error(error):
        if not log_path.exists() or log_path.stat().st_size < 1_000_000:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{time.time():.0f} tick_error {type(error).__name__}\n")

    agent = Agent(params=params, applier=applier, uplink=uplink, socket_factory=messaging.sub_sock,
                  decode=decode, slot_check=slot_check, delay_probe=DelayProbe().run,
                  resource_probe=_resource_probe, on_tick_error=log_error)
    agent.start()
    while True:
        time.sleep(agent.step())


if __name__ == "__main__":
    main()
