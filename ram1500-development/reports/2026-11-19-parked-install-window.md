# Ram 1500 — November 19 parked installation decision

**Owner goal:** move toward an actual improvement, not just perpetual log ingestion. The 11-step source-of-truth plan is `ram1500-development/reports/2026-11-18-end-to-end-development-plan.md` on `ram1500-full-route-cruise-reconcile-20261118` (draft PR #18).

## What can be installed tonight

**Candidate:** install the updated *passive diagnostic sidecar* under `/data/ram-diag/`, **not** a steering calibration or ECU change. This is a real device software update to the diagnostic recorder only. The previously installed sidecar was a short-lived diagnostic pilot, and its last parked run exceeded the 15% CPU gate (measured 21.6% of one core). The latest source has lower-overhead CAN parsing, a 50ms drain loop, CPU and private-RSS self-limits, source-specific queue-slot verification, and immediate stop when the truck moves. These optimizations passed off-device tests but **have not yet passed an on-device retest**. No claim of road-use improvement is made.

**Pinned code for the operator:**
- Repository `wholesalebridgeowner/sunnypilot`, candidate branch `ram1500-parked-diagnostics-acceptance-20261119`.
- `ram1500-development/device_telemetry.py` — existing passive sidecar from diagnostics base branch, no new actuator logic.
- `ram1500-development/summarize_can_capture.py` — existing CAN rate summaries.
- `ram1500-development/parked_capture_acceptance.py` — new fail-closed, privacy-allowlisted two-minute run gate.
- `ram1500-development/test_parked_capture_acceptance.py` — synthetic health, reader-slot, movement, fault and privacy tests.
- `.github/workflows/ram-parked-diagnostic-acceptance.yml` — CI.

The exact current source must be fetched to the trusted Windows PC and its git commit and file hashes recorded **before** copying to the device. Do not use mutable `main`, an unsigned random install URL, or the Comma's remote cloud log API to perform an alleged file write.

## Go/no-go prerequisites

1. **User approval of this specific diagnostic-only install**, with the Comma physically parked, transmission in Park, and ignition state as required for onroad diagnostic sampling. Use an approved, authenticated SSH connection from the Windows PC on the same reachable LAN. Its home Internet connection alone does not enable remote shell from Railway.
2. **Exact device identity/source read-back:** Comma 3 / `tici`, installed Sunnypilot `staging-tici@1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, compatible interpreter `/usr/local/venv/bin/python3`, EPS firmware `68466110AB` unless an independently verified newer snapshot explains a change. Check the real local git working tree and version before writing.
3. **Process and resource preflight:** `pandad`, `card`, `controlsd`, `selfdrived` and `loggerd` stable; adequate disk/RAM; no current fault alerts. Run the sidecar's non-subscribing `--check-slots` first. The original hardware showed `carState` at **14/15** subscribers before the one allowed sidecar run, and `pandaStates` full at **15/15**. A second sidecar run without a Comma reboot can evict critical subscribers, so **never retry before a verified parked reboot and fresh preflight**.
4. **Explicit rollback snapshot:** preserve the original `/data/ram-diag/device_telemetry.py` as a local owner-only backup outside `/data/openpilot`. Never overwrite live driving/controller files. Check available free space and file permissions (0700 directory, 0600 private capture files). Record source SHA-256 hashes locally, not secret values.
5. **No unattended capture:** operator supervises one two-minute run, using the default read-only capture (no `--allow-moving`, no `--all-can-ids`, no automatic restart, no background service).

### Parked two-minute pilot (after user GO, local copy and preflight)

These are examples to be executed by the operator *on the authorized parked device* after checking actual paths. They are **not commands that have been executed by ChatGPT**.

```bash
cd /data/openpilot
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py --check-slots

# One capture only, after slots and vehicle condition pass.
PYTHONPATH=/data/openpilot timeout 150 /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py \
  --output-dir /data/ram-telemetry --minutes 2 --sample-hz 2 \
  --max-mb 8 --keep-files 2 --max-cpu-pct 15 --max-private-mb 150 --drain-ms 50

/usr/local/venv/bin/python3 /data/ram-diag/parked_capture_acceptance.py \
  /data/ram-telemetry/ram-telemetry-*.jsonl \
  --output /data/ram-telemetry/acceptance.json
```

Only inspect/share the **aggregate** `acceptance.json` and optional CAN summary privately, never raw captures, VIN, GPS, CAN payloads or user tokens. The acceptance script prints only allowlisted aggregates.

### Acceptance

`parked_capture_acceptance.py` is **fail-closed**. Its `passed=true` requires:
- One complete `sidecar_start` / `sidecar_stop` with reason `duration reached`, a two-minute health history, and the moving/unbounded modes disabled.
- All 11 subscribed services' reader counts increase by exactly one, never exceeding 15; no missing reader snapshots.
- All observed 10-second window CPU figures no higher than **15% of one core**; private RSS no higher than **150 MiB**.
- Vehicle speed no greater than **0.3 m/s**, enough `carState` and CAN coverage, valid CAN data, zero decoder failures and no unexpected fault event.
- Manually verify the critical process PIDs remained stable, the capture terminated, the car remained in Park and the displayed device health stayed normal; the offline parser **cannot certify these from the capture alone**.

**If any gate fails:** stop; preserve private diagnostics; do not rerun sidecar before a parked reboot; restore the backed-up diagnostic script if needed. Reverting diagnostic files does not repair message-queue reader allocation until reboot. No vehicle control settings are changed.

## What is not ready tonight

Steps **6, 8 and 10** of the owner's 11-step plan still require a genuinely source-pinned **numeric calibration candidate**, multiple independent and held-out active-driving routes with steering stability and ping-pong/overshoot scorecards, independent review, and supervised OEM/Panda vehicle validation. The November 19 **8:19:09 PM** `steerSaturated` incident was confirmed in pinned R-logs with a source-reported CAN torque command field hitting **350**, but that alone does **not** justify raising the 350 command cap or changing live steering gains.

**Decision:** *diagnostic pilot is the only proposed device software install in this window*. A steering, throttle, ACC, Panda, EPS or safety-controller build is **NO-GO** on the evidence reviewed to date. Re-evaluate after a validated replay candidate and the planned release gates pass.

## Remaining blockers to a real installation

- No verified direct SSH/authorized shell from this chat to the parked Comma over home Wi-Fi; Railway Comma API has log access, **not** arbitrary on-device read/write.
- No user-confirmed current parked maintenance session/Windows PC connection for executing the diagnostic update.
- No passing on-device CPU/private-RSS validation of the optimized passive sidecar yet.
- No reviewed, held-out controller calibration release suitable for live vehicle software.

**Next deliverable:** a real parked diagnostic pass/fail report (if the operator opens a maintenance session) and a separate, data-qualified offline steering candidate. Never represent this diagnostic install as a driving-experience improvement.
