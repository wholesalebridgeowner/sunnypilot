# Ram Comma/Panda diagnostic sidecar — v0 read-only design

**Status: source and offline tests; NOT installed on vehicle, NOT hardware-tested.**

## Read-only remote connectivity audit — November 17, 2026
A one-time diagnostic probe was run via the already configured Railway/Comma JWT connection, using only supported Athena read methods (`getVersion`, `getGithubUsername`, `getSshAuthorizedKeys`) and the authenticated device listing. No command execution or installer invocation occurred.

- Comma API device type: `three` (consistent with logs reporting `tici`). Earlier conversational descriptions of the device as Comma 2 should not be used to choose binaries.
- Reported Sunnypilot: `0.10.1`, `staging-tici`, commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`.
- Device was online at inspection, with the last Athena ping about 62 seconds old.
- GitHub SSH username and authorized keys: **not configured**.
- Comma Prime: **inactive**.
- The Comma API permits read-only diagnostic RPCs and explicit upload requests, but is not an arbitrary remote shell installer. No authorized remote-install action was available through the connected tools.

**Installation prerequisite:** When parked, enable SSH in device Settings and configure an authorized GitHub account/SSH public key for an operator-controlled computer. Local-network SSH is a supported path; Comma Prime provides a remote SSH proxy if subscribed. The operator must establish a properly authenticated shell to the parked device, verify its actual runtime and installed code, and only then perform a minimal, reversible passive diagnostic pilot.

Do not treat API access to log uploads as permission to run arbitrary commands, or claim software is installed merely because an upload request succeeds.


## Purpose
Develop a safe path from one-off Q-log review toward higher-fidelity engineering observability. The connected Comma device already receives Panda CAN packets and runs `loggerd`, which saves raw/decimated `can` and `sendcan` streams in R-/Q-logs. We should use the existing paths instead of creating a competing CAN device driver.

The opt-in script `device_telemetry.py` subscribes (never publishes) to existing cereal messaging services, retaining selected Ram DT identifiers from `opendbc/safety/modes/chrysler.h`. It also captures car state, requested and applied steering, model lane-change state, cruise status, and lead estimates. It emits local NDJSON files under an explicitly selected directory, rotates at 16 MiB each and retains eight by default. No upload, camera, microphone or vehicle command capability is implemented.

## Features delivered
- `device_telemetry.py`: passive subscriptions to `can`, `sendcan`, `carState`, `carControl`, `carOutput`, `controlsState`, `selfdriveState`, `onroadEvents`, `longitudinalPlan`, `radarState`, `drivingModelData`.
- CAN filter for Ram DT EPS feedback (`0x31`), LKAS transmit (`0xA6`), ESP/brake (`0x83`), vehicle speed (`0x79`), accelerator input (`0x9D`), factory ACC status (`0x99`), cruise buttons (`0xB1`), LKAS HUD (`0xFA`), center stack (`0x28A`). These labels identify message categories, NOT decoded sensor units. Actual bus is retained, so addresses are not conflated across buses.
- Optional `--all-can-ids` for more complete capture. This can increase CPU and storage overhead and exposes more private signals; use only in a controlled, measured session.
- Filtered scalar diagnostic streams sampled at 10 Hz by default, bounded capture duration 20 minutes by default (maximum 120), explicit opt-in run and no persistence across reboots.
- Local `summarize_can_capture.py` produces frame counts, observed rates and maximum gaps by bus/address without exporting raw frames.
- Unit tests that run without device or car.

## Hardware validation and deployment steps (not yet performed)
1. Confirm device identity and OS environment; original conversation referenced a Comma 2 but the recorded build reported `tici`. Obtain version, storage, processor load and permissions while the vehicle is parked.
2. Review Python/cereal messaging API compatibility with actual deployed image; user device was recorded on Sunnypilot `staging-tici`, version 0.10.1, modified working tree. This repository branch may not match its runtime.
3. Measure baseline CPU, loggerd frame drops, storage availability and onroad system health. Do not install anything that competes with critical control processes.
4. Copy and run sidecar manually in an isolated 1–2-minute **parked** session at 1–5 Hz. Confirm it subscribes without affecting existing message timing and generates expected fields. Monitor `loggerd` and `pandad` stability.
5. If acceptable, use a defined, supervised longer recording. Do not auto-start, run unattended or stream CAN off-device until reviewed and authorized separately.
6. Extract data from the device using a private channel **only with explicit permission**. Never commit raw logs, VIN, GPS, or dongle IDs to a public repo. Confirm file permissions and storage retention.

## Suggested offline run (only after parked hardware validation)
```bash
python3 ram1500-development/device_telemetry.py \
  --output-dir /data/media/0/ram-telemetry \
  --minutes 2 --sample-hz 2 --max-mb 8 --keep-files 2

python3 ram1500-development/summarize_can_capture.py \
  /data/media/0/ram-telemetry/ram-telemetry-*.jsonl \
  --output /data/media/0/ram-telemetry-summary.json
```

## Next diagnostics improvements
- Build DBC-backed offline decoding and compare raw-frame interpretations to `carState.steeringTorqueEps`, `carState.vEgo`, `carOutput.actuatorsOutput.torqueOutputCan`.
- Correlate factory ACC cruise-button and gap-selection traffic with observed distance and acceleration.
- Identify all transitions of `drivingModelData.meta.laneChangeState`; measure steering rate and lateral acceleration during approach, transition, and recentering.
- Enhance log collection to retrieve R-logs from targeted historical routes in addition to recent 72-hour data. Make cloud retrieval bounded and per-route, never move sensitive raw data into public GitHub.
- Only after extensive replay and independent safety validation consider read-only recommended settings, then separately reviewed control code. Preserve Panda/OEM safety limits, driver monitoring and controls handback.

## Limitations and guardrails
This is a **diagnostic recorder**. It neither enables longitudinal control nor changes factory ACC, EPS, steering torque, calibration, model weights, or safety behavior. Direct steering/braking modification is outside this PR. Raw CAN is powerful evidence, but source-code message definitions, firmware differences, ECU gating and sample drops must be verified before conclusions. An accepted Comma upload request does not establish that a device file was actually transferred.
