"""Optional local dashboard snapshot from the advisor's already-decoded envelopes."""
import json
import math
import os
from pathlib import Path
import socket
import stat


MAX_AGE_NS = {"carState": 100_000_000, "carControl": 100_000_000,
              "controlsState": 100_000_000, "drivingModelData": 500_000_000}
LANE_STATES = {"off", "preLaneChange", "laneChangeStarting", "laneChangeFinishing"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _field(stream, msg):
    if stream == "carState":
        speed = msg.vEgo
        if msg.canValid is not True or not _number(speed) or speed < 0:
            return None
        cruise = getattr(msg, "cruiseState", None)
        value = getattr(cruise, "speed", None)
        enabled = getattr(cruise, "enabled", None) is True and _number(value) and 0 < value * 3.6 < 254.5
        return [speed, value if enabled else None]
    if stream == "carControl":
        return [msg.latActive] if type(msg.latActive) is bool else None
    if stream == "controlsState":
        lateral = msg.lateralControlState
        if lateral.which() == "torqueState" and lateral.torqueState.active is True:
            saturated = lateral.torqueState.saturated
            return [saturated] if type(saturated) is bool else None
        return [None]
    if stream == "drivingModelData":
        state = str(msg.meta.laneChangeState)
        return [state] if state in LANE_STATES else None
    return None


class DashboardRelay:
    def __init__(self, directory=None):
        self.uid = os.geteuid()
        self.directory = Path(directory or f"/dev/shm/rampilot-dash-{self.uid}")

    def send(self, samples, now, started):
        try:
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return
        try:
            parent = os.fstat(descriptor)
            if parent.st_uid != self.uid or stat.S_IMODE(parent.st_mode) != 0o700:
                return
            target = os.stat("advisor.sock", dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISSOCK(target.st_mode) or target.st_uid != self.uid:
                return
            data = self._encode(samples, now, started)
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
                sender.setblocking(False)
                # Keep the verified directory pinned while addressing its socket.
                sender.sendto(data, f"/proc/self/fd/{descriptor}/advisor.sock")
        except (OSError, ValueError):
            pass  # An absent or backlogged display may drop any number of samples.
        finally:
            os.close(descriptor)

    @staticmethod
    def _encode(samples, now, started):
        # Only this drain's observations are eligible. There is no cache or retry queue.
        at_ns = int(now * 1_000_000_000)
        fields = {}
        for stream, (stamp, msg) in samples.items():
            if type(stamp) is not int or stamp <= 0 or stamp < started * 1_000_000_000:
                continue
            if not 0 <= at_ns - stamp <= MAX_AGE_NS[stream]:
                continue
            try:
                values = _field(stream, msg)
            except Exception:
                continue  # Unsupported schema or malformed optional field in this stream.
            if values is not None:
                fields[stream] = [stamp // 1000, *values]
        payload = {"v": 1, "source": "advisor", "at_us": at_ns // 1000, "fields": fields}
        return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("ascii")
