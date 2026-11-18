"""Read-only factory parking event episodes from pinned RAM EPS status and car streams.

Consumes only scalar observations from already-recorded R-logs. The factory EPS
AUTO_PARK_HAS_CONTROL_2 flag defines episodes; speed/steering/Comma signals
are joined to the LAST known at-or-before observations, within 150 ms.
No CAN transmission, actuator decoding, GPS, identifiers or frame payloads.
"""
from __future__ import annotations

from math import isfinite

MAX_STATE_AGE_S = .15
NEAR_STOP_SPEED_MS = .3


def _number(v):
    if isinstance(v, bool) or not isinstance(v, (float, int)):
        return None
    return float(v) if isfinite(v) else None


class ParkEpisodes:
    def __init__(self):
        self.latest = {}
        self.active = None
        self.last_flag = None
        self.completed = []
        self.eps_frames = 0
        self.discarded_out_of_order = 0
        self.last_t = None

    def update(self, time_s, stream, *, speed=None, angle=None, lat_active=None,
               comma_torque=None, driver_pressed=None, gear=None, park_flag=None):
        """Ordered scalar stream samples. Returns nothing; summarized on finish()."""
        t = _number(time_s)
        if t is None or t < 0:
            raise ValueError("invalid time")
        if self.last_t is not None and t < self.last_t - 1e-6:
            self.discarded_out_of_order += 1
            return
        self.last_t = max(self.last_t or t, t)
        if stream == "carState":
            self.latest["carState"] = (t, _number(speed), _number(angle),
                                       driver_pressed if isinstance(driver_pressed, bool) else None,
                                       gear if isinstance(gear, str) else None)
            if self.active is not None:
                state=self.active
                state["carStateSamples"] += 1
                if self._fresh("carControl",t):
                    state["freshCommaLatSamples"] += 1
                    if self.latest["carControl"][1] is True:
                        state["commaLatActiveTrue"] += 1
                    elif self.latest["carControl"][1] is False:
                        state["commaLatActiveFalse"] += 1
                if self._fresh("carOutput",t):
                    v=self.latest["carOutput"][1]
                    if v is not None:
                        state["freshCommaTorqueSamples"] += 1
                        if abs(v) > 0:
                            state["nonzeroCommaTorqueSamples"] += 1
                if speed is not None and angle is not None and -0.1 <= speed <= NEAR_STOP_SPEED_MS:
                    state["nearStopSamples"] += 1
                    state["stoppedAngleMin"] = min(state["stoppedAngleMin"],angle)
                    state["stoppedAngleMax"] = max(state["stoppedAngleMax"],angle)
                    if driver_pressed is True:
                        state["steeringPressedNearStopSamples"] += 1
        elif stream == "carControl":
            self.latest["carControl"] = (t, lat_active if isinstance(lat_active, bool) else None)
        elif stream == "carOutput":
            self.latest["carOutput"] = (t, _number(comma_torque))
        elif stream == "eps":
            if not isinstance(park_flag,bool):
                return
            self.eps_frames += 1
            if park_flag and self.active is None:
                self.active = {"startSeconds":t,"endSeconds":t,"epsFlagTrueFrames":0,
                               "carStateSamples":0,"nearStopSamples":0,
                               "stoppedAngleMin":float("inf"),"stoppedAngleMax":float("-inf"),
                               "freshCommaLatSamples":0,"commaLatActiveTrue":0,"commaLatActiveFalse":0,
                               "freshCommaTorqueSamples":0,"nonzeroCommaTorqueSamples":0,
                               "steeringPressedNearStopSamples":0}
            if self.active is not None and park_flag:
                self.active["epsFlagTrueFrames"] += 1
                self.active["endSeconds"] = t
            elif self.active is not None and not park_flag:
                self._close(t,completed=True)
            self.last_flag = park_flag
        else:
            raise ValueError("unknown scalar stream")

    def _fresh(self,stream,t):
        rec=self.latest.get(stream)
        return rec is not None and 0 <= t-rec[0] <= MAX_STATE_AGE_S

    def _close(self,t,*,completed):
        episode=self.active
        if episode is None:
            return
        lo,hi=episode.pop("stoppedAngleMin"),episode.pop("stoppedAngleMax")
        episode["nearStopAngleRangeDeg"]=round(hi-lo,2) if hi>=lo else None
        episode["durationSeconds"]=round(t-episode["startSeconds"],3)
        episode["closedWithFalseFlag"]=completed
        # No individual absolute route timestamp or precise time of frame is output.
        episode.pop("startSeconds")
        episode.pop("endSeconds")
        self.completed.append(episode)
        self.active=None

    def finish(self):
        if self.active is not None:
            self._close(self.last_t,completed=False)
        return {
            "epsFrameObservations":self.eps_frames,
            "factoryParkEpisodes":self.completed,
            "outOfOrderObservations":self.discarded_out_of_order,
            "limitations":[
                "The factory EPS flag, not guessed CAN IDs, determines active parking episodes.",
                "Near-stop is <=0.3 m/s; it does not prove an exact physical wheel speed of zero.",
                "Comma latActive and CAN torque are as-of preceding samples with a 150ms age cap.",
                "SteeringPressed is an EPS driver-column torque flag, not direct measurement of human hand contact.",
                "Near-stop steering-angle range spans all qualifying samples within the episode, not necessarily one continuous stop.",
                "This read-only audit does not identify factory PAM steering request frames or authorize third-party control.",
            ]
        }
