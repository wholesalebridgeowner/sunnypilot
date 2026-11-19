#!/usr/bin/env python3
"""Incremental, read-only route metrics for the recorded Comma 3 build.

No CAN writes, settings writes, device SSH or upload instructions. The caller
must decode logs with the exact installed-build Cap'n Proto schema.
"""
from collections import Counter
import math

SCHEMA_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"

def get(o, *names):
    for name in names:
        try:
            o = o[name] if isinstance(o, dict) else getattr(o, name)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
    return o

def number(value):
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value))

def percentile(values, frac):
    if not values:
        return None
    values = sorted(values)
    i = (len(values) - 1) * frac
    lower = int(i)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] * (upper - i) + values[upper] * (i - lower), 4)

class RouteMetrics:
    def __init__(self):
        self.counts = Counter()
        self.events = Counter()
        self.event_episodes = Counter()
        self.last_event = {}
        self.long = Counter()
        self.lat = Counter()
        self.car_params = {}
        self.first = None
        self.last = None
        self.last_car = None
        self.last_accel = None
        self.active_lane = None
        self.lanes = []
        self.headway = []
        self.jerk = []
        self.lateral_rates = []
        self.steering_saturated_samples = 0
        self.steering_activity_samples = 0

    def ingest(self, kind, t, data):
        """Process one message in log-monotonic order, including boundaries."""
        if not number(t):
            return
        if self.last is not None and t < self.last - 0.5:
            raise ValueError("non-monotonic route: inspect route ordering/schema")
        self.first = t if self.first is None else min(self.first, t)
        self.last = t if self.last is None else max(self.last, t)
        self.counts[kind] += 1
        if kind == "carState":
            self.last_car = {"t": t,
                 "speed": get(data, "vEgo"),
                 "accel": get(data, "aEgo"),
                 "cruise": get(data, "cruiseState", "enabled"),
                 "gas": get(data, "gasPressed"),
                 "brake": get(data, "brakePressed")}
            a = self.last_car["accel"]
            if number(a):
                if self.last_accel is not None:
                    delta = t - self.last_accel[0]
                    if 0.03 <= delta <= 0.3:
                        j = (a - self.last_accel[1]) / delta
                        if number(j):
                            self.jerk.append(abs(j))
                self.last_accel = (t, a)
            rate = get(data, "steeringRateDeg")
            if number(rate):
                self.lateral_rates.append(abs(rate))
                if self.active_lane is not None:
                    self.active_lane["peakSteeringRate"] = max(
                        self.active_lane["peakSteeringRate"], abs(rate))
                    self.active_lane["steeringSamples"] += 1
        elif kind == "carControl":
            self.long[str(get(data, "longActive"))] += 1
            self.lat[str(get(data, "latActive"))] += 1
        elif kind == "carParams":
            self.car_params["openpilotLongitudinalControl"] = get(data, "openpilotLongitudinalControl")
            self.car_params["pcmCruise"] = get(data, "pcmCruise")
        elif kind == "radarState":
            lead = get(data, "leadOne")
            car = self.last_car
            d = get(lead, "dRel")
            if (get(lead, "status") is True and car is not None
                    and 0 <= t - car["t"] <= .35
                    and number(d) and d > 0
                    and number(car["speed"]) and car["speed"] > 2
                    and car["cruise"] is True
                    and car["gas"] is not True and car["brake"] is not True):
                gap = d / car["speed"]
                if number(gap) and 0 <= gap <= 20:
                    self.headway.append(gap)
        elif kind == "drivingModelData":
            state = str(get(data, "meta", "laneChangeState"))
            direction = str(get(data, "meta", "laneChangeDirection"))
            if state == "laneChangeStarting":
                if self.active_lane is None:
                    self.active_lane = {"start": t, "direction": direction,
                                        "peakSteeringRate": 0.0, "steeringSamples": 0}
            elif self.active_lane is not None:
                duration = t - self.active_lane["start"]
                if 0 < duration <= 30 and self.active_lane["steeringSamples"]:
                    self.lanes.append({"duration": duration,
                                       "direction": self.active_lane["direction"],
                                       "peakSteeringRate": self.active_lane["peakSteeringRate"]})
                self.active_lane = None
        elif kind == "onroadEvents":
            events = get(data, "onroadEvents") or data
            if isinstance(events, (str, bytes)):
                events = [events]
            for e in events:
                name = str(e if isinstance(e, str) else get(e, "name"))
                if name == "None":
                    continue
                self.events[name] += 1
                prev = self.last_event.get(name)
                if prev is None or t - prev > 1:
                    self.event_episodes[name] += 1
                self.last_event[name] = t
        elif kind == "controlsState":
            lateral = get(data, "lateralControlState")
            try:
                which = lateral.which()
                state = getattr(lateral, which)
            except (AttributeError, TypeError, ValueError):
                state = None
            if get(state, "saturated") is True:
                self.steering_saturated_samples += 1
            if get(state, "active") is True:
                self.steering_activity_samples += 1

    def report(self):
        durations = [a["duration"] for a in self.lanes]
        lane_rates = [a["peakSteeringRate"] for a in self.lanes]
        return {
            "schemaCommit": SCHEMA_COMMIT,
            "durationSeconds": round(self.last - self.first, 1) if self.first is not None else None,
            "messageCounts": dict(self.counts),
            "coverage": {
                "carStateSamples": self.counts["carState"],
                "controlsStateSamples": self.counts["controlsState"],
                "radarStateSamples": self.counts["radarState"],
                "laneModelSamples": self.counts["drivingModelData"],
            },
            "cruiseOwnership": {
                "carParams": self.car_params,
                "longActiveSamples": dict(self.long),
                "latActiveSamples": dict(self.lat),
                "caution": "Factory ACC may control braking/throttle; model plan does not prove actuation",
            },
            "laneChange": {
                "completeStartingWindows": len(durations),
                "medianStartingSeconds": percentile(durations, .5),
                "p90StartingSeconds": percentile(durations, .9),
                "p90PeakSteeringRateDegPerSecond": percentile(lane_rates, .9),
            },
            "leadHeadway": {
                "eligibleCruiseSamples": len(self.headway),
                "medianSeconds": percentile(self.headway, .5),
                "p10Seconds": percentile(self.headway, .1),
                "p90Seconds": percentile(self.headway, .9),
                "caution": "May be model-derived lead, not factory ACC gap target",
            },
            "comfort": {
                "observedAccelerationJerkSamples": len(self.jerk),
                "p90AbsJerkMps3": percentile(self.jerk, .9),
                "caution": "Unfiltered acceleration differencing is noisy; trend-only",
            },
            "steering": {
                "lateralSaturationStateSamples": self.steering_saturated_samples,
                "lateralActiveSamples": self.steering_activity_samples,
                "p90AbsSteeringRateDegPerSecond": percentile(self.lateral_rates, .9),
                "steerSaturatedEventEpisodes": self.event_episodes["steerSaturated"],
            },
            "eventEpisodes": dict(self.event_episodes),
            "limitations": [
                "Counts of samples are not independent failure episodes",
                "Drive routes, speed, weather and traffic must be matched before causal conclusions",
                "A trend report is not controller-release evidence",
            ],
        }

def summarize_log_messages(messages):
    acc = RouteMetrics()
    for msg in messages:
        kind = str(msg.which())
        data = getattr(msg, kind)
        acc.ingest(kind, int(msg.logMonoTime) / 1e9,
                   list(data) if kind == "onroadEvents" else data)
    return acc.report()

def compare(current, previous):
    """Conservative observational comparison, not an auto-tuner."""
    if not previous:
        return {"baselineAvailable": False, "metrics": {}}
    specs = [
        ("laneChange", "medianStartingSeconds", 2),
        ("laneChange", "p90PeakSteeringRateDegPerSecond", 2),
        ("leadHeadway", "medianSeconds", 20),
        ("comfort", "p90AbsJerkMps3", 100),
        ("steering", "steerSaturatedEventEpisodes", 1),
    ]
    result = {}
    for section, name, max_rel_change in specs:
        a, b = current.get(section, {}).get(name), previous.get(section, {}).get(name)
        if number(a) and number(b):
            result[f"{section}.{name}"] = {
                "previous": b, "current": a, "delta": round(a - b, 4),
                "largeDifference": bool(b and abs((a-b)/b) > max_rel_change),
            }
    return {"baselineAvailable": True, "metrics": result,
            "interpretation": "Observational route-to-route comparison; do not infer improvement without matched conditions and validation."}
