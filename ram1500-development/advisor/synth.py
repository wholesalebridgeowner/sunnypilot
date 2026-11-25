"""Synthetic drives for tests: messages with the cereal attribute names and
batches as the agent builds them. Numbers are invented; nothing here comes
from a real drive. Test support only: never installed on the Comma.
"""
from __future__ import annotations

from types import SimpleNamespace as ns

MS = 1_000_000
SEC = 1_000_000_000


def car_state(v=25.0, angle=0.0, torque=0.0, eps=0.0, pressed=False, brake=False, cruise=False,
              gear="drive", left=False, right=False, gas=False, fault=False, can_valid=True, available=None):
    # available=None leaves cruise availability unobserved, as before the field was added.
    return ns(vEgo=v, canValid=can_valid, steeringAngleDeg=angle, steeringTorque=torque, steeringTorqueEps=eps,
              steeringPressed=pressed, brakePressed=brake, cruiseState=ns(enabled=cruise, available=available),
              gearShifter=gear, leftBlinker=left, rightBlinker=right, gasPressed=gas,
              steerFaultTemporary=fault, steerFaultPermanent=False)


def car_control(lat=True, torque=0.0):
    return ns(latActive=lat, actuators=ns(torque=torque))


def controls_state(desired=0.0, actual=None):
    return ns(desiredCurvature=desired, curvature=desired if actual is None else actual)


def car_output(torque=0.0):
    return ns(actuatorsOutput=ns(torqueOutputCan=torque))


def radar_state(lead=False, distance=0.0):
    return ns(leadOne=ns(status=lead, dRel=distance))


def model(state="off", direction="none", left_y=-1.8, right_y=1.8, left_prob=0.95, right_prob=0.95):
    return ns(meta=ns(laneChangeState=state, laneChangeDirection=direction),
              laneLineMeta=ns(leftY=left_y, rightY=right_y, leftProb=left_prob, rightProb=right_prob))


def alerts(*names):
    return [ns(name=name) for name in names]


def live_delay(delay=0.33, status="estimated", blocks=30, estimate=0.33, std=0.01):
    return ns(lateralDelay=delay, status=status, validBlocks=blocks,
              lateralDelayEstimate=estimate, lateralDelayEstimateStd=std)


_BUILDERS = {"carState": car_state, "carControl": car_control, "controlsState": controls_state,
             "carOutput": car_output, "radarState": radar_state, "drivingModelData": model,
             "liveDelay": live_delay}
EVERY_TICK = ("carState", "carControl", "controlsState", "carOutput", "radarState", "drivingModelData",
              "onroadEvents")


def batch(t_ns, skip=(), **streams):
    """One tick with every stream stamped `t_ns`: a straight at 25 m/s, steering on, no alert.

    A keyword names a stream and gives what differs: a dict of builder
    arguments (`carState=dict(pressed=True)`), a list of alert names for
    onroadEvents, or a ready-made message. liveDelay is only present when
    named. A stream listed in `skip` is left out.
    """
    out = {}
    for stream in EVERY_TICK + tuple(s for s in streams if s not in EVERY_TICK):
        if stream in skip:
            continue
        value = streams.get(stream)
        if stream == "onroadEvents":
            msg = alerts(*(value or ()))
        elif value is None or isinstance(value, dict):
            msg = _BUILDERS[stream](**(value or {}))
        else:
            msg = value
        out[stream] = (t_ns, msg)
    return out


def ticks(n, period_ms=100.0, start_ns=1_000 * SEC):
    """`n` tick times, `period_ms` apart."""
    return [start_ns + int(round(i * period_ms * MS)) for i in range(n)]


def drive(times_ns, change=None):
    """One batch per tick time. `change(i, t_ns)` returns the keywords for `batch` that differ, or None."""
    return [batch(t_ns, **((change(i, t_ns) if change else None) or {})) for i, t_ns in enumerate(times_ns)]


def tick_batches(events, tick_times_ns):
    """Batches as the agent's drain builds them from full-rate streams.

    `events` are (t_ns, stream, message). Each batch holds, per stream, the
    newest message stamped after the previous tick and at or before this one.
    """
    events = sorted(events, key=lambda e: e[0])
    out, i = [], 0
    for tick_ns in tick_times_ns:
        newest = {}
        while i < len(events) and events[i][0] <= tick_ns:
            t_ns, stream, msg = events[i]
            newest[stream] = (t_ns, msg)
            i += 1
        out.append(newest)
    return out


def run(pipeline, batches):
    """Feed the batches, end the drive, and return every message as (type, data)."""
    out = []
    for b in batches:
        out.extend(pipeline.ingest_tick(b))
    out.extend(pipeline.end())
    return out


def summaries(messages):
    return [data for kind, data in messages if kind == "summary"]
