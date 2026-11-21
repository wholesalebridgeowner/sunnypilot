"""Schema v2 as an allow-list, for the privacy tests.

Every key a message may hold, and for every string the fixed tokens or the
shape it may have. `problems(event)` lists what a message holds beyond that:
a key that is not on the list, a string that is not a token, a hex id or a
version, a number that is not finite or so large that it could be a clock
time. A message body holds no place, no name and no time of day.

Test support only: never installed on the Comma, and the service does not
filter by it.
"""
from __future__ import annotations

import math
import re

MAX_NUMBER = 1e9                    # a clock time in seconds or milliseconds would exceed it
MEASURES = {"steering", "limit", "brake", "laneChange", "delay", "lane", "swings", "ay", "curve", "lag", "unknown"}
SETTING_KEYS = {"lagdToggle", "lagdToggleDelayS", "nnlc", "laneChangeTimer", "modelBundleId", "build"}

_HEX12 = re.compile(r"[0-9a-f]{12}")
_DRIVE_ID = re.compile(r"[0-9a-f]{32}|replay")
_EVENT_ID = re.compile(r"[0-9a-f]{12}-[0-9]{1,9}|replay-[0-9]{1,9}")
_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")
_RESTORE = re.compile(r"nothing_to_restore|deferred_until_drive_end|restored|"
                      r"owner_changed:(LagdToggle|LagdToggleDelay|NeuralNetworkLateralControl)"
                      r"(,(LagdToggle|LagdToggleDelay|NeuralNetworkLateralControl))*")
_STATUS = {"estimated", "unestimated", "invalid"}

NUMBER, FLAG, COUNT = "number", "flag", "count"


def _tokens(*names):
    return ("tokens", frozenset(names))


def _shape(pattern):
    return ("shape", pattern)


def _list_of(names):
    return ("list", frozenset(names))


_SETTINGS = {"lagdToggle": FLAG, "lagdToggleDelayS": NUMBER, "nnlc": FLAG, "laneChangeTimer": COUNT,
             "modelBundleId": ("shape", re.compile(r"[0-9a-f]{12}|default|unreadable")), "build": _shape(_HEX12)}
_DELAY = {"learnedS": NUMBER, "estimateS": NUMBER, "estimateStdS": NUMBER, "validBlocks": COUNT,
          "status": _tokens(*_STATUS), "cacheS": NUMBER, "baseActuatorS": NUMBER, "outOfBand": FLAG}
_PROBE = {"result": _tokens("inert", "effective", "unknown", "not_run"), "helperSha": _shape(_HEX12),
          "probeMs": COUNT}
_STALE = dict.fromkeys(("cc", "ctl", "co", "dmd", "rs", "ev"), NUMBER)
_HEALTH = {"gapN": COUNT, "modelGapN": COUNT, "cpuPct": NUMBER, "cpuPeakPct": NUMBER, "rssMb": NUMBER,
           "queue": COUNT, "dropped": COUNT, "tickErrors": COUNT, "failed": _list_of(MEASURES),
           "staleS": _STALE, "staleSteerS": {key: NUMBER for key in ("ctl", "co", "dmd", "ev")}}
_SUMMARY = {
    "w": COUNT, "tMonoS": NUMBER, "n": COUNT, "windowS": NUMBER, "unobservedS": NUMBER, "tickMaxS": NUMBER,
    **dict.fromkeys(("latOnS", "steeringS", "forcedZeroS", "fzFallbackS", "pressedS", "driverS", "laneChangeS",
                     "movingS", "cruiseS", "vMean", "vMeanSteering", "limitS", "limitDriverS", "limitLaneChangeS",
                     "limitNoneS", "limitUnexpectedS", "askS", "askNoneS", "warnS", "demandMaxNone"), NUMBER),
    **dict.fromkeys(("warnEvents", "brakeEpisodes", "lcStarts", "lcCompleted", "lcCancelled", "lcBlinkerOnly",
                     "lcHandsOn", "lcTruncated"), COUNT),
    "lane": {"n": COUNT, **dict.fromkeys(("sumSq", "scoredS", "validS", "eligibleS", "vMean", "widthMin", "widthMax",
                                          "probMin"), NUMBER)},
    "swings": {"n": COUNT, "straightS": NUMBER, "vMean": NUMBER},
    "ay": {"n": COUNT, "s": NUMBER, "sumSq": NUMBER, "planSumSq": NUMBER, "vMean": NUMBER},
    "curve": {"events": COUNT, **dict.fromkeys(("s", "belowSum", "aboveSum", "s04", "below04Sum", "above04Sum", "lowS",
                                                "lowLimitS", "lowBelowSum", "lowAboveSum"), NUMBER)},
    "lag": {"s": NUMBER, "n": COUNT, "syy": NUMBER, "sxy": "numbers", "sxx": "numbers"},
    "delay": {"valueS": NUMBER, "minS": NUMBER, "maxS": NUMBER, "status": _tokens(*_STATUS), "blocks": COUNT},
    "health": _HEALTH,
    "trimmed": _list_of({"lag", "curve", "ay", "swings", "lane"}),
}
DATA = {
    "agent_start": {"restore": _shape(_RESTORE)},
    "drive_start": {"resumed": FLAG, "settings": _SETTINGS, "delay": _DELAY,
                    "agent": {"version": _shape(_VERSION), "measures": _list_of(MEASURES), "applyEnabled": FLAG}},
    "drive_ready": {"delayProbe": _PROBE, "nnlcModel": _tokens("ram", "mock", "other"), "nnlcFuzzy": FLAG},
    "summary": _SUMMARY,
    "brake": {"peakDrop1s": NUMBER, "peakDrop2s": NUMBER, "who": _tokens("driver", "cruise", "other"),
              "tookOverFromCruise": FLAG, "speedMs": NUMBER, "leadGapS": NUMBER, "leadDistanceM": NUMBER},
    "lane_change": {"outcome": _tokens("completed", "cancelled"), "dir": _tokens("L", "R"), "handsOn": FLAG,
                    "pressedShare": NUMBER, "pushTorqueMax": NUMBER, "vStartMs": NUMBER, "crossed": FLAG,
                    "crossS": NUMBER, "startingS": NUMBER, "peakLatAccel": NUMBER,
                    "settleState": _tokens("settled", "not_settled", "no_crossing", "slow_stream",
                                           "not_measurable:drive_end", "not_measurable:data",
                                           "not_measurable:next_lane_change", "not_measurable:steering_off",
                                           "not_measurable:lines"),
                    "settleS": NUMBER, "overshootM": NUMBER},
    "limit_event": {"limitS": NUMBER, "vMeanMs": NUMBER, "vAtMaxMs": NUMBER, "demandMax": NUMBER,
                    "achievedAtMax": NUMBER, "achievedMean": NUMBER, "driverTorqueMean": COUNT, "epsOverCmd": NUMBER,
                    "warned": FLAG, "label": _tokens("sharp", "slow", "unexpected")},
    "settings_change": {"settings": _SETTINGS, "changed": _list_of(SETTING_KEYS)},
    "listening_disabled": {"reason": _tokens("subscriber_slots_full", "self_limit"), "cpuPct": NUMBER,
                           "rssMb": NUMBER},
    "drive_end": {"windows": COUNT, "listening": FLAG, "restore": _shape(_RESTORE), "failed": _list_of(MEASURES),
                  "totals": dict.fromkeys(("windowS", "unobservedS", "steeringS", "movingS"), NUMBER),
                  "settings": _SETTINGS, "delay": _DELAY, "delayProbe": _PROBE, "truncatedLaneChanges": COUNT},
}
ENVELOPE = ("v", "type", "driveId", "id", "data")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check(value, rule, where: str, found: list) -> None:
    if value is None:
        return                                      # null is "not known": allowed anywhere
    if isinstance(rule, dict):
        if not isinstance(value, dict):
            found.append(f"{where}: not an object")
            return
        for key, inner in value.items():
            if key not in rule:
                found.append(f"{where}.{key}: key not in the schema")
            else:
                _check(inner, rule[key], f"{where}.{key}", found)
    elif rule == FLAG:
        if not isinstance(value, bool):
            found.append(f"{where}: not a flag")
    elif rule in (NUMBER, COUNT):
        if not _is_number(value) or not math.isfinite(value) or abs(value) > MAX_NUMBER:
            found.append(f"{where}: not a plain number")
        elif rule == COUNT and not isinstance(value, int):
            found.append(f"{where}: not a whole number")
    elif rule == "numbers":
        if not isinstance(value, list):
            found.append(f"{where}: not a list")
        else:
            for i, inner in enumerate(value):
                _check(inner, NUMBER, f"{where}[{i}]", found)
                if inner is None:
                    found.append(f"{where}[{i}]: null in a list of numbers")
    else:
        kind, allowed = rule
        if kind == "list":
            if not isinstance(value, list) or not all(isinstance(item, str) and item in allowed for item in value):
                found.append(f"{where}: not a list of known names")
        elif not isinstance(value, str):
            found.append(f"{where}: not a string")
        elif kind == "tokens" and value not in allowed:
            found.append(f"{where}: string is not one of the fixed tokens")
        elif kind == "shape" and not allowed.fullmatch(value):
            found.append(f"{where}: string has not the allowed shape")


def problems(event) -> list:
    """What `event` holds beyond schema v2; an empty list when it holds nothing else."""
    found = []
    if not isinstance(event, dict):
        return ["not an object"]
    if tuple(sorted(event)) != tuple(sorted(ENVELOPE)):
        found.append("envelope: keys differ from v, type, driveId, id, data")
    if event.get("v") != 2 or isinstance(event.get("v"), bool):
        found.append("v: not 2")
    kind = event.get("type")
    if kind not in DATA:
        found.append("type: not a message type of this build")
        return found
    drive_id = event.get("driveId")
    if kind == "agent_start":
        if drive_id is not None:
            found.append("driveId: agent_start belongs to no drive")
    elif not isinstance(drive_id, str) or not _DRIVE_ID.fullmatch(drive_id):
        found.append("driveId: not 32 hex")
    if not isinstance(event.get("id"), str) or not _EVENT_ID.fullmatch(event["id"]):
        found.append("id: not <12 hex>-<number>")
    if not isinstance(event.get("data"), dict):
        found.append("data: not an object")
    else:
        _check(event["data"], DATA[kind], "data", found)
    return found
