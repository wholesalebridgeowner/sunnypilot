# Ram 1500 Drive Advisor: design

**Date:** 2026-11-20. **Status:** design approved by the owner in conversation; nothing built yet.
**Installed build this design targets:** sunnypilot 0.10.1, `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` (staging-tici).

## Goal

Make sunnypilot drive the owner's 2019+ Ram 1500 (5th gen) better by learning from his own drives:

1. Record diagnostics on every drive.
2. During a drive, suggest one steering setting to try. The change applies only after the owner pulls over, puts the truck in Park and approves it, and it lasts for that drive only.
3. After the last drive of the day, review all of the day's drives and suggest at most one change to keep, which the owner approves.

The owner's goals, in his words: fewer steering warnings, steadier lane centering, better curves, smoother lane changes, and smoother braking with less hard braking.

## Owner decisions

| Question | Decision |
|---|---|
| What may change | Built-in sunnypilot settings only. No modified sunnypilot build. |
| Connection on the road | The Comma joins the owner's phone hotspot. |
| Live alert | Phone notification **with sound**. The Approve button works only while the Comma reports Park. |
| Life of a live change | A trial for that drive. It reverts when the drive ends; the nightly review recommends whether to keep it. |
| Nightly report timing | About one hour after the truck parks for the night and the drive's logs finish uploading. |
| Where analysis runs | Railway. The Comma records, summarizes, and applies approved settings. |

## Hard constraints

- No change to `/data/openpilot`, Panda safety, steering limits (`STEER_MAX`), CAN traffic, or the factory ACC.
- The Comma agent only listens to existing messages and writes the settings on its allowlist.
- On this truck, Comma does not control speed or braking (`openpilotLongitudinalControl` is false; the factory ACC brakes). **Braking is measured and reported only.** Braking advice is limited to things the owner adjusts himself, such as the factory following-distance setting.
- No video, GPS, VIN, dongle ID or route names leave the Comma. Raw recordings stay on the Comma and in the owner's existing private log bucket.

## Settings the system may change

These are sunnypilot's own settings at the installed build:

| Setting | What it does | When it takes effect | Allowed use |
|---|---|---|---|
| `LagdToggle` | On: steering delay is learned live. Off: a fixed delay is used. | Within seconds (re-read while driving) | Live trial and nightly |
| `LagdToggleDelay` | Fixed software delay added to the car's base actuator delay when `LagdToggle` is off | Within seconds | Live trial and nightly |
| `NeuralNetworkLateralControl` | Neural-network steering model (a Ram 1500 5th-gen model ships with the build) | Next drive | Nightly only |

**Trial delay range.** A trial sets `LagdToggle` off and sets `LagdToggleDelay` so that the total delay (base actuator delay from `CarParams` plus software delay) is within **±0.10 s of the truck's current learned delay**, and always within **0.10 to 0.50 s total**. Steps are 0.05 s. These limits are constants in the Comma agent and cannot be changed remotely.

## Architecture

```
Comma (in truck)                          Railway (private)                 Owner's phone
----------------                          -----------------                 -------------
agent: record ─ summarize ── per-minute ──> ingest ─> live analyzer ──────> ntfy alert (sound)
       │            summaries (HTTPS)               │                         │ Approve (Park only)
       │                                            v                         v
       └─ apply/restore <── signed one-time ── approval service <──────── approval tap
          (allowlist,        approval                │
           Park only)                                v
                                         nightly reviewer <── existing log bucket (R/Q-logs)
                                                │
                                                └──> ntfy report + Approve ──> applied before next drive
```

### Comma agent (one program in `/data/ram-diag/`)

- **Start.** One line added to `/data/continue.sh`, before `exec ./launch_openpilot.sh`, starts the agent in the background at low priority (`nice +10`). The line is written so a missing or failing agent can never stop sunnypilot from launching. The original file is backed up and restored by the uninstaller.
- **Listen.** It waits until the Comma is onroad and sunnypilot's own programs are up, then checks every stream it needs has fewer than 15 listeners (a 16th listener evicts all listeners of that stream) and subscribes once per boot. It refuses to listen if any stream is full. It reuses the recorder that passed the 2026-11-20 parked test (12.4% of one core, 17.7 MB).
- **Record.** Writes the bounded local recording, as in the parked test, with motion now allowed.
- **Summarize.** Every 60 s it builds a small summary (see Metrics) and sends it over HTTPS to Railway with the Comma's own key. If there is no connection, summaries wait in a bounded queue and send later; the oldest are dropped first.
- **Apply and restore.** The only part that writes settings. Rules below.
- **Limits.** It stops recording above 15% of one core for two 10-second windows, or above 150 MB private memory. The apply-and-restore part keeps running when recording stops.

### Railway

- **Ingest:** accepts summaries signed with the Comma's key and stores them privately.
- **Live analyzer:** applies simple threshold rules to recent minutes (see Suggestion rules). It sends at most one suggestion at a time and at most two per drive.
- **Notifier:** posts to a new private ntfy topic for the truck. A live suggestion is sent with sound. The Approve and Decline buttons call the approval service.
- **Approval service:** issues a one-time approval for a specific suggestion that expires after 10 minutes. The Comma fetches it and verifies it with the shared key.
- **Nightly reviewer:** starts about 60 minutes after a drive ends and its logs have uploaded through the existing log bridge. It reviews every drive since the last report, compares trial-setting drives with comparable normal drives, and sends a report with at most one suggested change to keep. An approved nightly change is applied by the agent while parked, before the next drive.

## Rules the Comma enforces itself

1. Only settings on the allowlist above, and only values within the fixed ranges.
2. Only while parked: gear is Park, speed is 0, sunnypilot is not engaged. Checked immediately before writing.
3. Only with a valid one-time approval for that exact change, issued in the last 10 minutes.
4. One change at a time; at most two live trials per drive.
5. Before writing, it saves the previous values to a journal file. It restores them when the drive ends (ignition off). If the Comma restarts with an open journal, restoring is the first thing the agent does.
6. If the owner changes a trial setting himself on the Comma screen, the agent stops the trial and does not overwrite his choice.
7. It never publishes messages, sends CAN, or modifies `/data/openpilot`.

## Metrics (per-minute summary)

All are counts or numbers; none identify the driver, the place or the vehicle.

- Steering warnings: `steerSaturated` alert episodes; time with steering command at its limit.
- Lane centering: side-to-side weaving (steering-direction reversals and steering rate on straight road).
- Curves: gap between requested and achieved curvature (`controlsState.desiredCurvature` vs `curvature`), split by curve size and speed.
- Lane changes: count, duration, peak steering rate.
- Braking (report only): hard-braking events (`aEgo` below -3 m/s²), with whether a lead car was present and how close.
- Context: speed range, minutes engaged, driver steering overrides, current settings.

## Suggestion rules (first version)

Simple thresholds, tuned during rollout step 1 against drives already in the owner's private bucket:

- Frequent weaving on straight road: suggest a shorter delay trial (-0.05 s).
- Late tracking in curves: suggest a longer delay trial (+0.05 s).
- Repeated steering warnings in curves with neural steering off: nightly suggestion to try neural-network steering on the next drive.
- Hard-braking patterns: report only, with plain advice such as a longer following-distance setting.

A suggestion needs at least 5 qualifying minutes of engaged driving. The nightly reviewer recommends keeping a trial only if it improved the targeted metric without worsening steering warnings on comparable driving.

## Failure handling

| Failure | Behavior |
|---|---|
| No hotspot or Railway down | Recording continues; summaries queue; no suggestions; nothing changes. |
| Approval expired or invalid | The agent ignores it; nothing changes. |
| Agent crashes | sunnypilot is unaffected. Any trial setting is restored the next time the agent starts. |
| Stream listener limit reached | The agent does not listen that boot; the drive is still logged by sunnypilot as usual. |
| Owner changes a setting during a trial | The trial ends without overwriting the owner's change. |

## Security and privacy

- The Comma authenticates to Railway with its own key stored only on the Comma (`/data/ram-diag`, owner-only permissions) and in Railway's private variables. Never in git.
- The ntfy topic is new and secret, like the topics the owner already uses.
- Public repository content contains no addresses, host names, user names, topics or Railway IDs.

## Rollout

Each step needs the owner's go-ahead and must pass its check before the next one starts.

1. **Offline:** unit tests for every rule above; run the analyzer on stored drives. *Pass:* all tests pass; suggestion rules produce sensible results on known drives.
2. **Parked test with the owner present:** install the agent, apply a delay trial in Park, then restore it. *Pass:* the setting changes only after approval, restores correctly, and sunnypilot starts normally after a restart.
3. **Drives, recording only:** no suggestions. *Pass:* processor and memory use under the limits while driving; no new fault alerts; summaries arrive.
4. **Nightly report only.** *Pass:* reports arrive on time and match the drives.
5. **Nightly suggestions with approval.** *Pass:* an approved change applies before the next drive and is visible in the next report.
6. **Live suggestions while driving.** *Pass:* the alert sounds, Approve works only in Park, and the trial reverts at drive end.

## Out of scope

- Changing factory ACC behavior, braking or speed.
- Custom tuning values or a modified sunnypilot build.
- Changes to the Comma's on-screen interface.
- Other vehicles.
