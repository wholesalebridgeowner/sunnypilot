# CLAUDE: Resume Ram 1500 work after Windows PC restart

**Open this first.** Updated November 19, 2026. This is a GitHub-only handoff, not a claim that the Comma device has been modified.

**CURRENT 2026-11-19 UPDATE FOR CLAUDE:** Review the section `## Nov 19: continuous Railway log review (ChatGPT handoff)` at the end of this file before resuming Ram work. It tracks new draft PR #31 and the five-minute Railway worker; do not confuse this with a proven Comma shell or device deployment.


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


## Additional parallel analysis branch (do not confuse with SSH work)
GitHub draft [PR #2](https://github.com/wholesalebridgeowner/sunnypilot/pull/2), branch `ram1500-pinned-comfort-20261117`, is based on Claude's `claude/ram1500-review-2026-11-17` branch. It extends the **pinned 1f7233cb decoder** with read-only source-provenance-aware following-gap, cruise acceleration and lane-change measurements, the `investigate_comfort.py` CLI and fixture tests. The push-triggered GitHub fixture CI passed at `da264b082254c482029c8c2898fbdb954a20924a`. No vehicle or safety controls changed. This PR is intentionally separate to avoid overwriting either Claude's independent review code or this branch's passive device logger. It can be reviewed/merged after the schema and command-line output are validated on local private Saturday logs.


## Nov 19: continuous Railway log review (ChatGPT handoff)

**Owner request:** Move beyond daily batch analysis. Search for new uploads around the clock; analyze once the upload stops changing. Iterate on measured vehicle comfort and lane-change behaviour, with future code/config candidates reviewed and tracked against logs. This section is intended for the user's local Claude session, not a claim Claude has read it yet.

### Existing infrastructure (verified)
- Railway project: `ram-drive-log-bridge` (project ID `db5575cb-6c02-403c-a310-b93be2cb9caf`), production environment ID `86f4c5d7-baa6-496a-bc41-7b49f8579d5b`.
- Existing `comma-log-sync` service ID `a58c9b55-23d3-4e97-a1af-9efd018e7a85` runs `*/5 * * * *` and uses the paired Comma API and the private S3 bucket for Q-/R-log collection. It is not a direct device shell.
- New, independent Railway `ram-scheduled-route-review` service ID `f73462ee-9b4c-4b0c-a785-ddf39ea70a51` has source repo `wholesalebridgeowner/sunnypilot`, branch `ram1500-auto-review-20261119`, root `/ram1500-development`, Dockerfile `Dockerfile.auto-review`, and cron `*/5 * * * *` (every five minutes, 24/7). It references the collector's S3 secrets by Railway variable references. Do not copy tokens into GitHub or reports.

### Code and tests for review
- **Draft PR #31:** https://github.com/wholesalebridgeowner/sunnypilot/pull/31, branch `ram1500-auto-review-20261119`, based on this diagnostics branch. Separate from Claude's independent review branch and earlier PRs.
- `ram1500-development/auto_review.py`: stream-based read-only aggregate route metrics for lane-change starting windows, steering-rate distributions, saturation event episodes, observed longitudinal jerk, conditional headway and source/control provenance. `compare()` reports observational deltas, not automatically validated improvements.
- `ram1500-development/auto_review_job.py`: private-bucket route discovery, one new eligible Q-log route per run, contiguous-from-zero check, 30-minute quiet period (the initial six-hour delay was reduced on Nov 19), SHA-based idempotency, separate private per-route JSON reports and `automated-ram-reviews/v1/latest.json` summary. A quiet period is a heuristic, not proof the route has completed; later uploads yield a new fingerprint.
- `ram1500-development/Dockerfile.auto-review` pins cereal schema to the **actual Comma drive build** `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; the original diagnostics Dockerfile remains unchanged.
- `ram1500-development/test_auto_review.py` and `.github/workflows/ram-auto-review-tests.yml`: offline fixture CI. Initial run caught a one-sample percentile bug (fixed in `9d0a73c`). A 30-minute quiet-period change (`6a5d641`) initially broke its fixture gate and failed CI; corrected tests in `9406572` now passed **both the focused Ram automated-review workflow and the existing broader Ram offline telemetry workflow** on Nov 19 (successful GitHub Actions runs `44562322547`, `44562322609`).

### Current limitations and next steps for Claude
1. GitHub CI at `9406572` **passed** and Railway deployment `4f683691-5848-48af-a8d9-e6a55388f60b` **succeeded** on Nov 19. Next, inspect Railway runtime logs for `RAM_AUTO_REVIEW` and verify at least one real, private route analysis. A successful Docker image/deployment alone is not proof of a completed review. If a cron job has not executed yet, distinguish 'deployed' from 'processed route'.
2. Inspect the quiet/contiguity gate; distinguish partial/new uploads from independent, authoritative route completion. Do not equate a contiguous prefix with final completeness.
3. Validate the serial log reader with actual pinned-format events and all supported enum values, without publishing raw route keys, VIN, coordinates, or credentials. Validate memory/cost and absence of duplicate counting.
4. Compare output against existing pinned-schema Claude `ramtools` investigations, keep separate from alternate drive-build schemas. Do not merge or overwrite independent work without review.
5. This branch does **not** install code or settings on the Comma, send vehicle commands, or implement a verified wide-area device read/write tunnel. Separately, an **uninstalled, un-deployed** preliminary diagnostic relay server was committed at `ram1500-outbound-bridge-20261119:ram1500-development/remote-bridge/server.py` (commit `8f65984c`). It requires its own authenticated Comma-initiated agent, tests, and deployment plan before any hardware interaction.
6. Future workflow: uploaded route → private metrics/baseline analysis → review/simulation → propose small, explicitly versioned candidate → after engineering validation, test on a parked vehicle through an authenticated path → compare subsequent routes. No automatic vehicle controller modifications are part of this review service.

The user should not need to reconnect a phone to the Comma's Wi-Fi. Its connection to the phone hotspot may be interrupted when parked; existing uploaded logs remain available to Railway and later uploads should resume when it reconnects.


## Nov 19 evening: material progress on blocked route-review output (ChatGPT → Claude)

**Do not confuse deployed success with validated drivability improvement.** The existing Railway collector and reviewer run every five minutes. The reviewer had repeatedly quarantined private Q-log routes because logger event timestamps can arrive out of order, especially near segment boundaries. The historical per-segment-only bounded ordering trial refused *any* backward boundary overlap. Previous clock audits on private data counted within-segment inversions under two seconds, often with no large boundary inversion.

To test the root cause, ChatGPT created **draft PR #45**: https://github.com/wholesalebridgeowner/sunnypilot/pull/45, branch `ram1500-crosssegment-readonly-pilot`, based on the actual deployed review source commit `2377c055e1254498307b45aa471dcbda7814f763` (not simply the remote branch pointer). It adds:
- `auto_review_crosssegment_order.py`: strict source-pinned 2-second bounded heap reordering *across* segment boundaries, preserving all events, with a hard 10,000-event memory ceiling and rejection of larger clock regressions or mismatched source headers.
- `auto_review_crosssegment_pilot.py`: private one-shot replay of an already quarantined route into a distinct experimental JSON object; original quarantines and accepted latest baseline are unchanged. No Comma commands, settings writes, or actuator modifications.
- `test_auto_review_crosssegment_order.py` and `test_auto_review_crosssegment_pilot.py`: fixture tests. Dedicated GitHub Action `44657352566` passed at commit `e622874`.

**FIRST REAL ROUTE TEST SUCCEEDED**: A pin-to-commit Railway one-shot service `ram-crosssegment-one-shot` (service ID `26807fb2-9afe-4eb0-b837-d15e070c73d3`, deployment `d37e61ff-8237-4e8e-8405-0ec5f62c3005`) analyzed one actual previously quarantined **54-segment** route and saved experimental aggregate measurements: **32,290 carState samples, one detected within-segment inversion, no >0.5-second cross-segment inversions, zero observed lane-change starting windows and zero steerSaturated event episodes**. This establishes that the bounded ordering can process at least one complete source-pinned historical Q-log route that the previous direct aggregator rejected; it does not establish the quality or representativeness of every metric or the cause of any vehicle behaviour. The aggregated result is private in S3 under `automated-ram-reviews/v1/intersegment-trials/`. Do not print route IDs, raw logs, VIN, location, credentials, etc.

**Next engineering goal:** Re-run the separate one-shot on additional distinct quarantined routes; measure acceptance/rejection ratios, recover lane-change/comfort metrics where Q-log coverage permits, compare source-matched and condition-matched routes, then decide whether a separately reviewed promotion into the five-minute automatic reviewer is justified. Do not silently turn quarantine into accepted telemetry. The old `comma-log-sync` collector intermittently saw Athena HTTP 409 on R-log upload requests but also successfully uploaded new logs in subsequent runs; investigate backoff if this persists, but it does not invalidate existing S3 logs.


### Nov 19 night: cross-segment recovery verified; five-minute reviewer updated

**Review this before continuing.** Work from draft PR #45, https://github.com/wholesalebridgeowner/sunnypilot/pull/45, current tested commit `8d86107587cb3bd0d8bb5f0679d62bd5b85df01e`.

- An isolated Railway pilot recovered **three distinct previously quarantined** private Q-log recordings with the new global bounded 2s timestamp alignment: 54, 3 and 2 segments; 32,290 + 1,479 + 1,031 = **34,800 recorded carState samples**. Original quarantine reports remained unchanged; one-shot results were saved under private `automated-ram-reviews/v1/intersegment-trials/`.
- A privacy-allowlisted aggregate reader measured for the 54-segment route: 3,231.7 seconds, 32,290 each of carState, carControl and controlsState, 6,454 lane model messages, 12,912 radarState messages, p90 absolute steering-rate 5.0°/s, observed unfiltered acceleration jerk p90 1.7013 m/s³. **Critical:** all 32,290 carControl messages show `latActive=False`. The other two recordings also have zero active lateral samples (1,479 and 1,032 false). Hence these are NOT usable assisted-steering comparison baselines, regardless of successful timestamp repair. The raw Q-logs are private. No observed lane-change start or qualified headway event in these three; do not claim those systems performed well from zeros.
- **Independent known active evidence:** The prior private 20-segment **R-log** cruise/steering audit in Railway service `ram-full-route-cruise-private-once` did measure **10,764 `carControl.latActive=True` samples**, **10,861 `carState.cruiseState.enabled=True` samples**, and 114,076 carControl rows. This is an observational selected 20-segment window, not a proven complete trip; please target those proven active samples for future comparison instead of manual/non-engaged drives.
- New `auto_review_job.py` on PR #45 **prioritizes fresh uploads** before historical quarantine diagnostics; uses the global bounded, per-segment-source-verified event-time ordering; records a summary of ordering quality; and **does not update latest steering comparison baseline unless `carControl.latActive` has actual true samples**. This avoids a manual drive replacing the assisted-driving baseline.
- All **three GitHub workflows passed** at `8d861075` after adapting the legacy historical-quarantine fixture to reflect bounded event-order recovery.
- **Production Railway review service updated**: `ram-scheduled-route-review`, service `f73462ee-9b4c-4b0c-a785-ddf39ea70a51`, five-minute `*/5 * * * *` cron, now pinned to `ram1500-crosssegment-readonly-pilot@8d861075`. Deployment `6280691a-8e55-4168-8bdd-e0dd1fd34a8b` reported **SUCCESS**. Its actual first new-upload cron result after this change was not yet inspected; a successful deploy is not proof of the new path reading an eligible active route. Previous deployed commit `2377c055` remains available in GitHub as rollback baseline.
- Separate one-shot service `ram-crosssegment-one-shot` has a pinned read-only summary command. Do not treat it as a remote Comma shell. **No vehicle settings or control code were changed.**

**Priority order for Claude/automation:** (1) verify next five-minute review execution on commit `8d861075`; (2) determine why new-route eligibility only sees non-assisted recordings and investigate recent active R-log capture/upload coverage, using existing `comma-log-sync` / source-pinned Railway tools; (3) compute condition-matched steering/comfort metrics **only with verified `latActive=True`**; (4) propose one specific, measured candidate change after actual replay; (5) separately advance the Comma-initiated parked remote connection plan, with no assumption that Athena is an arbitrary shell. Aim for a verified metric or clear blocker per automated iteration, not a generic 'still running' status.


## URGENT time-marked steering warning, Nov 19, 8:19 PM CST (new driver report)

**Driver's exact words:** "8:19 p.m. I had a torque steering warning." This is **2026-11-20T02:19:00Z**. The exact displayed text and whether it was Comma or OEM cluster are not yet confirmed. Track in [GitHub incident #46](https://github.com/wholesalebridgeowner/sunnypilot/issues/46). This incident takes precedence over normal Q-log comfort summaries and the manual/non-assisted drives.

- The existing `comma-log-sync` finished at **02:15:51 UTC**, uploading five files (18 routes discovered, zero collector request errors); that predated the incident. Its next observed run completed at **02:20:42 UTC**, uploading five files, 226 existing files, and zero request errors. This verifies ongoing transfer **but does not establish whether the 8:19 event is in uploaded R/Q-logs**.
- **Immediate engineering objective:** locate device route/segment that spans 02:19 UTC using paired-device route metadata and private manifests. Confirm wall-time to logger-monotonic time alignment, source commit and `latActive` before interpreting any data. Pull R-logs where available, focusing on ±30–60 seconds, and correlate event labels, controller torque request, native CAN output command, EPS/driver torque/steering feedback and saturation/driver override/fault flags. Use 1–2 minutes wider window if exact timestamps have drift. Keep raw identifiers and driving locations private. If no R-log yet, report the gap; do not substitute old recordings. The user's exact terminology is *not yet independently verified* as `steerSaturated`.
- Until diagnostics are complete, no controller or tuning modification based on the warning. A user may still be driving when this handoff is read; do not prompt them for device interaction underway.


## VERIFIED: 8:19 PM steering saturation Q-log, Nov 19

**Claude priority:** Issue [#46](https://github.com/wholesalebridgeowner/sunnypilot/issues/46) now contains a **source-verified exact warning finding**. Do not keep treating it as an unconfirmed user characterization. The owner reported a steering torque warning at 8:19 PM CST on Nov 19, 2026. The private Q-log shows **`steerSaturated` (warning=true) at approximately 8:19:09.18 PM CST** (2026-11-20 02:19:09.18 UTC), 9.18 seconds after reported minute.

- Targeted read-only incident branch: `ram1500-2019-steering-warning-probe`. New probe `ram1500-development/incident_qlog_probe.py` identifies the real route via comma.ai paired-device timestamp metadata (rather than parsing opaque S3 route names), downloads only seven Q-log segments near expected segment 38, checks the pinned source `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, and verifies wall-to-monotonic time anchors in all seven. Deployment `33bf9ad3-1836-4b35-8133-03097151d7a0` returned the real result. Newer code commit `94807d97` corrects the actuator-output torque label to avoid calling a normalized scale 'CAN-native'. The GitHub fixture CI passed at preceding commit `aa624578`; recheck at latest head before modifying production.
- Four-minute window: **2,400 `carState` + 2,400 `carControl`** observations, **1,089 latActive=true**, one `steerSaturated` sample, 244 `steerOverride` samples (samples not episodes). The `steerSaturated` event's `warning` flag is true.
- ±5 seconds of exact event: `latActive=true` for **100/100** `carControl` samples; `lateralControlState.saturated=true` in **7/100** `controlsState` samples; normalized requested steering torque `p90(abs)=max(abs)=1.0`; `carOutput.actuatorsOutput.torque` magnitude also reaches 1.0, **not a verified native CAN command**; EPS sensor abs p90=305/max=326; driver torque sensor abs p90=50/max=65; abs steering rate p90=20°/s/max=52.5°/s; speed p90=10.961 m/s/max=11.151 m/s.
- This is a **confirmed assisted-driving steering saturation alert** with the normalized controller request at its ceiling nearby, not proof of physical EPS torque saturation, root cause, excessive driver input or a bad calibration. Do not propose a specific torque increase from these counts alone.
- One route matched 02:19 UTC by actual comma.ai start/end metadata; five Q-log segments around expected segment 38 were present, but **zero R-logs within ±2 segments** even though there were R-logs elsewhere in the route at inventory time. Collector runs at 02:15 and 02:20 UTC successfully uploaded five files each but do not prove the warning's own R-log has arrived. Recheck later inventory, and use a source-matched R-log to inspect request to native output, controller phase, EPS response and driver input if available. If not, report missing evidence explicitly.
- The full anonymized result and exact timestamp are in [GitHub incident #46](https://github.com/wholesalebridgeowner/sunnypilot/issues/46). Follow this time-marked incident before general non-assisted Q-log tuning work. Keep raw routes, VIN, location, CAN and credentials private; all code so far is **read-only**.


## VERIFIED R-log result — 8:19:09 PM torque ceiling

**Critical new evidence:** The owner's Comma reconnecting to home Wi-Fi allowed the private R-logs for the incident to become available. The 8:42 PM CST S3 inventory showed 19 R-log segments stored for the one matching route (four within ±2 of expected segment 38), versus zero earlier. The subsequent time-centered R-log probe actually decoded **five contiguous segments 36–40** with source headers and wall/monotonic clocks verified in all five.

- Exact `OnroadEvent.steerSaturated` warning is **2026-11-20T02:19:09.18Z (8:19:09.18 PM CST Nov 19)**, matching Q-log source-verified observation.
- In ±5 seconds: **1000** each of `carState`, `carControl`, `carOutput` and `controlsState`. `latActive=true` **1000/1000**; `controlsState.lateralControlState.saturated=true` **74/1000**; `carState.steeringPressed=true` **0/1000** (flag only).
- `carControl.actuators.torque` normalized request p90(abs)=max(abs)=**1.0**. **Native CAN command field** `carOutput.actuatorsOutput.torqueOutputCan` p90(abs)=max(abs)=**350**, exactly matching prior documented Chrysler `STEER_MAX=350` reference constant from installed-build source. `carOutput.actuatorsOutput.torque` is normalized, not native; do not confuse them.
- EPS steering sensor abs p90 **307**, max **339**; driver torque sensor abs p90 **51**, max **75**; steering rate abs p90 **20°/s**, max **52.5°/s**; speed p90 **10.961 m/s**, max **11.153 m/s**.
- This significantly narrows cause to **active steering torque demand reaching the known source-referenced native command ceiling**, confirmed by high-rate R-log output, though physical EPS acceptance, steering error, driver load and exact controller phase are not yet established. **Do not automatically raise STEER_MAX** or make an on-device change from this single observation.
- Code and tests in **draft PR #48**: https://github.com/wholesalebridgeowner/sunnypilot/pull/48 (branch `ram1500-2019-steering-warning-probe`, based on cross-segment analysis PR #45). `incident_rlog_probe.py` source-pinned 5 R-logs, anchored event time, and reports only approved aggregate fields. All incident probe fixture tests passed at `c28445974e9b9183922333de8847f8ef8ef2a06d`. Railway one-shot deployment `e454b580-48f1-472e-86c5-cdfa0d00e0db` completed successfully. Exact results are archived in anonymized GitHub issue [#46](https://github.com/wholesalebridgeowner/sunnypilot/issues/46).

**Next actionable work for Claude/hourly automation:** Replay the true 8:19:09 window against pinned installed Chrysler controller math, separately quantify hard command cap, error-to-EPS measurement bounds, slew-rate clipping and any LKAS gating; inspect EPS feedback and the warning preceding state to distinguish legitimate torque-demand limit from a controller calibration issue. Look for similar events on independent active-assistance drives and match speed/curvature/driver torque before suggesting a measured candidate adjustment. Preserve user approval and parked-only validation for any eventual changes; the work so far is read-only.
