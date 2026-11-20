# Drive Advisor Runtime Implementation Plan (plans 2 to 4)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> The owner pre-approved this plan ("plan is approved when ready. implement it and go live immediately", 2026-11-20).
> Same executor and context as the core plan, so tasks name interfaces and tests; the code is in the task commits.

**Goal:** Run the Drive Advisor for real: a Comma agent that summarizes each drive minute and applies approved settings, and a Railway service that analyzes, notifies the owner through ntfy, takes approvals, and sends the nightly report.

**Architecture:** The agent (Comma, `/data/ram-diag/advisor/`) reuses the core modules and talks to the Railway service over HTTPS with requests signed by a shared key. The service keeps state in the existing private bucket under `advisor/`. Approvals are signed with the same key and re-validated by the Comma's applier and settings guard.

**Tech Stack:** Python 3.12 standard library on both sides (`urllib`, `http.server`, `threading`), cereal messaging and `Params` on the Comma, `boto3` on Railway (already used by the review job), ntfy.sh HTTP API.

---

## Protocol

- **Key:** 32 random bytes as hex. On the Comma: `/data/ram-diag/advisor/advisor.key` (mode 600). On Railway: variable `ADVISOR_KEY`. Never printed, never in git.
- **Comma to Railway auth:** headers `X-Advisor-Ts` (unix seconds) and `X-Advisor-Sig` = HMAC-SHA256(key, `METHOD\nPATH\nTS\nBODY`). Railway rejects a skew over 300 s or a bad signature. Every event has an `id`, and duplicate ids are ignored.
- `POST /v1/events` with body `{"type", "driveId", "id", "data"}`. Types:
  - `drive_start`, with `data` = `{"learnedDelayS", "baseActuatorDelayS", "settings"}`.
  - `summary`, with `data` = a minute summary plus `{"trialActive", "trialsThisDrive"}`.
  - `drive_end`.
  - `apply_result`, with `data` = `{"suggestionId", "status"}`.
- `GET /v1/approvals` (signed): signed approvals waiting for this Comma.
- `POST /a/<token>` Approve and `POST /d/<token>` Decline: called by the ntfy action buttons. The tokens are random and single-use.
- **Approval lifetime:** live 10 minutes, nightly 12 hours. The Comma retries a valid approval until the truck is parked or the approval expires.
- **Parked:** ignition off (not onroad) counts as parked. Onroad, parked means gear Park, speed under 0.1 m/s, and not engaged.

## File structure

| File | Side | Responsibility |
|---|---|---|
| `advisor/approval.py` (modify) | both | Lifetime per mode: live 600 s, nightly 43,200 s. |
| `advisor/wire.py` | both | Request signing and verification. |
| `advisor/device_params.py` | Comma | `Params` adapter; base and learned delay readers. |
| `advisor/agent.py` | Comma | Main loop: drive detection, listening, minute summaries, approvals, restore. |
| `advisor/uplink.py` | Comma | Background sender with a bounded queue, and approval polling. |
| `advisor/install_agent.py` | Comma | Pinned install, backup and uninstall of the `continue.sh` hook. |
| `advisor/service/store.py` | Railway | Drive and suggestion records in the bucket (an in-memory fake for tests). |
| `advisor/service/notify.py` | Railway | ntfy messages with Approve and Decline actions. |
| `advisor/service/app.py` | Railway | HTTP endpoints, live analyzer, nightly scheduler. |
| `advisor/service/Dockerfile` | Railway | Container for the service. |

## Tasks

- [ ] **R1: Approval lifetime per mode.** Tests: a nightly approval is valid at 11 h and expired at 13 h; a live approval is still expired at 601 s.
- [ ] **R2: wire.py.** Tests: a round trip verifies; a tampered body, wrong key, skew over 300 s, or missing header fails.
- [ ] **R3: device_params.py.** Tests with a fake `Params`:
  - learned delay comes from `LagdValueCache` only while `LagdToggle` is on, otherwise `None`, so live trials are refused;
  - base delay comes from `CarParams.steerActuatorDelay`;
  - typed get and put round-trip.
- [ ] **R4: agent.py core, with no device imports at module level.** Tests with fake sockets, `Params` and uplink:
  - the onroad-to-offroad transition calls `restore` and `new_drive` and sends `drive_end`;
  - a startup with an open journal while offroad restores first;
  - one summary per minute is queued, with `trialActive`;
  - an approval is applied only when parked;
  - an unparked approval is retried, then dropped at expiry;
  - an owner override ends the trial;
  - offroad counts as parked for nightly approvals;
  - only the newest message per service per loop is decoded.
- [ ] **R5: uplink.py.** Tests: the bounded queue drops the oldest; a failed send is retried later; approvals are fetched; nothing blocks the agent loop.
- [ ] **R6: install_agent.py.** Tests on a temporary folder:
  - the hook line is inserted exactly once before `exec`, and the file is otherwise byte-identical;
  - the backup is written first;
  - uninstall restores the original;
  - the install refuses an unknown `continue.sh`;
  - the hook passes a syntax check (`bash -n`) when bash is available.
- [ ] **R7: service store, notify and app.** Tests with a fake store and fake ntfy:
  - a signed summary is accepted and an unsigned one gets 401;
  - weaving minutes create one suggestion with `trial_values` and send one notification with sound;
  - Approve creates a signed approval, a second tap is refused, and Decline blocks the same reason for the rest of the drive;
  - `drive_end` schedules a nightly report about 60 minutes later, a newer drive postpones it, and the report sends at most one suggestion with Approve.
- [ ] **R8: Deploy the Railway service.** In project `ram-drive-log-bridge`:
  - create service `ram-drive-advisor`;
  - generate a public domain;
  - set the variables: `ADVISOR_KEY` through stdin, `NTFY_TOPIC`, and S3 reference variables;
  - deploy, then check `/healthz` and that an unsigned request is refused.
- [ ] **R9: Install on the Comma** (needs the Comma on home Wi-Fi):
  - pinned files and the key over SSH stdin;
  - the `continue.sh` hook, verified by reading it back;
  - reboot only with the owner's OK;
  - confirm the agent starts and sends `drive_start` on the next ignition.
- [ ] **R10: Owner steps:**
  - subscribe to the ntfy topic in the ntfy app;
  - the first live approval doubles as the parked apply test;
  - the nightly report arrives about 60 minutes after the last drive.

## Deviations from the design (recorded)

- **Nightly approvals last 12 hours** instead of 10 minutes, because the Comma may be off overnight. Live approvals keep 10 minutes.
- **Version-1 nightly review** uses the minute summaries the agent uploads, not the full R-logs. A deeper R-log review can follow.
- **The agent keeps no extra raw recording,** to keep processor use low. sunnypilot's own logs still upload as before. The minute summaries are the driving diagnostics.
