"""Combine the advisor and Qt screen's existing reads. No native queue dependencies."""
import contextlib
import json
import math
import os
from pathlib import Path
import socket
import stat
import time

MPH_PER_MS = 3600 / 1609.344
RELAY_MAX_AGE_US = 1_250_000
TOPIC_AGE_US = {"deviceState": 1_500_000, "carParams": 75_000_000, "carStateSP": 100_000,
                "carState": 100_000, "carControl": 100_000, "controlsState": 100_000, "drivingModelData": 500_000}
LANES = ("off", "preLaneChange", "laneChangeStarting", "laneChangeFinishing")
DIESEL_FLAGS = {"fuel_us_gal": ("FUEL_LEVEL_VALIDATED",), "average_mpg": ("AVERAGE_MPG_VALIDATED",),
                "instant_mpg": ("FUEL_FLOW_VALIDATED", "INSTANT_MPG_VALIDATED"),
                "idle_gph": ("FUEL_FLOW_VALIDATED", "IDLE_RATE_VALIDATED"),
                "range_mi": ("RANGE_VALIDATED",), "def_pct": ("DEF_VALIDATED",)}


def empty_snapshot():
  return {"stale": True, "speed_mph": None, "set_speed_mph": None, "speed_limit_mph": None,
          "regen": "Unknown", "diesel": {}, "steering_active": None, "steering_limited": None,
          "blindspot_available": None, "lane_change": None}


def nonnegative(value):
  return type(value) in (int, float) and 0 <= value < 1e15 and math.isfinite(value)


def topic(packet, name, now, length, started=0):
  if packet is None:
    return None
  at = packet["at_us"]
  row = packet["fields"].get(name)
  if not (0 <= now - at < RELAY_MAX_AGE_US and isinstance(row, list) and len(row) == length):
    return None
  stamp = row[0]
  if type(stamp) is not int or stamp <= 0 or stamp < started or not 0 <= at - stamp < TOPIC_AGE_US[name]:
    return None
  # The 2 Hz handoff may lag by one interval. Repeated envelopes cannot acquire
  # a new source lifetime simply because the relay or the browser is still alive.
  return row if now - stamp < TOPIC_AGE_US[name] + RELAY_MAX_AGE_US else None


def diesel_values(result, sp):
  regen, readings = sp[2:]
  if type(regen) is not bool and not readings:
    return
  from opendbc.car.chrysler import ram_dt_diesel as validated
  if type(regen) is bool and validated.REGEN_VALIDATED:
    result["regen"] = "Active" if regen else "Inactive"
  if isinstance(readings, dict):
    for key, flags in DIESEL_FLAGS.items():
      value = readings.get(key)
      if nonnegative(value) and (key != "def_pct" or value <= 100) and all(getattr(validated, flag) for flag in flags):
        result["diesel"][key] = value


def combine(advisor, screen, now):
  result = empty_snapshot()
  heartbeat = topic(screen, "deviceState", now, 3)
  if heartbeat is None or heartbeat[1] is not True:
    return result
  start = heartbeat[2]
  if type(start) is not int or not 0 < start <= heartbeat[0]:
    return result
  car = topic(advisor, "carState", now, 3, start)
  if car is None or not nonnegative(car[1]):
    return result
  result.update(stale=False, speed_mph=car[1] * MPH_PER_MS)
  if nonnegative(car[2]) and 0 < car[2] * 3.6 < 254.5:
    result["set_speed_mph"] = car[2] * MPH_PER_MS
  cc = topic(advisor, "carControl", now, 2, start)
  if cc is not None and type(cc[1]) is bool:
    result["steering_active"] = cc[1]
  ctl = topic(advisor, "controlsState", now, 2, start)
  if result["steering_active"] and ctl is not None and type(ctl[1]) is bool:
    result["steering_limited"] = ctl[1]
  lane = topic(advisor, "drivingModelData", now, 2, start)
  if lane is not None and lane[1] in LANES:
    result["lane_change"] = lane[1]
  cp = topic(screen, "carParams", now, 2, start)
  if cp is not None and type(cp[1]) is bool:
    result["blindspot_available"] = cp[1]
  sp = topic(screen, "carStateSP", now, 4, start)
  if sp is not None:
    if nonnegative(sp[1]) and sp[1] > 0:
      result["speed_limit_mph"] = sp[1] * MPH_PER_MS
    diesel_values(result, sp)
  return result


class SharedSource:
  """Private, same-user datagrams. Each packet replaces its source's entire snapshot."""
  def __init__(self, directory=None, clock=None):
    self.directory = Path(directory or f"/dev/shm/rampilot-dash-{os.geteuid()}")
    self.clock = clock or (lambda: time.monotonic_ns() // 1000)
    self.sockets = {}
    self.packets = {}
    self.last_stamp = {}
    self.cleanup = contextlib.ExitStack()

  def __enter__(self):
    self.directory.mkdir(mode=0o700, exist_ok=True)
    st = self.directory.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
      raise RuntimeError("dashboard relay needs a private owner-only directory")
    try:
      for name in ("advisor", "screen"):
        path = self.directory / (name + ".sock")
        # Called under the service singleton. Recover only our own residual socket.
        if path.exists() or path.is_symlink():
          old = path.lstat()
          if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.geteuid():
            raise RuntimeError("unexpected dashboard relay endpoint")
          path.unlink()
        sock = self.cleanup.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
        sock.setblocking(False)
        sock.bind(str(path))
        self.cleanup.callback(path.unlink, missing_ok=True)
        path.chmod(0o600)
        self.sockets[name] = sock
    except BaseException:
      self.cleanup.close()
      raise
    return self

  def __exit__(self, *exc):
    self.cleanup.close()

  def snapshot(self):
    for name, sock in self.sockets.items():
      # A dead/stalled consumer must never replay a backlog as live.
      for _ in range(32):
        try:
          data = sock.recv(4097)
        except BlockingIOError:
          break
        try:
          value = json.loads(data) if len(data) <= 4096 else None
        except (ValueError, UnicodeError, RecursionError):
          value = None
        if not (isinstance(value, dict) and value.get("source") == name and type(value.get("v")) is int and
                value["v"] == 1 and type(value.get("at_us")) is int and isinstance(value.get("fields"), dict)):
          self.packets.pop(name, None)
          continue
        stamp = value["at_us"]
        now = self.clock()
        if stamp > now or now - stamp >= RELAY_MAX_AGE_US:
          continue
        if stamp > self.last_stamp.get(name, 0):
          self.packets[name] = value
          self.last_stamp[name] = stamp
      else:
        self.packets.pop(name, None)
    return combine(self.packets.get("advisor"), self.packets.get("screen"), self.clock())
