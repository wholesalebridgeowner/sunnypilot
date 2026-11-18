# Factory parallel-parking passive R-log capture — ready-for-data checklist

**Updated November 18, 2026. This is an experiment plan, not a claim the maneuver has been recorded.** The owner confirms this Ram has operational factory automatic parallel parking and intends to use it this morning.

## One supervised normal OEM maneuver

Driver follows factory guidance in a suitable legal parking area, remains in control and ready to intervene. Do not interact with engineering software while operating the vehicle. The Comma's normal `loggerd` records R-logs; no custom on-road subscriber, CAN injection, EPS calibration changes, or firmware reflash.

After parking is complete, ask for the *approximate local clock time* and whether the maneuver completed, aborted, or required driver intervention. The owner does not need to repeat the maneuver or perform anything unusual for our tools.

## Retrieval: as soon as completed logs are finalized

1. Use approximate reported local time, plus Comma route/segment timestamp metadata, to identify the segment spanning the maneuver. Verify zone and offset and do not assume the device log's monotonic clock is UTC.
2. Prefer authorized **read-only** SSH retrieval of completed R-log segments to a private Windows folder after the truck is safely parked. Claude has access through the trusted Windows machine. Do not pull a currently open/truncated R-log, trigger a reboot, or interfere with logging.
3. If SSH is unavailable, use the existing authorized Railway `comma-log-sync` pipeline and verify that the relevant `rlog.zst` object actually arrived in private S3. Athena `uploadFilesToUrls` requests do not guarantee instant delivery. Existing collector polls recent routes and is bounded per run.
4. Keep raw R-logs, GPS, VIN, device identity, exact street locations and CAN bytes in private storage. Only publish aggregated/anonymous results.

## Offline comparison tools already committed

- `park_assist_can_audit.py`: CAN source bus/ID rates and byte-change fractions across normal and OEM phases; **does not decode commands**.
- `park_assist_phase_windows.py`: selects non-overlapping phases **within the same chronologically ordered R-log session** using `--normal START:END` and `--oem-parking START:END`. The seconds start from the **first CAN-bearing message of the supplied input files**, not the original route start or wall-clock.
- `park_assist_motion_context.py`: summarizes vehicle speed, EPS feedback, steering wheel angle change, `carControl.latActive` and the *Comma* native steering command within each phase. It uses the same first-CAN anchor as the CAN comparison. The `factoryParking` label is **driver-annotated**; the script does not independently prove that an OEM park-assist mode is active.
- `test_park_assist_phase_windows.py` and `test_park_assist_motion_context.py`: synthetic temporal boundary and zero-speed-motion tests.

The input schema must match the device's recorded Sunnypilot build. The current known build is `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, but always check the new route's `initData.gitCommit` before decoding. For the existing private Python/Railway analyzer, reuse a verified pinned `cereal/log.capnp` and byte-identical `car.capnp`.

Example command shapes on a trusted **offline** computer (sample ranges and paths are placeholders, not the driver's actual maneuver):

```bash
python ram1500-development/park_assist_phase_windows.py \
  --schema /private/schema/cereal/log.capnp \
  --logs /private/route/12/rlog.zst /private/route/13/rlog.zst \
  --normal 10:40 --oem-parking 70:120 \
  --out /private/analysis/parking-can-summary.json

python ram1500-development/park_assist_motion_context.py \
  --schema /private/schema/cereal/log.capnp \
  --logs /private/route/12/rlog.zst /private/route/13/rlog.zst \
  --normal 10:40 --oem-parking 70:120 \
  --out /private/analysis/parking-motion-context.json
```

First confirm the selected ranges on the private recorded route. The two analysis windows should contain **comparable low-speed, low-driver-intervention** operation when possible. Better yet, include multiple ordinary driving / parking phases on the same route. If OEM park assist disengages or the recording is incomplete, flag those facts instead of treating the entire window as active parking.

## Specific questions the first analysis can answer

- Was substantial **steering-wheel angle movement observed at reported stopped speeds** during the annotated OEM maneuver?
- Was Comma lateral control *inactive* and Comma LKAS command **zero**, while the OEM parking mechanism steered? That would provide useful evidence of distinct control ownership, but not a complete authorization map.
- Which CAN buses and IDs were observed during both phases, and which showed unusual frequency or byte-change behavior?
- Did EPS motor feedback and steering wheel motion follow distinct phases? Are there obvious differences across activation, motion and return to manual control?

## What it cannot yet establish

CAN ID changes alone do not identify a parking actuator command, specific requesting ECU, frame semantics, or authorization gate. EPS torque and steering angles can also change because the driver or other factory systems are steering. A timed association is not permission to replay captured OEM frames, bypass Panda safety, or enable Comma low-speed steering. Any later actual steering-control candidate requires separate DBC/OEM evidence, replay tests, fault analysis, and independent safety validation.

**Do not claim the factory parking capture exists until the user confirms completing it and the specific R-log data is verified.**
