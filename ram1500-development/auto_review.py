#!/usr/bin/env python3
"""Incremental, read-only route metrics for the recorded Comma 3 build.

No CAN writes, settings writes, device SSH or upload instructions. The caller
must decode logs with the exact installed-build Cap'n Proto schema.
"""
from collections import Counter
import math

SCHEMA_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
FACTORY_ACC_QUALITY = "factory_acc_pedal_free_can_valid_source_pinned_segment_isolated_latest_radar_per_carstate_qlog_v3"
HEADWAY_QUALITY = "factory_acc_pedal_free_can_valid_source_pinned_segment_isolated_equal_time_radar_conflict_guarded_qlog_v4"
LANE_QUALITY = "model_direction_consistent_start_to_finish_active_healthy_segment_isolated_qlog_v4"
UNSEGMENTED_QUALITY = "segment_boundaries_unverified_not_for_comparison"

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
    if lower == upper:
        return round(values[lower], 4)
    return round(values[lower] * (upper - i) + values[upper] * (i - lower), 4)

class SegmentBoundary:
    """Non-identifying, in-memory divider between verified log segments.

    No route key, timestamp or serialized vehicle observation is attached.
    Only metrics consumers explicitly opting in to boundary emission see it.
    """


class RouteMetrics:
    def __init__(self):
        self.counts = Counter()
        self.segments_seen = 0
        self.events = Counter()
        self.event_episodes = Counter()
        self.last_onroad_events = set()
        self.long = Counter()
        self.lat = Counter()
        self.car_params = {}
        self.first = None
        self.last = None
        self.last_car = None
        self.last_accel = None
        self.last_factory_accel = None
        self.factory_acc_jerk = []
        self.long_source_conflict = False
        self.active_lane = None
        self.last_lat = None
        self.lane_rejections = Counter()
        self.lanes = []
        self.headway = []
        self.headway_ambiguous_samples = 0
        self.jerk = []
        self.lateral_rates = []
        self.steering_saturated_samples = 0
        self.steering_activity_samples = 0

    def segment_boundary(self):
        """Never join radar, derivatives or model phases across Q-log files.

        The route ordering check remains intact: a backward segment clock
        still raises rather than being excused by this reset.
        """
        self._flush_pending_headway()
        self.segments_seen += 1
        if self.active_lane is not None:
            self.lane_rejections["segment_boundary_incomplete"] += 1
            self.active_lane = None
        self.last_car = None
        self.last_lat = None
        self.last_accel = None
        self.last_factory_accel = None

    def _flush_pending_headway(self):
        """Commit at most one past-state radar pairing per carState record.

        The newest eligible radar observation supersedes older lead estimates
        before the carState frame is finalized, avoiding high-rate radar
        duplicating one low-rate speed sample in observed headway percentiles.
        """
        if self.last_car is not None:
            candidate = self.last_car.get("headwayCandidate")
            if number(candidate):
                self.headway.append(candidate)

    def _fresh_lateral(self, t):
        return (self.last_lat is not None
                and self.last_lat["active"] is True
                and 0 <= t - self.last_lat["t"] <= .15)

    def _fresh_healthy_car(self, t):
        return (self.last_car is not None
                and self.last_car["healthy"] is True
                and 0 <= t - self.last_car["t"] <= .15)

    def _lane_observation_ok(self, t):
        return self._fresh_lateral(t) and self._fresh_healthy_car(t)

    def _factory_acc_eligible(self):
        """Conservative observed-motion gate; never attributes actuation to Comma."""
        car = self.last_car
        return (not self.long_source_conflict
                and self.car_params.get("openpilotLongitudinalControl") is False
                and self.car_params.get("pcmCruise") is True
                and car is not None and car["canValid"] is True
                and car["cruise"] is True
                and car["gas"] is False and car["brake"] is False
                and number(car["speed"]) and car["speed"] > 2)

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
            self._flush_pending_headway()
            self.last_car = {"t": t,
                 "headwayCandidate": None, "headwayRadarTime": None,
                 "headwayRadarEvidence": None, "headwayAmbiguous": False,
                 "speed": get(data, "vEgo"),
                 "accel": get(data, "aEgo"),
                 "cruise": get(data, "cruiseState", "enabled"),
                 "canValid": get(data, "canValid"),
                 "gas": get(data, "gasPressed"),
                 "brake": get(data, "brakePressed"),
                 "healthy": (get(data, "canValid") is True
                             and get(data, "steerFaultTemporary") is False
                             and get(data, "steerFaultPermanent") is False
                             and get(data, "steeringPressed") is False)}
            if self.active_lane is not None and not self._lane_observation_ok(t):
                self.active_lane["invalid"] = True
            a = self.last_car["accel"]
            if number(a):
                if self.last_accel is not None:
                    delta = t - self.last_accel[0]
                    if 0.03 <= delta <= 0.3:
                        j = (a - self.last_accel[1]) / delta
                        if number(j):
                            self.jerk.append(abs(j))
                self.last_accel = (t, a)
            # Separate strictly factory-ACC-conditional observed jerk from
            # the legacy *all driving* trend. A dropout resets the derivative;
            # never bridge a pedal press, cruise-off period or invalid CAN.
            if self._factory_acc_eligible() and number(a):
                if self.last_factory_accel is not None:
                    delta = t - self.last_factory_accel[0]
                    if 0.03 <= delta <= 0.3:
                        value = abs((a - self.last_factory_accel[1]) / delta)
                        if number(value):
                            self.factory_acc_jerk.append(value)
                self.last_factory_accel = (t, a)
            else:
                self.last_factory_accel = None
            rate = get(data, "steeringRateDeg")
            if number(rate):
                self.lateral_rates.append(abs(rate))
                if self.active_lane is not None and not self.active_lane["invalid"]:
                    self.active_lane["peakSteeringRate"] = max(
                        self.active_lane["peakSteeringRate"], abs(rate))
                    self.active_lane["steeringSamples"] += 1
        elif kind == "carControl":
            self.long[str(get(data, "longActive"))] += 1
            active = get(data, "latActive")
            self.lat[str(active)] += 1
            if self.active_lane is not None:
                # A transition, discontinuity or missing health evidence
                # anywhere in the model window permanently disqualifies it.
                if (active is not True or self.last_lat is None
                        or not 0 <= t - self.last_lat["t"] <= .15
                        or not self._fresh_healthy_car(t)):
                    self.active_lane["invalid"] = True
            self.last_lat = {"t": t, "active": active}
        elif kind == "carParams":
            incoming = {"openpilotLongitudinalControl": get(data, "openpilotLongitudinalControl"),
                        "pcmCruise": get(data, "pcmCruise")}
            if self.car_params and incoming != self.car_params:
                # Mixed source/ownership evidence invalidates the ENTIRE
                # route-level factory attribution, including earlier samples.
                self.long_source_conflict = True
            self.car_params = incoming
            if self.long_source_conflict:
                self.last_factory_accel = None
        elif kind == "radarState":
            car = self.last_car
            # Retain at most one sample per carState. Two disagreeing radar
            # observations at the same monotonic timestamp have no defensible
            # order: quarantine this vehicle-state pairing, even if a later
            # radar frame arrives. Identical repeated observations are benign.
            if (car is not None and self._factory_acc_eligible()
                    and 0 <= t - car["t"] <= .35
                    and not car["headwayAmbiguous"]
                    and (car["headwayRadarTime"] is None
                         or t >= car["headwayRadarTime"])):
                lead = get(data, "leadOne")
                status = get(lead, "status")
                d = get(lead, "dRel")
                evidence = (status if status is True or status is False else None,
                            d if status is True and number(d) else None)
                if t == car["headwayRadarTime"]:
                    if evidence != car["headwayRadarEvidence"]:
                        car["headwayAmbiguous"] = True
                        car["headwayCandidate"] = None
                        self.headway_ambiguous_samples += 1
                else:
                    gap = (d / car["speed"] if status is True
                           and number(d) and d > 0 else None)
                    car["headwayRadarTime"] = t
                    car["headwayRadarEvidence"] = evidence
                    car["headwayCandidate"] = (
                        gap if number(gap) and 0 <= gap <= 20 else None)
        elif kind == "drivingModelData":
            state = str(get(data, "meta", "laneChangeState"))
            direction = str(get(data, "meta", "laneChangeDirection"))
            if state == "laneChangeStarting":
                if self.active_lane is None:
                    self.active_lane = {"start": t, "direction": direction,
                                        "invalid": (direction not in ("left", "right")
                                                    or not self._lane_observation_ok(t)),
                                        "peakSteeringRate": 0.0, "steeringSamples": 0}
                elif (direction != self.active_lane["direction"]
                      or not self._lane_observation_ok(t)):
                    self.active_lane["invalid"] = True
            elif self.active_lane is not None:
                duration = t - self.active_lane["start"]
                if (state == "laneChangeFinishing"
                        and not self.active_lane["invalid"]
                        and direction == self.active_lane["direction"]
                        and self._lane_observation_ok(t)
                        and 0 < duration <= 30
                        and self.active_lane["steeringSamples"]):
                    self.lanes.append({"duration": duration,
                                       "direction": self.active_lane["direction"],
                                       "peakSteeringRate": self.active_lane["peakSteeringRate"]})
                else:
                    # This remains a sampled model-phase proxy, not physical
                    # lane crossing or confirmed trajectory completion.
                    reason = ("model_phase_interrupted" if state != "laneChangeFinishing"
                              else "model_direction_conflict"
                              if direction != self.active_lane["direction"]
                              else "missing_active_or_healthy_evidence")
                    self.lane_rejections[reason] += 1
                self.active_lane = None
        elif kind == "onroadEvents":
            events = get(data, "onroadEvents") or data
            if isinstance(events, (str, bytes)):
                events = [events]
            observed = set()
            for e in events:
                name = str(e if isinstance(e, str) else get(e, "name"))
                if name == "None":
                    continue
                observed.add(name)
            for name in observed:
                self.events[name] += 1
                if name not in self.last_onroad_events:
                    self.event_episodes[name] += 1
            # A published empty event set is necessary to establish clearance.
            # An approximately 1 Hz heartbeat is not a new alert episode.
            self.last_onroad_events = observed
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
        # An unfinished final model window must be visible as missing evidence.
        # Keep report() idempotent: do not mutate persistent rejection counts.
        lane_rejections = self.lane_rejections.copy()
        if self.active_lane is not None:
            lane_rejections["incomplete_at_route_end"] += 1
        durations = [a["duration"] for a in self.lanes]
        source_ok = (not self.long_source_conflict
                     and self.car_params.get("openpilotLongitudinalControl") is False
                     and self.car_params.get("pcmCruise") is True)
        # Later contradictory CarParams invalidates even earlier observations.
        pending_headway = (self.last_car.get("headwayCandidate")
                           if self.last_car is not None else None)
        qualified_headway = (
            self.headway + ([pending_headway] if number(pending_headway) else [])
            if source_ok else [])
        qualified_acc_jerk = self.factory_acc_jerk if source_ok else []
        lane_rates = [a["peakSteeringRate"] for a in self.lanes]
        return {
            "schemaCommit": SCHEMA_COMMIT,
            "durationSeconds": round(self.last - self.first, 1) if self.first is not None else None,
            "messageCounts": dict(self.counts),
            "coverage": {
                "segmentBoundariesSeen": self.segments_seen,
                "segmentIsolatedMetrics": self.segments_seen > 0,
                "carStateSamples": self.counts["carState"],
                "controlsStateSamples": self.counts["controlsState"],
                "radarStateSamples": self.counts["radarState"],
                "laneModelSamples": self.counts["drivingModelData"],
            },
            "cruiseOwnership": {
                "carParams": self.car_params,
                "factoryAccSourceConsistent": source_ok,
                "carParamsConflict": self.long_source_conflict,
                "longActiveSamples": dict(self.long),
                "latActiveSamples": dict(self.lat),
                "caution": "Factory ACC may control braking/throttle; model plan does not prove actuation",
            },
            "laneChange": {
                "qualification": LANE_QUALITY if self.segments_seen else UNSEGMENTED_QUALITY,
                "completeStartingWindows": len(durations),
                "medianStartingSeconds": percentile(durations, .5),
                "p90StartingSeconds": percentile(durations, .9),
                "p90PeakSteeringRateDegPerSecond": percentile(lane_rates, .9),
                "rejectedModelWindows": dict(lane_rejections),
                "caution": "Model start-to-finish with consistent direction only; sampled Comma lateral-active and CAN/EPS health required. Unfinished model windows are rejected. Physical lane crossing and full-resolution comfort are unverified.",
            },
            "leadHeadway": {
                "qualification": HEADWAY_QUALITY if self.segments_seen else UNSEGMENTED_QUALITY,
                "eligibleCruiseSamples": len(qualified_headway),
                "equalTimeRadarRejectedCarStateSamples": self.headway_ambiguous_samples,
                "medianSeconds": percentile(qualified_headway, .5),
                "p10Seconds": percentile(qualified_headway, .1),
                "p90Seconds": percentile(qualified_headway, .9),
                "caution": "One latest, past-carState-paired model lead per carState; contradictory equal-time radar evidence rejects that sample. Q-log observed headway, not OEM selected gap or confirmed radar authority",
            },
            "factoryAccObservedMotion": {
                "qualification": FACTORY_ACC_QUALITY if self.segments_seen else UNSEGMENTED_QUALITY,
                "eligibleJerkSamples": len(qualified_acc_jerk),
                "p90AbsObservedJerkMps3": percentile(qualified_acc_jerk, .9),
                "caution": "Sampled vehicle aEgo derivative under factory ACC conditions, not OEM throttle/brake command; no filtering, no causal smoothness claim",
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
                "Q-logs are sampled and do not resolve R-log lane timing, peak steering rates or actuator ownership",
                "Source-verified segment boundaries prevent cross-file lead, jerk and lane-phase joins; clock regressions still invalidate a recording",
                "Alert episode counts are observed absent-to-present transitions; missing event messages leave continuity unverified",
            ],
        }

def summarize_log_messages(messages):
    acc = RouteMetrics()
    for msg in messages:
        if isinstance(msg, SegmentBoundary):
            acc.segment_boundary()
            continue
        kind = str(msg.which())
        data = getattr(msg, kind)
        acc.ingest(kind, int(msg.logMonoTime) / 1e9,
                   list(data) if kind == "onroadEvents" else data)
    return acc.report()

def compare(current, previous):
    """Conservative observational comparison, not an auto-tuner."""
    if not previous:
        return {"baselineAvailable": False, "metrics": {}}
    # Require exact build and explicit source-verified segment provenance on
    # BOTH sides, not just a section label. A historic or manually assembled
    # report must not impersonate source-pinned route-comparison evidence.
    if (current.get("schemaCommit") != SCHEMA_COMMIT
            or previous.get("schemaCommit") != SCHEMA_COMMIT
            or current.get("coverage", {}).get("segmentIsolatedMetrics") is not True
            or previous.get("coverage", {}).get("segmentIsolatedMetrics") is not True):
        return {
            "baselineAvailable": True, "metrics": {},
            "comparisonSuppressed": "source_or_segment_provenance_unverified",
            "interpretation": "No numeric comparison without identical pinned source and verified segment isolation.",
        }
    # All-driving jerk and raw saturation-episode counts lack a matched,
    # independently verified exposure denominator. Never publish a +/- delta
    # that could be misread as a comfort or vehicle-safety improvement.
    specs = [
        ("laneChange", "medianStartingSeconds"),
        ("laneChange", "p90PeakSteeringRateDegPerSecond"),
        ("leadHeadway", "medianSeconds"),
        ("factoryAccObservedMotion", "p90AbsObservedJerkMps3"),
    ]
    result = {}
    for section, name in specs:
        if section in ("laneChange", "leadHeadway", "factoryAccObservedMotion"):
            marker = (LANE_QUALITY
                      if section == "laneChange" else FACTORY_ACC_QUALITY)
            if (current.get(section, {}).get("qualification") != marker
                    or previous.get(section, {}).get("qualification") != marker):
                continue  # Never compare old unqualified or differently gated values
            if (section != "laneChange"
                    and (current.get("cruiseOwnership", {}).get("factoryAccSourceConsistent") is not True
                         or previous.get("cruiseOwnership", {}).get("factoryAccSourceConsistent") is not True)):
                continue
        a, b = current.get(section, {}).get(name), previous.get(section, {}).get(name)
        if number(a) and number(b):
            result[f"{section}.{name}"] = {
                "previous": b, "current": a, "delta": round(a - b, 4),
            }
    return {"baselineAvailable": True, "metrics": result,
            "interpretation": "Observational route-to-route comparison; do not infer improvement without matched conditions and validation."}
