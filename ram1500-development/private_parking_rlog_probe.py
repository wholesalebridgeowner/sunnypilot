#!/usr/bin/env python3
"""One-shot, private OEM parking R-log analysis for an existing Railway S3 bucket.

Read-only: no vehicle connection, CAN publishing, upload or device settings.
Only anonymized 5-second car-state summaries and CAN ID frequency changes are
printed; GPS, VIN, identifying route keys, raw CAN bytes and source paths stay private.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json
import os
import tempfile

from private_route_probe import select_keys, reader_from_cereal

TARGET_SEGMENTS = (11, 12)
ROUTE_PREFIX = "000003dc"
BIN_SECONDS = 5
# The owner reported factory park assist activation near route second 720,
# based on a route beginning approximately 11:20 UTC. These ranges are
# candidates, not verified OEM operating-mode labels. The candidate window
# stops before observed speeds rise above ~5 mph, when the 2021 factory
# ParkSense active steering guidance ordinarily deactivates.
BASELINE_SECONDS = (660, 680)
PARK_CANDIDATE_SECONDS = (685, 705)


def valid_number(x):
    try:
        import math
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None



def eps_auto_park_has_control(frame):
    """RAM DT EPS_2 @ CAN 0x31, DBC AUTO_PARK_HAS_CONTROL_2 51|1@0+.

    Chrysler RAM DT generator maps Stellantis EPS_2 address 544 to 49.
    A one-bit Motorola signal at DBC start bit 51 occupies byte 6, bit 3.
    Only reads CAN data; does not interpret OEM steering commands.
    """
    try:
        if int(frame.src) != 0 or int(frame.address) != 0x31:
            return None
        data = bytes(frame.dat)
        if len(data) < 7:
            return None
        return bool((data[6] >> 3) & 1)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None

class Summary:
    def __init__(self):
        self.bins = defaultdict(lambda: {"speed": [], "angle": [], "eps": [], "requested": [],
                                         "driverSteering": 0, "latActive": Counter(), "gear": Counter(),
                                         "stoppedAngles": [], "column": [], "autoParkControl": Counter(),
                                         "autoParkTransitions": Counter()})
        self.frame_counts = {"baseline": Counter(), "parkCandidate": Counter()}
        self.frame_changed = {"baseline": Counter(), "parkCandidate": Counter()}
        self.frame_previous = {"baseline": {}, "parkCandidate": {}}
        self.events = Counter()
        self.segment_counts = Counter()
        self._last_auto_park = None

    def observe(self, segment: int, local_sec: float, event):
        sec = segment * 60 + local_sec
        if sec < 0 or sec >= 86400:
            return
        name = event.which()
        self.segment_counts[(segment, name)] += 1
        idx = int(sec // BIN_SECONDS)
        state = self.bins[idx]
        if name == "carState":
            obj = event.carState
            for name_key, attr in (("speed", "vEgo"), ("angle", "steeringAngleDeg"), ("eps", "steeringTorqueEps")):
                v = valid_number(getattr(obj, attr, None))
                if v is not None:
                    state[name_key].append(v)
            speed = valid_number(getattr(obj, "vEgo", None))
            angle = valid_number(getattr(obj, "steeringAngleDeg", None))
            if speed is not None and angle is not None and speed <= .3:
                state["stoppedAngles"].append(angle)
            driver_column = valid_number(getattr(obj, "steeringTorque", None))
            if driver_column is not None:
                state["column"].append(driver_column)
            state["driverSteering"] += bool(obj.steeringPressed)
            state["gear"][str(obj.gearShifter)] += 1
        elif name == "carControl":
            state["latActive"][str(event.carControl.latActive)] += 1
        elif name == "carOutput":
            v = valid_number(event.carOutput.actuatorsOutput.torqueOutputCan)
            if v is not None:
                state["requested"].append(v)
        elif name in ("can", "sendcan"):
            phase = ("baseline" if BASELINE_SECONDS[0] <= sec < BASELINE_SECONDS[1] else
                     "parkCandidate" if PARK_CANDIDATE_SECONDS[0] <= sec < PARK_CANDIDATE_SECONDS[1] else None)
            for frame in getattr(event, name):
                if name == "can":
                    flag = eps_auto_park_has_control(frame)
                    if flag is not None:
                        state["autoParkControl"]["true" if flag else "false"] += 1
                        if self._last_auto_park is not None and flag != self._last_auto_park:
                            state["autoParkTransitions"]["enabled" if flag else "disabled"] += 1
                        self._last_auto_park = flag
                if phase:
                    key = (name, int(frame.src), int(frame.address))
                    raw = bytes(frame.dat)
                    self.frame_counts[phase][key] += 1
                    if key in self.frame_previous[phase] and self.frame_previous[phase][key] != raw:
                        self.frame_changed[phase][key] += 1
                    self.frame_previous[phase][key] = raw
        elif name == "onroadEvents":
            for x in event.onroadEvents:
                key = str(x.name)
                if key in ("canError", "controlsMismatch", "steerSaturated"):
                    self.events[key] += 1

    def result(self):
        bins=[]
        for idx, row in sorted(self.bins.items()):
            if not row["speed"] and not row["angle"]:
                continue
            speed=row["speed"]
            angle=row["angle"]
            eps=row["eps"]
            req=row["requested"]
            stopped_angles=row["stoppedAngles"]
            column=row["column"]
            bins.append({
                "relativeWindowSeconds": [idx*BIN_SECONDS, (idx+1)*BIN_SECONDS],
                "carStateSamples": len(speed),
                "speedMinMps": round(min(speed), 3) if speed else None,
                "speedMaxMps": round(max(speed), 3) if speed else None,
                "stoppedSamplesLe0p3Mps": sum(x <= .3 for x in speed),
                "steeringAngleRangeDeg": round(max(angle)-min(angle), 2) if angle else None,
                "steeringAngleNetChangeDeg": round(angle[-1]-angle[0], 2) if angle else None,
                "stoppedSteeringAngleRangeDeg": round(max(stopped_angles)-min(stopped_angles),2) if stopped_angles else None,
                "stoppedSteeringAngleNetChangeDeg": round(stopped_angles[-1]-stopped_angles[0],2) if stopped_angles else None,
                "columnTorqueAbsMaxNative": round(max(map(abs,column)),2) if column else None,
                "epsMotorFeedbackAbsMaxRaw": round(max(map(abs,eps)), 2) if eps else None,
                "commaNativeCommandAbsMax": round(max(map(abs,req)), 2) if req else None,
                "commaLateralActiveSamples": dict(row["latActive"]),
                "driverSteeringPressedSamples": row["driverSteering"],
                "gearCounts": dict(row["gear"]),
                "epsAutoParkHasControl2Samples": dict(row["autoParkControl"]),
                "epsAutoParkControlTransitions": dict(row["autoParkTransitions"]),
            })
        candidates=[]
        normal=self.frame_counts["baseline"]
        parking=self.frame_counts["parkCandidate"]
        for key in set(normal)|set(parking):
            n,p=normal[key],parking[key]
            if max(n,p) < 10:
                continue
            hz_normal=n/(BASELINE_SECONDS[1]-BASELINE_SECONDS[0])
            hz_park=p/(PARK_CANDIDATE_SECONDS[1]-PARK_CANDIDATE_SECONDS[0])
            changed_normal=self.frame_changed["baseline"][key]/max(n-1,1) if n>1 else None
            changed_park=self.frame_changed["parkCandidate"][key]/max(p-1,1) if p>1 else None
            candidates.append({"stream":key[0], "bus":key[1], "idHex":f"0x{key[2]:X}",
                               "baselineFrames":n,"parkingCandidateFrames":p,
                               "baselineHz":round(hz_normal,1),"parkingCandidateHz":round(hz_park,1),
                               "baselineChangeFraction":round(changed_normal,3) if changed_normal is not None else None,
                               "parkingChangeFraction":round(changed_park,3) if changed_park is not None else None})
        candidates.sort(key=lambda x:(x["baselineFrames"] != 0,
                                      -(x["parkingCandidateFrames"] / (x["baselineFrames"]+1)),
                                      x["bus"],x["idHex"]))
        return {
            "status":"unverified_oem_parking_annotation",
            "bins":bins,
            "canCandidateChanges":candidates[:45],
            "canFrameSummary":{"baseline":sum(normal.values()),"parkingCandidate":sum(parking.values())},
            "criticalEventCounts":dict(self.events),
            "messageCountsBySegmentAndService":[{"segment":k[0],"service":k[1],"count":v}
                                                 for k,v in sorted(self.segment_counts.items())],
            "scope":{"segments":list(TARGET_SEGMENTS),"binSeconds":BIN_SECONDS,
                     "baselineCandidateSeconds":list(BASELINE_SECONDS),
                     "parkingCandidateSeconds":list(PARK_CANDIDATE_SECONDS)},
            "limitations":[
                "Owner time indicates approximate parking activation, not independently verified vehicle mode.",
                "Route-relative seconds are reconstructed using 60-second log segments; validate alignment.",
                "Changes in CAN frequency/payload variability do not identify steering command ownership.",
                "AUTO_PARK_HAS_CONTROL_2 is a named EPS_2 DBC bit, not proof of the entire factory command or third-party authorization protocol.",
                "A stationary steering wheel-angle change with Comma lateral control off would support a distinct factory path, not authorize replay.",
                "This is aggregate-only observational analysis; no physical actuation or steering software changed."
            ]
        }


def observe_segment_events(summary, segment, messages):
    """Normalize a segment to its first CAN-bearing event, not stale initData."""
    start = None
    for msg in messages:
        ts = int(msg.logMonoTime)
        if start is None:
            if msg.which() != "can":
                continue
            start = ts
        if ts < start:
            continue
        summary.observe(segment, (ts-start)/1e9, msg)
    if start is None:
        raise RuntimeError("selected R-log contains no CAN anchor")


def main():
    import boto3
    from botocore.config import Config

    e = os.environ
    required=("S3_BUCKET","S3_ENDPOINT","S3_ACCESS_KEY_ID","S3_SECRET_ACCESS_KEY")
    if any(not e.get(k) for k in required):
        raise RuntimeError("Missing private bucket variable references")
    s3=boto3.client("s3",endpoint_url=e["S3_ENDPOINT"],aws_access_key_id=e["S3_ACCESS_KEY_ID"],
                    aws_secret_access_key=e["S3_SECRET_ACCESS_KEY"],region_name=e.get("S3_REGION","auto"),
                    config=Config(s3={"addressing_style":"virtual"}))
    paginator=s3.get_paginator("list_objects_v2")
    objects=(o for page in paginator.paginate(Bucket=e["S3_BUCKET"],Prefix="logs/")
             for o in page.get("Contents",()))
    keys=select_keys(objects,route_prefix=ROUTE_PREFIX,segments=TARGET_SEGMENTS)
    reader=reader_from_cereal(Path(os.environ.get("PINNED_CEREAL_LOG_SCHEMA","/opt/sunnypilot/cereal/log.capnp")))
    data=Summary()
    with tempfile.TemporaryDirectory(prefix="park-private-") as temp:
        for segment,key in zip(TARGET_SEGMENTS, keys):
            dest=Path(temp)/f"{segment}.zst"
            s3.download_file(e["S3_BUCKET"],key,str(dest))
            if dest.stat().st_size > 30_000_000:
                raise RuntimeError("Rlog exceeds fixed private analysis size limit")
            observe_segment_events(data, segment, reader(dest))
    print("PARKING_PASSIVE_AGGREGATES "+json.dumps(data.result(),separators=(",",":"),allow_nan=False),flush=True)

if __name__=="__main__":
    main()
