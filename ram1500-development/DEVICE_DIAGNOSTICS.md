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

The opt-in script `device_telemetry.py` subscribes (never publishes) to existing cereal messaging services, retaining selected Ram DT identifiers from `opendbc/safety/modes/chrysler.h`. It also captures car state, requested and applied steering, model lane-change state, cruise status, and lead estimates. It emits local NDJSON files under an explicitly selected directory, rotates at 16 MiB each and retains eight by default. No upload, camera, microphone or vehicle command capability is implemented. It refuses to start if subscribing would evict existing subscribers, and stops when the vehicle moves unless explicitly allowed (see "Claude review of the sidecar").

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

## Suggested run (only after parked hardware validation)
Copy the two scripts to `/data/ram-diag/` — **not** into `/data/openpilot`, which would dirty the installed git tree
and can be cleaned by the updater. Run them against the device's own `cereal`:
```bash
cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py --check-slots
cd /data/openpilot && PYTHONPATH=/data/openpilot timeout 150 /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py \
  --output-dir /data/ram-telemetry --minutes 2 --sample-hz 2 --max-mb 8 --keep-files 2
/usr/local/venv/bin/python3 /data/ram-diag/summarize_can_capture.py /data/ram-telemetry/ram-telemetry-*.jsonl \
  --output /data/ram-telemetry/summary.json
```

## Claude review of the sidecar (2026-11-17)
Verified against the drive build `sunnypilot@1f7233cb` (what the Comma reports it runs):
- **Passive design confirmed.** Subscribes through cereal/msgq only; never opens the panda, never publishes, no `sendcan`.
- **CAN IDs correct.** All nine match `CHRYSLER_RAM_DT_ADDRS` in the drive build's `opendbc/safety/modes/chrysler.h:193-203`.
- **Schema compatible.** Every field the sidecar reads was exercised with real Cap'n Proto messages built from the pinned
  drive-build schema; all decode (enums as text, CAN filtered by ID). Nothing returned missing.
- **Risk found and guarded — msgq subscriber eviction.** msgq allows 15 subscribers per service (`msgq.h NUM_READERS`) and
  never frees a slot when a process exits; the 16th subscriber **evicts every subscriber of that service**
  (`msgq.cc msgq_init_subscriber`), including `card`/`controlsd`, which then drop messages while they resync. The sidecar now
  reads each service's reader count from the first 8 bytes of `/dev/shm/msgq_<service>` (read-only) and refuses to start if
  subscribing would evict anyone (`--check-slots` prints the counts). Rule regardless: **start it only while parked; never
  start or restart it while driving.**
- **Parked-only enforced.** Capture stops when `carState.vEgo > 0.3 m/s` unless `--allow-moving` (not part of the pilot).
- **Overhead measured, not assumed.** Runs at `nice +10`; writes a `sidecar_health` row every 10 s (own CPU % of one core,
  RSS, load average) plus `sidecar_start` / `sidecar_stop` rows with reader counts before/after and the stop reason.
- **Fixed:** the file-size cap counted `\n` as 1 byte but Windows wrote `\r\n` (test failed on Windows; harmless on device).
- Decode errors (including pycapnp `KjException`) are counted instead of ending the capture.

## Parked pilot plan v1 (Claude) — each gate needs Mark's explicit GO
Scope: one 2-minute capture, truck parked, nothing installed into openpilot, nothing that starts on its own.

| Gate | Who | Action | Reversible by |
|---|---|---|---|
| G1 | Mark, in the parked truck (ignition on; engine off, or engine running only outdoors) | Settings → Network → Advanced: turn **Enable SSH** on; **SSH Keys** → Add → `wholesalebridgeowner`. Note the Wi-Fi IP shown there. The truck must be on the same home Wi-Fi as the PC. | Same screen: remove keys, turn SSH off |
| G2 | Claude, read-only | `windows_preflight.ps1 -DeviceIP <ip>`; first SSH as `comma@<ip>` with host-key prompt, record fingerprint privately. Read-only commands only: `git --no-optional-locks rev-parse HEAD`, `git --no-optional-locks status --porcelain`, `git --no-optional-locks diff --stat` (identifies the dirty-tree changes that actually ran Nov 14), `python3 --version`, `cat /VERSION`, `df -h /data`, `free -m`, `uptime`, `top -bn1`, msgq reader counts. | Nothing written |
| G3 | Claude | `mkdir /data/ram-diag /data/ram-telemetry`, copy the two scripts, `--check-slots`, then the 2-minute run above with `timeout 150` as a hard stop, while snapshotting `top` and confirming `pandad`, `card`, `controlsd`, `loggerd` stay up. | `rm -rf /data/ram-diag /data/ram-telemetry` |
| G4 | Claude | Run `summarize_can_capture.py` on the device; copy back **only** `summary.json` and the health/stop rows to a private folder outside git. | Delete the private copy |

Pass criteria: stop reason "duration reached"; sidecar CPU ≤ 15 % of one core; reader counts after = before + 1 per
service (no eviction); no `commIssue`, `canError`, `controlsMismatch` or on-screen alert during the run; expected CAN IDs at
their nominal rates. **Abort** on any alert, any of those events, a failed slot check, or CPU above the limit.

Access note for G1: the Comma trusts **every** public key on the GitHub account it is given. `wholesalebridgeowner` currently
has two authentication keys (the "pop-os xps15" laptop and the DoodlePC key already on this PC), so both machines get a shell
on the truck's computer until SSH is turned off or the username removed. No new key is needed for this PC.

Not covered by this plan (separate approval each): capture while driving (`--allow-moving`), `--all-can-ids`, copying raw
CAN frames off the device, any change under `/data/openpilot`, autostart.

## G1–G2 results (2026-11-17, read-only; nothing written on the device)
G1 done by Mark (SSH on, `wholesalebridgeowner` keys loaded). G2 = two SSH sessions running read-only commands only
(`git --no-optional-locks`, `ps`, `df`, `free`, reading params and `/dev/shm` headers). Device identifiers are kept private.

| Item | Measured |
|---|---|
| Hardware / OS | Comma 3 (`tici`), AGNOS 12.8, Linux 4.9.103 aarch64, 8 cores, 3.6 GB RAM |
| Python | 3.12.3 at `/usr/bin/python3` (the sidecar needs ≥ 3.11 for `datetime.UTC`: OK) |
| Installed code | `sunnypilot/sunnypilot` `staging-tici` @ `1f7233cb` (v0.10.1, Oct 13 2025), **prebuilt**, working tree **clean** (`status --porcelain` and `diff` empty). No pending update. |
| Why logs said "dirty" | Branch `staging-tici` has **no upstream configured**; `system/version.py:49-53` returns dirty=True whenever `get_branch()` (`git rev-parse @{u}`, `common/git.py:22-23`) is empty. The Nov 14 drive ran exactly `1f7233cb` with no local edits. |
| State during check | Onroad (ignition on, parked); load average 5–6 on 8 cores; ~0.8 GB RAM available; CPU ~63 °C; `/data` 82 % used, 5.2 GB free |
| ADB | Enabled (`AdbEnabled=1`). Not needed for this work; recommend turning it off. |

msgq reader counts (onroad, 15 slots per service; the sidecar subscribes to the first eleven):

| can | sendcan | carState | carControl | carOutput | controlsState | selfdriveState | onroadEvents | longitudinalPlan | radarState | drivingModelData | pandaStates | modelV2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 2 | **14** | 10 | 4 | 5 | 8 | 3 | 4 | 5 | 1 | **15** | 7 |

Consequences for G3:
- `carState` has **one** free slot, so the sidecar can run **once per device boot**. A second run before a reboot would evict
  every `carState` subscriber, including controls. The preflight now blocks that.
- **Preflight bug found and fixed by this check:** the drive build keeps queues at `/dev/shm/<service>`, not
  `/dev/shm/msgq_<service>` (current upstream msgq). The first version of the preflight looked only at the upstream path,
  would have read "no queue file" for everything and called it safe. It now checks both layouts and `--check-slots`
  prints which file it read.
- `pandaStates` is already full (15/15), so any new `pandaStates` subscriber would evict all of them. The sidecar does not
  subscribe to it; keep it that way.
- Memory and disk headroom are adequate for a 2-minute, 8 MB-capped capture. CPU is already busy onroad, which is why the
  pass criterion caps the sidecar at 15 % of one core and it runs at `nice +10` below the real-time controls processes.

## G3 run 1 (2026-11-17, parked, onroad) — stopped at start by a sidecar bug; no impact on driving processes
- Pre-checks passed after one correction: the system `/usr/bin/python3` has no `capnp`; openpilot runs from
  **`/usr/local/venv/bin/python3`** (3.12.3). All run commands must use that interpreter.
- Scripts copied to `/data/ram-diag/` (mode 700), SHA-256 verified against the PR branch. Nothing under `/data/openpilot` touched.
- `--check-slots` on the device read the real queue files (`/dev/shm/<service>`): `carState` 14/15, nothing to evict.
- The capture then exited within its first poll: `KeyError` on `subs[sock]`. msgq's `Poller.poll()` returns **new**
  socket wrapper objects, so the original loop (present since the first version of this script; missed in review) could
  never find them. **Fixed:** poll as a wait, then drain every subscribed socket; regression test reproduces the device
  error against the old loop. `--check-slots` no longer requires `--output-dir`; an exception now records its reason.
- Watchdog result: `pandad`, `card`, `controlsd`, `selfdrived`, `loggerd` kept the **same PIDs** before/after. Reader
  counts rose by exactly 1 on each of the 11 services (no eviction). `carState` is now 15/15, so the preflight correctly
  blocks a second run until the Comma reboots.
- Retry needs: a Comma reboot while parked (clears `/dev/shm`), ignition on until it is onroad again, then the same commands:
```bash
cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py --check-slots
cd /data/openpilot && PYTHONPATH=/data/openpilot timeout 150 /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py \
  --output-dir /data/ram-telemetry --minutes 2 --sample-hz 2 --max-mb 8 --keep-files 2
```

## G3 run 2 + G4 (2026-11-17, parked in Park, ignition RUN, onroad, not engaged)
Comma rebooted over SSH (Mark's approval). It came back offroad because the truck's ignition signal had dropped (the Comma
stays powered from the harness); Mark cycled the ignition and it went onroad. Fresh onroad baseline: `carState` 14/15 readers,
so 14 is the normal single-session count. Capture: 2 min, 2 Hz scalar sampling, all frames of the nine Ram IDs.

| Criterion | Result |
|---|---|
| Stop reason | **Pass** — "duration reached", 120.0 s, 0 malformed records, exit 0 |
| Driving processes | **Pass** — `pandad`, `card`, `controlsd`, `selfdrived`, `loggerd` same PIDs before/after; stayed onroad |
| Subscriber eviction | **Pass** — every service +1 exactly (`carState` 14→15); nothing evicted |
| Fault events | **Pass** — only `wrongGear`, `seatbeltNotLatched`, `belowSteerSpeed` (expected parked, belt off); no `commIssue`, `canError`, `controlsMismatch`; no alert displayed; `canValid` true in all 238 carState samples |
| CAN integrity | **Pass** — table below |
| Sidecar CPU ≤ 15 % of one core | **Fail** — 21.6 % steady (≈ 2.7 % of the 8-core device). The live watchdog sampled the `timeout` wrapper instead of the Python process, so the breach was only visible in the sidecar's own health rows. |
| Sidecar memory | **Fail (trend)** — RSS rose linearly 29.6 → 53.9 MB over 120 s (~12 MB/min). Fine for 2 min; a 20-min run projects to ~270 MB with ~0.8 GB available. Must be understood and fixed before any longer capture. |

Observed CAN (frames in 120 s; `src` ≥ 128 = frames the panda transmitted, i.e. its own LKAS/HUD and harness forwarding):

| Bus | ID | Meaning | Rate | Max gap |
|---|---|---|---|---|
| 0 | 0x31 | EPS_2 (motor/column torque) | 100.1 Hz | 21 ms |
| 0 | 0x79, 0x83, 0x9D, 0xB1 | speed, brake, accelerator, cruise buttons | 50.0 Hz | 31 ms |
| 0 | 0x28A | center stack | 1.0 Hz | 1010 ms |
| 2 | 0x99, 0xA6 | factory DASM: ACC state, **factory LKAS command** | 50.1 Hz | 31 ms |
| 2 | 0xFA | factory DASM HUD | 16.7 Hz | 71 ms |
| 128 | 0xA6, 0xFA, 0x99 | sent to car bus: **openpilot's** LKAS command (50 Hz), its HUD (4 Hz), forwarded ACC state | 50 / 4 / 50 Hz | 30 / 252 / 31 ms |
| 130 | 0x31 … 0x28A | car-bus frames forwarded to the camera | same as bus 0 | — |
| sendcan 0 | 0xA6, 0xFA | openpilot TX requests | 50.0 / 4.0 Hz | 23 / 253 ms |

This matches the drive-build interface: the harness keeps the factory camera's 0xA6/0xFA off the car bus and openpilot
sends its own at 50 Hz (steering) and 4 Hz (HUD, `frame % 25`). EPS torque was 0 throughout (parked, lateral not active).

Kept on the device (owner-only): `/data/ram-telemetry/` (raw capture 9.2 MB in two files + `summary.json`), `/data/ram-diag/`.
Copied off: the summary and the sidecar's own rows only. Rollback: `rm -rf /data/ram-diag /data/ram-telemetry`.

Before any longer or driving capture (each needs its own approval):
1. Find and fix the RSS growth (reproduce off-device with real pinned-schema `can` messages; measure with `tracemalloc`).
2. Cut CAN parsing cost below the 15 % bar (pre-filter by address before building dicts, or decimate `can`/`sendcan`
   processing to what the analysis needs).
3. Watchdog must sample the Python process (`pgrep -f "python3 /data/ram-diag/device_telemetry.py"`) and kill the run
   live on CPU or RSS limits, not only report them afterwards.

## Next diagnostics improvements
- Build DBC-backed offline decoding and compare raw-frame interpretations to `carState.steeringTorqueEps`, `carState.vEgo`, `carOutput.actuatorsOutput.torqueOutputCan`.
- Correlate factory ACC cruise-button and gap-selection traffic with observed distance and acceleration.
- Identify all transitions of `drivingModelData.meta.laneChangeState`; measure steering rate and lateral acceleration during approach, transition, and recentering.
- Enhance log collection to retrieve R-logs from targeted historical routes in addition to recent 72-hour data. Make cloud retrieval bounded and per-route, never move sensitive raw data into public GitHub.
- Only after extensive replay and independent safety validation consider read-only recommended settings, then separately reviewed control code. Preserve Panda/OEM safety limits, driver monitoring and controls handback.

## Limitations and guardrails
This is a **diagnostic recorder**. It neither enables longitudinal control nor changes factory ACC, EPS, steering torque, calibration, model weights, or safety behavior. Direct steering/braking modification is outside this PR. Raw CAN is powerful evidence, but source-code message definitions, firmware differences, ECU gating and sample drops must be verified before conclusions. An accepted Comma upload request does not establish that a device file was actually transferred.

## Windows operator preflight (no changes to device)
The companion `windows_preflight.ps1` checks whether Git and OpenSSH are installed, whether there are public key files (without reading private keys), and optionally whether port 22 is reachable on an explicitly specified local Comma IP. Run only from your trusted Windows PC. The optional IP must be established independently on the device; the script performs no subnet scans.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\\ram1500-development\\windows_preflight.ps1
# Optional, only once actual parked Comma IPv4 address is verified:
powershell -NoProfile -ExecutionPolicy Bypass -File .\\ram1500-development\\windows_preflight.ps1 -DeviceIP 192.168.1.100
```

The example IP is illustrative, **not** the discovered address of your Comma. SSH keys and any remote installation need explicit operator authorization, host-key verification, and a parked vehicle. The diagnostic sidecar now restricts its local directory to mode 0700 and files to mode 0600. This is defense-in-depth; local device disk access and operating-system permissions still matter.

## Fixes after G3 run 2 (2026-11-17) — tested off-device only, NOT yet run on the Comma
- **Memory:** off-device the processing path grows Python's heap by only 1–8 KiB per 30 s of truck-shaped traffic, so the
  device's VmRSS climb (~12 MB/min) points at the shared 10 MB msgq rings (11 × 10 MB, verified sizes) being paged in as
  they are read: shared, already-resident memory that stops growing once each ring wraps. Health rows now split
  `rssAnonKiB` (private) from `rssShmemKiB` (shared) so the next device run confirms or refutes this.
- **CPU:** no Poller; sockets drained on a 50 ms cycle (removes per-message wake-ups, the part the off-device bench cannot
  measure); `carState` reads only `vEgo` at 100 Hz and builds the full record at the sample rate; CAN frames check the
  address first and skip per-frame labels (labels once in `sidecar_start`). Off-device bench
  (`bench_device_telemetry.py`, real pinned-schema messages): processing CPU −20 to −28 %, output −30 % (2.31 → 1.62 MB per
  30 s). The device figure needs a new parked run.
- **Self-enforced limits:** stops itself when its own CPU exceeds `--max-cpu-pct` (default 15 % of one core) in two
  consecutive 10 s windows, or private memory exceeds `--max-private-mb` (default 150).
