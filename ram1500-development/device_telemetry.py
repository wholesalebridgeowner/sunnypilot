#!/usr/bin/env python3
"""Read-only, opt-in Ram telemetry sidecar for a *parked* Comma device.

Subscribes to existing cereal messaging streams. Never publishes messages,
issues CAN commands, changes ECU parameters, or touches openpilot control code.
No automatic startup or upload. User must start explicitly after validation.

Example (after installing/reviewing on device, while parked):
  python3 ram1500-development/device_telemetry.py \
    --output-dir /data/media/0/ram-telemetry --minutes 20

Local NDJSON contains sensitive vehicle and trip telemetry: keep it private.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

# Ram DT identifiers as defined by pinned opendbc safety mode.
RAM_CAN_IDS = {
  0x31: "eps_motor_feedback",
  0x83: "brake_vehicle_state",
  0x79: "vehicle_speed",
  0x9D: "accelerator_input",
  0x99: "acc_cruise_state",
  0xA6: "lkas_command",
  0xB1: "cruise_buttons",
  0xFA: "lkas_hud",
  0x28A: "center_stack",
}
SERVICES = ("can", "sendcan", "carState", "carControl", "carOutput",
            "controlsState", "selfdriveState", "onroadEvents",
            "longitudinalPlan", "radarState", "drivingModelData")
# Q-log already handles complete trip history; sidecar focuses on key signals
# in a bounded diagnostic window without duplicating camera/video traffic.
FIELDS = {
 "carState": ("vEgo", "aEgo", "steeringAngleDeg", "steeringRateDeg",
              "steeringTorque", "steeringTorqueEps", "steeringPressed",
              "canValid", "canTimeout", "steerFaultTemporary",
              "steerFaultPermanent", "gasPressed", "brakePressed"),
 "carControl": ("enabled", "latActive", "longActive"),
 "selfdriveState": ("enabled", "active", "alertType"),
 "controlsState": ("desiredCurvature", "curvature", "longControlState"),
 "longitudinalPlan": ("aTarget", "hasLead", "shouldStop", "allowThrottle",
                       "allowBrake", "longitudinalPlanSource"),
 "drivingModelData": (),
}

def scalar(obj, name):
  try: value = getattr(obj, name)
  except (AttributeError, TypeError, ValueError): return None
  if value is None or isinstance(value, (bool, int, float, str)):
    return value
  return str(value) if name in ("longControlState", "longitudinalPlanSource") else None

def record_for_message(evt, service, raw_can=False):
  """Return low-overhead structured sample, or None for unneeded messages."""
  timestamp = int(evt.logMonoTime)
  payload = getattr(evt, service)
  row = {"monoTimeNs": timestamp, "service": service}
  if service in ("can", "sendcan"):
    packets = []
    for m in payload:
      address = int(m.address)
      if not raw_can and address not in RAM_CAN_IDS:
        continue
      # CAN frames only; do not decode bytes into guessed physical units.
      packets.append({"bus":int(m.src), "id":address,
                      "label":RAM_CAN_IDS.get(address,"other"),
                      "dataHex":bytes(m.dat).hex()})
    if not packets:return None
    row["packets"] = packets
  elif service == "onroadEvents":
    names = [str(x.name) for x in payload]
    if not names:return None
    row["events"] = names
  else:
    row.update({key:scalar(payload,key) for key in FIELDS.get(service,())})
    if service=="carState":
      cs=getattr(payload,"cruiseState",None)
      for key in ("enabled","available","standstill","speed"):
        row["cruise_"+key]=scalar(cs,key)
    elif service in ("carControl","carOutput"):
      act=getattr(payload,"actuators" if service=="carControl" else "actuatorsOutput",None)
      for key in ("torque","torqueOutputCan","accel"):
        row["actuator_"+key]=scalar(act,key)
    elif service=="radarState":
      lead=getattr(payload,"leadOne",None)
      for key in ("status","dRel","vRel","vLead","radar"):
        row["lead_"+key]=scalar(lead,key)
    elif service=="drivingModelData":
      meta=getattr(payload,"meta",None)
      for key in ("laneChangeState","laneChangeDirection"):
        val=getattr(meta,key,None)
        row[key]=str(val) if val is not None else None
  return row

class RotatingWriter:
  """Local-only bounded JSONL writer. Delete oldest files only in own directory."""
  def __init__(self,directory,max_bytes=16*1024*1024,keep=8):
    self.directory=Path(directory)
    if max_bytes < 4096 or keep < 1: raise ValueError("Invalid size/retention")
    self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    self.directory.chmod(0o700)  # private location for trip/CAN telemetry
    self.max_bytes=max_bytes
    self.keep=keep
    self.index=0
    self.f=None
    self.length=0
    self.rotate()
  def rotate(self):
    if self.f:self.f.close()
    name=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    self.index+=1
    path=self.directory / f"ram-telemetry-{name}-{self.index:04d}.jsonl"
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    self.f=os.fdopen(fd,"w",encoding="utf8")
    self.length=0
    files=sorted(self.directory.glob("ram-telemetry-*.jsonl"))
    for old in files[:-self.keep]:
      if old!=path:old.unlink()
  def write(self,row):
    line=json.dumps(row,separators=(",",":"),allow_nan=False)+"\n"
    size=len(line.encode())
    if size>self.max_bytes:
      raise ValueError("single event exceeds segment file cap")
    if self.length+size>self.max_bytes:self.rotate()
    self.f.write(line)
    self.length+=size
  def close(self):
    if self.f:self.f.close();self.f=None

def run(args):
  # Deliberately lazy device-only dependency: pure fixtures run on normal Python.
  import cereal.messaging as messaging
  from cereal import log as cereal_log
  writer=RotatingWriter(args.output_dir,args.max_mb*1024*1024,args.keep_files)
  poller=messaging.Poller()
  subs={}
  for service in SERVICES:
    subs[messaging.sub_sock(service,poller)]=service
  counts=Counter()
  last_sample={}
  end=time.monotonic()+args.minutes*60
  try:
    while time.monotonic()<end:
      for sock in poller.poll(100):
        service=subs[sock]
        raw=sock.receive(non_blocking=True)
        if raw is None:continue
        try:
          with cereal_log.Event.from_bytes(raw) as evt:
            interval_ns=int(1e9/args.sample_hz)
            if service not in ("can","sendcan","onroadEvents"):
              if evt.logMonoTime-last_sample.get(service,-interval_ns)<interval_ns:continue
              last_sample[service]=evt.logMonoTime
            item=record_for_message(evt,service,args.all_can_ids)
            if item is not None:
              writer.write(item)
              counts[service]+=1
        except (ValueError,AttributeError,TypeError) as ex:
          counts["decode_errors"]+=1
  finally:
    writer.close()
  return dict(counts)

def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output-dir",type=Path,required=True)
  parser.add_argument("--minutes",type=int,default=20)
  parser.add_argument("--sample-hz",type=int,default=10)
  parser.add_argument("--max-mb",type=int,default=16)
  parser.add_argument("--keep-files",type=int,default=8)
  parser.add_argument("--all-can-ids",action="store_true",
                      help="more sensitive/high-volume; record all observed CAN packets")
  args=parser.parse_args()
  if not (1<=args.minutes<=120 and 1<=args.sample_hz<=20 and 1<=args.max_mb<=64
          and 1<=args.keep_files<=20):
    parser.error("Capture limits exceeded")
  print(json.dumps({"completed":run(args)},sort_keys=True))

if __name__=="__main__":
  main()
