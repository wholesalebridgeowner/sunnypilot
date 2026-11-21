#!/usr/bin/env python3
"""Drive Advisor agent on the Comma.

Listens to existing sunnypilot messages (never publishes, never sends CAN) and
sends one summary per minute. It changes no setting: applying owner-approved
settings through the Applier is locked on this build (applier.py). All device
imports are inside main(), so Agent is testable off the Comma with fakes.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import applier as apply_lock
from applier import VehicleState
from device_params import base_actuator_delay_s, learned_delay_s
from metrics import MinuteSummary
from samples import SampleBuilder, get

SERVICES = ("carState", "carControl", "controlsState", "carOutput", "radarState",
            "drivingModelData", "onroadEvents")
SETTINGS = ("LagdToggle", "LagdToggleDelay", "NeuralNetworkLateralControl")
SAMPLE_HZ = 10.0
WINDOW_S = 60.0
SUBSCRIBE_DELAY_S = 30.0      # let sunnypilot's own programs subscribe first
STATE_FRESH_S = 1.0           # carState/carControl older than this = unknown
APPLY_RETRY_S = 1.0
HEALTH_WINDOW_S = 10.0
CPU_LIMIT_PCT = 15.0
RSS_LIMIT_MB = 150.0


class Agent:
    def __init__(self, *, params, applier, uplink, socket_factory, decode, slot_check,
                 car_params_decode=None, wall_clock=time.time, monotonic=time.monotonic,
                 resource_probe=None, subscribe_delay_s=SUBSCRIBE_DELAY_S):
        self.params = params
        self.applier = applier
        self.uplink = uplink
        self.socket_factory = socket_factory
        self.decode = decode
        self.slot_check = slot_check
        self.car_params_decode = car_params_decode
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self.resource_probe = resource_probe
        self.subscribe_delay_s = subscribe_delay_s
        self.boot_id = uuid.uuid4().hex[:12]
        self.seq = 0
        self.socks = None
        self.listening = True
        self.onroad = False
        self.onroad_since = None
        self.drive_id = None
        self.builder = None
        self.minute = None
        self.latest_state = {}
        self.pending = {}
        self.next_apply_try = 0.0
        self.health = None
        self.cpu_breach = False

    # --- events -------------------------------------------------------------
    def _event(self, kind: str, data: dict) -> None:
        self.seq += 1
        self.uplink.send({"type": kind, "driveId": self.drive_id, "id": f"{self.boot_id}-{self.seq}", "data": data})

    def _delays(self):
        base = (base_actuator_delay_s(self.params, self.car_params_decode) if self.car_params_decode
                else base_actuator_delay_s(self.params))
        return learned_delay_s(self.params), base

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        status = self.applier.restore(onroad=self.params.onroad())
        self._event("agent_start", {"restore": status})

    def tick(self) -> None:
        now = self.monotonic()
        onroad = self.params.onroad()
        if onroad and not self.onroad:
            self._drive_start(now)
        elif not onroad and self.onroad:
            self._drive_end()
        self.onroad = onroad
        if onroad:
            self._maybe_subscribe(now)
            if self.listening and self.socks:
                self._drain(now)
            self._check_health(now)
        self._handle_approvals(now)
        ended = self.applier.check_owner_override()
        if ended:
            self._event("trial_ended", {"reason": ended})

    def _drive_start(self, now: float) -> None:
        self.drive_id = uuid.uuid4().hex
        self.onroad_since = now
        self.applier.new_drive()
        self.builder = SampleBuilder(sample_hz=SAMPLE_HZ)
        self.minute = None
        self.latest_state = {}
        learned, base = self._delays()
        self._event("drive_start", {"learnedDelayS": learned, "baseActuatorDelayS": base,
                                    "settings": {k: self.params.get(k) for k in SETTINGS}})

    def _drive_end(self) -> None:
        if self.minute is not None and self.minute.samples:
            self._send_minute()
        status = self.applier.restore(onroad=False)
        self._event("drive_end", {"restore": status})
        self.drive_id = None
        self.builder = None
        self.minute = None
        self.latest_state = {}

    def _maybe_subscribe(self, now: float) -> None:
        if self.socks is not None or not self.listening:
            return
        if now - self.onroad_since < self.subscribe_delay_s:
            return
        if not self.slot_check():
            self.listening = False
            self._event("listening_disabled", {"reason": "subscriber_slots_full"})
            return
        self.socks = {service: self.socket_factory(service) for service in SERVICES}

    # --- listening ----------------------------------------------------------
    def _drain(self, now: float) -> None:
        newest = {}
        for service, sock in self.socks.items():
            while (raw := sock.receive(non_blocking=True)) is not None:
                newest[service] = raw
        decoded = []
        for service, raw in newest.items():
            try:
                t_ns, msg = self.decode(service, raw)
            except Exception:
                continue
            decoded.append((service == "carState", t_ns, service, msg))
        for _, t_ns, service, msg in sorted(decoded, key=lambda item: (item[0], item[1])):
            if service in ("carState", "carControl"):
                self.latest_state[service] = (now, msg)
            sample = self.builder.ingest(service, t_ns, msg)
            if sample is None:
                continue
            prev = None
            if self.minute is not None and sample.t - self.minute.start_t >= WINDOW_S:
                prev = self.minute.last_sample
                self._send_minute()
            if self.minute is None:
                self.minute = MinuteSummary(start_t=sample.t, prev=prev)
            self.minute.add(sample)

    def _send_minute(self) -> None:
        data = self.minute.finish(SAMPLE_HZ)
        data.pop("startT", None)
        data["trialActive"] = self.applier.journal.is_open()
        data["trialsThisDrive"] = self.applier.live_trials_this_drive
        self._event("summary", data)
        self.minute = None

    def _check_health(self, now: float) -> None:
        if self.resource_probe is None or not self.listening:
            return
        cpu_s, rss_mb = self.resource_probe()
        if self.health is None:
            self.health = (now, cpu_s)
            return
        if now - self.health[0] < HEALTH_WINDOW_S:
            return
        cpu_pct = 100.0 * (cpu_s - self.health[1]) / (now - self.health[0])
        self.health = (now, cpu_s)
        breach = cpu_pct > CPU_LIMIT_PCT
        if (breach and self.cpu_breach) or rss_mb > RSS_LIMIT_MB:
            self.listening = False
            self._event("listening_disabled", {"reason": "self_limit", "cpuPct": round(cpu_pct, 1),
                                               "rssMb": round(rss_mb, 1)})
        self.cpu_breach = breach

    # --- approvals ----------------------------------------------------------
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
        learned, base = self._delays()
        state = self.vehicle_state()
        for nonce, approval in list(self.pending.items()):
            result = self.applier.apply(approval, state, now=self.wall_clock(),
                                        learned_delay_s=learned, base_actuator_delay_s=base)
            if result == "refused:not_parked":
                continue   # retried until parked or expired
            del self.pending[nonce]
            self._event("apply_result", {"suggestionId": approval.get("suggestion_id"), "status": result})


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
    import fcntl
    import sys

    home = Path(os.environ.get("ADVISOR_HOME", "/data/ram-diag/advisor"))
    lock = open(home / "agent.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return   # another agent is already running this boot
    sys.path.insert(0, str(home.parent))   # device_telemetry.py (subscriber-slot reader)
    import device_telemetry
    from cereal import messaging

    from applier import Applier
    from device_params import DeviceParams
    from journal import Journal, NonceLedger
    from uplink import Uplink

    key = bytes.fromhex((home / "advisor.key").read_text().strip())
    params = DeviceParams()
    applier = Applier(params, Journal(home / "trial.json"), NonceLedger(home / "nonces.json"), key)
    uplink = Uplink((home / "advisor.url").read_text().strip(), key)
    uplink.start()

    def decode(service, raw):
        event = messaging.log_from_bytes(raw)
        body = getattr(event, service)
        return event.logMonoTime, (list(body) if service == "onroadEvents" else body)

    def slot_check():
        return not device_telemetry.slot_problems(device_telemetry.msgq_reader_counts(SERVICES))

    agent = Agent(params=params, applier=applier, uplink=uplink, socket_factory=messaging.sub_sock,
                  decode=decode, slot_check=slot_check, resource_probe=_resource_probe)
    log_path = home / "agent.log"
    agent.start()
    while True:
        try:
            agent.tick()
        except Exception as error:   # never die on one bad tick; restore runs at drive end/next start
            if not log_path.exists() or log_path.stat().st_size < 1_000_000:
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(f"{time.time():.0f} tick_error {type(error).__name__}\n")
            time.sleep(1.0)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
