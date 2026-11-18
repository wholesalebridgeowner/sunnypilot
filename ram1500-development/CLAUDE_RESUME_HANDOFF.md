# CLAUDE: Resume Ram 1500 work after Windows PC restart

**Open this first.** Updated November 17, 2026. This is a GitHub-only handoff, not a claim that the Comma device has been modified.

## Repository and exact branch
- Repository: `wholesalebridgeowner/sunnypilot`
- **Current diagnostics and forensics work:** `ram1500-steering-forensics-20261117`
- Base branch: `ram1500-custom-development`
- Draft PR: https://github.com/wholesalebridgeowner/sunnypilot/pull/1
- This branch contains the most recent tools. Read and review the PR before any device installation; do not assume the working copy already has these commits.

## User goal
Develop a Ram 1500-specific high-quality assisted-driving stack: smoother lane centering, curves and lane changes; improve observed following distance, ACC/throttle comfort and responsiveness; increase telemetry resolution; later examine low-speed and navigation functions only within genuinely supported and validated safety interfaces. Do not modify live actuation limits, Panda safety or OEM authorization.

## Deliverables already saved in this branch
- `ram1500-development/PROJECT_PLAN.md`: engineering scope and acceptance gates.
- `ram1500-development/CLAUDE_REVIEW_PROMPT.md`: independent review request.
- `ram1500-development/DEVICE_DIAGNOSTICS.md`: device capture design, guarded manual installation procedure, verified remote-access audit.
- `ram1500-development/analyze_route.py`, `test_analyze_route.py`: offline saturation-event window extraction and tests.
- `ram1500-development/analyze_comfort.py`, `test_analyze_comfort.py`: longitudinal ownership, lead headway, acceleration and lane-change analysis.
- `ram1500-development/device_telemetry.py`, `test_device_telemetry.py`: **opt-in and passive** local diagnostic sidecar for selected Panda CAN and openpilot streams, capped local storage; no CAN transmission or remote upload.
- `ram1500-development/summarize_can_capture.py`, `test_summarize_can_capture.py`: local read-only raw CAN message timing diagnostics.
- `ram1500-development/reports/2026-11-14-steering-analysis.md`: known findings, hypotheses, open issues.
- `ram1500-development/reports/2026-11-14-following-throttle-lane-change.md`: stock ACC attribution and comfort investigation.
- `.github/workflows/ram-analysis-tests.yml`: offline fixture CI. Most recent run at commit `5b044e2f5ab7fb02852bf15ce3997ff34a32ae46` **passed**; new handoff commit itself is documentation-only.

## Verified Comma connectivity
From an authorized, read-only Athena JSON-RPC probe performed via existing Railway project `ram-drive-log-bridge`:
- Comma API device reports type `three`; driving telemetry reports `tici`. Treat this as **Comma 3**, not Comma 2, for software/build compatibility.
- The installed operating stack returned Sunnypilot `0.10.1`, remote `github.com/sunnypilot/sunnypilot`, branch `staging-tici`, commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. Historical driving logs indicated a dirty working tree, so running code may differ.
- Device was online during inspection.
- `getGithubUsername` and `getSshAuthorizedKeys` both returned empty/unconfigured.
- Comma Prime inactive.
- No remote code-install capability was available through Railway/GitHub connectors or the supported Athena diagnostic RPCs. **Device diagnostic logger is not installed.**
- Railway collector service already retrieves device logs using authorized Comma APIs; it is NOT an arbitrary remote shell.

## Windows PC / Claude setup: next user-approved task
The user has a Windows PC with Claude. Following PC restart:
1. Confirm your working tree and SSH tools before making changes. Git fetch and switch to `ram1500-steering-forensics-20261117`.
2. Read `DEVICE_DIAGNOSTICS.md`, inspect all new Python and tests. Run `python -m unittest discover -s ram1500-development -p "test_*.py" -v`. Check pinned schema and actual installed firmware before touching device.
3. On Windows, check OpenSSH Client availability and generate an **operator-owned SSH key pair** if needed; never share or commit the private key. Only register the public key with the user's GitHub account through user-authorized action.
4. Ask user to enable SSH on their parked Comma and set GitHub username `wholesalebridgeowner` using on-device settings. Make sure the Comma and PC share a reachable local network; do not assume the truck is home or automatically open a wide-area SSH port.
5. Establish and verify SSH with host-key checking and user consent, and inspect device OS, working-tree version, free space and resource usage. No automatic deployment or unattended startup.
6. Fix any device Python/cereal compatibility issues in a separate commit. Start with a short 1–2-minute **parked, read-only** capture using low sample rate, then verify timing, disk retention and system health; roll back if unstable.
7. Keep raw CAN, GPS, VIN and dongle data local/private. Report only anonymized quantitative findings and code references back to PR.
8. Do not flash device, reset the installed branch, deploy live controllers or attempt unauthorized safety-gate changes. Any future control candidate needs separate replay and independent validation.

## Engineering evidence and next data work
- Saturday November 14, 2026 route `000003cc--27a46b2116` yielded 51/51 Q-log segments, 728,719 readable messages; ~30,000 car state/control/output records, ~11,995 live torque records.
- One `steerSaturated` alert sample at +171 s. Two `canError` and controls mismatch samples at about +3002 s around parking/shutdown; not yet demonstrated to be in-motion failures.
- Counts such as `steerOverride` = 1,675 are **sample counts**, not independent interventions.
- Need precise time-aligned numerical actual-vs-commanded steering, motor torque, driver torque and lateral tracking around +171 s.
- For distance and throttle, pinned Chrysler Ram interface sends LKAS and cruise-control buttons, but does not emit direct throttle/brake CAN actuation; factory ACC appears to own longitudinal behavior. Confirm with `carParams.openpilotLongitudinalControl` and `carControl.longActive` from the recorded build.
- For lane change, source fades lane-line probability 0.5 s on initiation and ~1 s during finishing; evaluate measured steering-rate and jerk before considering changes.
- Source submodule pinned in this GitHub branch: `opendbc_repo` at commit `df807f8be394231b2366a1ecd9c9265ab51d2400`; actual device install must be checked before applying this assumption.

## Immediate Claude deliverable
Return a brief device compatibility assessment, local SSH connectivity status (without keys/secrets), exact commits for any fixes, fixture test results, and one **parked-session-only** reversible installation plan requiring the user's approval. Collaborate via the existing GitHub PR; avoid duplicating the existing files or silently modifying running vehicle software.

## New on this continuation branch (post-restart-ready)
- `ram1500-development/windows_preflight.ps1`: **read-only** Windows prerequisite check; reports installed git/OpenSSH tools, existence of SSH *public* key files, and optionally whether TCP/22 responds on a user-provided device IP. It does not read private keys, configure SSH, install anything, or alter the Comma.
- Run in PowerShell, from the cloned repository: `powershell -NoProfile -ExecutionPolicy Bypass -File .\\ram1500-development\\windows_preflight.ps1`. Add `-DeviceIP <known-local-Commap-IP>` only when Comma is home on the same LAN and IP is verified.
- `device_telemetry.py` now creates owner-only directories (0700) and telemetry capture files (0600); fixture test asserts permissions on POSIX.
- Latest diagnostics work remains on branch `ram1500-steering-forensics-20261117` and draft PR #1; check CI on current HEAD, rather than relying on earlier SHA references.

## Claude response — 2026-11-17 (after restart)
- Reviewed `device_telemetry.py`, `summarize_can_capture.py`, `windows_preflight.ps1`, both Nov 14 reports and PR #1.
- **Device compatibility (source-level, not yet on hardware):** sidecar is passive (msgq subscribe only); its nine CAN IDs match the
  drive build's Ram DT safety addresses; every field it reads decodes with the pinned drive-build schema (`1f7233cb`).
- **Risk found and guarded:** msgq evicts *all* subscribers of a service when a 16th subscriber registers. The sidecar now checks
  reader counts read-only from `/dev/shm` and refuses to start if it would evict `card`/`controlsd`; capture also stops on motion.
  Details and the gated parked-pilot plan: `DEVICE_DIAGNOSTICS.md` → "Claude review of the sidecar" and "Parked pilot plan v1".
- **Windows SSH status:** OpenSSH client present; this PC's DoodlePC key is already one of the two keys on the
  `wholesalebridgeowner` GitHub account, so no new key is needed. Comma side not yet enabled (needs Mark in the truck, gate G1).
- **Tests:** 24/24 offline tests pass on Python 3.12 and 3.14 (one Windows-only failure fixed).
- **Source of truth caution:** both Nov 14 reports cite `opendbc_repo@df807f8` (this fork's April 2026 code). The truck ran
  `sunnypilot@1f7233cb` (Oct 2025), which shares no git history with it and differs in every steering file. Pinned-schema decoding,
  exact event episodes and a ranked 171 s analysis are on branch `claude/ram1500-review-2026-11-17`
  (`reports/2026-11-17-claude-independent-review.md`, `REVIEW_LOG.md`); the two branches still need to be reconciled.

## New steering-alignment measurement tool (2026-11-17 evening)
- `ram1500-development/steering_saturation_report.py` consumes local CSV exported by `analyze_route.py` and performs bounded past-sample joins (150 ms default) among controller state, requested normalized torque, CAN steering command, vehicle EPS torque feedback and saturation events.
- It summarizes saturated/non-saturated samples, time-match coverage, requested-vs-commanded native LKAS gap using source-level 350-unit scale, and event-adjacent snapshots. It **does not equate EPS motor torque and CAN steering command or treat a warning as proof of physical EPS saturation**.
- Use: `python3 ram1500-development/steering_saturation_report.py /private/ram-171.csv --output /private/ram-171-steering.json`. Keep real-route CSV and JSON private, not committed to GitHub.
- `test_steering_saturation_report.py` includes four synthetic tests (time alignment, missing/stale data, non-finite values and event grouping). Local tests passed; GitHub Actions offline Ram analyzer workflow passed on commit `ad668e35fbc7eeecffdb7cb18c7a2fac153486bd`.
- Next step: execute on **private actual-route CSV**, verify real numerics and flag confidence. Synthetic CI passing does **not** establish a vehicle diagnosis or release readiness.


## Coordination note: pinned-drive branch and ACC analysis (new)
A separate Claude independent-review branch now exists: `claude/ram1500-review-2026-11-17`, latest observed commit `97779e9b23a150cbd1fb5b1fde2b5b0b4b602bc8`.
It includes `ramtools/`, `investigate_route.py`, a schema pin to the **actual recorded driving commit** `1f7233cb`, 31 fixture/offline tests, `REVIEW_LOG.md` and an extensive independent review. **Do not overwrite those changes while working on SSH.** Check the branch and reconcile any work before merging it with this diagnostics branch.

A source-reconciliation report now lives on this diagnostics branch at `ram1500-development/reports/drive-build-acc-lanechange-crossreview.md`. In particular, the November 14 **installed build** has the original basic Chrysler cruise cancel/resume support; the newer code's Intelligent Cruise Button Management was not part of that recorded car-controller source. Its lane-line fades were 0.5-second exit and ~1-second re-entry. Keep recorded-build claims separate from current-master behavior.

Next non-SSH tasks: execute the *pinned* `investigate_route.py` against private Saturday Q-logs, and get R-logs for the saturation segment before choosing a controller fix. Never run a mixed-schema decode with both the newer branch's opendbc and pinned drive-build schema loaded in the same process, as Claude reproduced a pycapnp schema-ID conflict.
