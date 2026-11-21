"""Input adapter for drive tables stored as numpy .npz files.

Builds the batches of a decimated-log replay from the tables of one drive:
one batch per carState row, holding the newest message of each other stream
at or before it (a message with the same time stamp as the carState belongs to
its batch: carState goes last). Messages carry the cereal attribute names, so
the pipeline reads them like live ones.

The tables hold numbers only, no log header and only the learned delay value
of liveDelay, so settings and delay status are not available from them.
Not installed on the Comma.

Table layout (column 0 is the time in seconds since boot, column 1 the chunk):
  cs   vEgo aEgo steeringAngleDeg steeringRateDeg steeringTorque steeringTorqueEps steeringPressed
       gasPressed brakePressed gear leftBlinker rightBlinker cruiseEnabled ...
  cc   latActive enabled longActive actuatorsTorque ...
  ctl  curvature desiredCurvature ...
  co   torqueOutputCan ...
  rs   leadStatus dRel ...
  dmd  leftY rightY leftProb rightProb laneChangeState laneChangeDirection ...
  ld   lateralDelay
  ev   (alert names in `ev_names`, joined by "|")
"""
from __future__ import annotations

from types import SimpleNamespace as ns

GEARS = {1: "park", 2: "drive", 3: "neutral", 4: "reverse"}
LANE_CHANGE_STATES = {0: "off", 1: "preLaneChange", 2: "laneChangeStarting", 3: "laneChangeFinishing"}
LANE_CHANGE_DIRECTIONS = {0: "none", 1: "left", 2: "right"}


def _car_state(r):
    return ns(vEgo=r[2], steeringAngleDeg=r[4], steeringTorque=r[6], steeringTorqueEps=r[7],
              steeringPressed=r[8] > 0.5, brakePressed=r[10] > 0.5, gearShifter=GEARS.get(int(r[11]), "unknown"),
              leftBlinker=r[12] > 0.5, rightBlinker=r[13] > 0.5, cruiseState=ns(enabled=r[14] > 0.5))


def _car_control(r):
    return ns(latActive=r[2] > 0.5, actuators=ns(torque=r[5]))


def _controls_state(r):
    return ns(curvature=r[2], desiredCurvature=r[3])


def _car_output(r):
    return ns(actuatorsOutput=ns(torqueOutputCan=r[2]))


def _radar_state(r):
    return ns(leadOne=ns(status=r[2] > 0.5, dRel=r[3]))


def _model(r):
    return ns(laneLineMeta=ns(leftY=r[2], rightY=r[3], leftProb=r[4], rightProb=r[5]),
              meta=ns(laneChangeState=LANE_CHANGE_STATES.get(int(r[6]), "unknown"),
                      laneChangeDirection=LANE_CHANGE_DIRECTIONS.get(int(r[7]), "unknown")))


def _live_delay(r):
    return ns(lateralDelay=r[2])


_TABLES = (("carControl", "cc", _car_control), ("controlsState", "ctl", _controls_state),
           ("carOutput", "co", _car_output), ("radarState", "rs", _radar_state),
           ("drivingModelData", "dmd", _model), ("liveDelay", "ld", _live_delay))


def load(path) -> dict:
    """The tables of one drive, as plain lists of rows (floats), plus the alert names."""
    import numpy as np

    with np.load(path, allow_pickle=False) as data:
        tables = {name: data[name].tolist() for name in ("cs", "cc", "ctl", "co", "rs", "dmd", "ld", "ev")
                  if name in data.files}
        tables["ev_names"] = [str(x) for x in data["ev_names"]] if "ev_names" in data.files else []
    return tables


def _t_ns(row) -> int:
    return int(round(row[0] * 1e9))


def decimated_batches(tables: dict, without=()):
    """One batch per carState row: {stream: (logMonoTime_ns, message)}. Streams named in `without` are left out."""
    streams = []
    for stream, key, build in _TABLES:
        rows = tables.get(key) or []
        if stream not in without and rows and len(rows[0]) > 2:
            streams.append([stream, rows, build, 0])
    events = tables.get("ev") or []
    names = tables.get("ev_names") or []
    use_events = "onroadEvents" not in without and bool(events)
    event_i = 0
    for row in tables.get("cs") or []:
        t = row[0]
        batch = {}
        for entry in streams:
            stream, rows, build, i = entry
            start = i
            while i < len(rows) and rows[i][0] <= t:
                i += 1
            if i > start:
                entry[3] = i
                batch[stream] = (_t_ns(rows[i - 1]), build(rows[i - 1]))
        if use_events:
            start = event_i
            while event_i < len(events) and events[event_i][0] <= t:
                event_i += 1
            if event_i > start:
                listed = [ns(name=name) for name in names[event_i - 1].split("|") if name]
                batch["onroadEvents"] = (_t_ns(events[event_i - 1]), listed)
        batch["carState"] = (_t_ns(row), _car_state(row))
        yield batch
