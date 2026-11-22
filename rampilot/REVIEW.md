# RamPilot offline review

The install baseline is `1f7233cb` (comma three). The native Qt source is `737a6c4236e843034680c951005b38d15815363f`. The review branches are local. A vehicle installation and a push remain on hold for owner approval. Each display validation switch also needs its own evidence.

## Review commits

The table covers every change introduced on `codex/rampilot-fixes` after its branch point `a59d954`, and on `codex/advisor-report-fixes` after `5a2324b`. The advisor is in a separate Git repository.

| Item | Repository and commit | What was checked offline | Outstanding |
| --- | --- | --- | --- |
| 1 | RamPilot `11da264` | Two successive installs and a rollback restored the original branch, commit and updater setting. 34 installer checks passed. | Device environment |
| 2 | RamPilot `01c2c06` | 45 installer checks covered offroad state, forced failure and retry, independent UI digest, executable permissions and exact baseline restoration. | Real updater and reboot |
| 3 | RamPilot `f2f023e` | Five UI-builder tests covered directory isolation, onroad rejection, build provenance and repeated output. | ARM toolchain and build |
| 4 | RamPilot `8b674b5` | 22 speed-limit vectors through the Chrysler interface. Removing optional frames from 2,000 recorded batches preserved CAN health. | Paired cluster observation |
| 5 | RamPilot `3866d9a` | Applied the sign patch to the pinned source. Compiled the sign logic with valid and invalid inputs and both unit systems. Item 13 also covers older streams and paused playback. | Native display |
| 6 | RamPilot `672b7ac` | Six startup and cache-transition checks used distinct live and stored delays across controls, three model paths and the torque estimator. | Whole-daemon startup on hardware |
| 7 | RamPilot `e4e451b` | Alert selection preserved the directional prompt, occupied-lane warning and collision alert. Text fit the pinned fonts. | Native rendering |
| 8 | RamPilot `9af753a` | Ten smoother checks and an open-loop 38-maneuver replay cover the 0.75 m/s³ candidate, the speed cutoff and tail. | Truck response and driver comfort |
| 10 | Advisor `4570208` | All 1,178 advisor tests passed. Legacy summary counts, missing windows, unfinished drives and report retries have regressions. Raw parking logging is off. | Real storage and delivery. A crash between notification acceptance and durable acknowledgment can duplicate a report. Migration recovers the old pending list and last known drive. |
| 11 | RamPilot `c087e26` | Documentation of Ratekeeper pacing checked against all three named loops and three timing tests. | Documentation only |
| 13 | RamPilot `62950a1` | Compatible wire schema, passive diesel decoding, independent freshness and default-off validation. 34 producer/schema tests and 34 subtests passed. Removing 470 diesel frames from 2,000 recorded batches preserved CAN health. Both native patches applied in sequence to the pinned source. Compiled presentation checks covered unreceived and invalid messages, paused playback, display units, Unknown and panel bounds. | ARM Qt build and screen. Fuel scale, cluster average/reset, range, DEF and the regeneration interpretation still need independent references. |
| 14 | RamPilot, this review document | Commit inventory checked against both branch histories. | Claude’s combined review |

A clean Linux export of the combined RamPilot implementation at `62950a1` passed all 45 installer checks and 43 focused regression tests. The Qt tests compiled the actual presentation logic with host fixtures. The comma three native executable has not been built or installed.

## Research and display behavior

**Item 9.** In all 38 original maneuver windows the return request peaked before Finishing ended. On a three-second follow-up window with 37 isolated maneuvers, extending the tail changed one peak by 0.033 m/s². The private report is `rampilot/live_replay/RETURN.md` in the analysis archive. Replay measures requested steering. Vehicle response remains unmeasured.

**Item 12.** An independent pass over 563 segments from 23 drives found a single sustained candidate regen flag on drive 24. The fuel signal rose for comparable engine power while the candidate loading band fell. The flag's meaning remains unverified. The factory manual describes conditional regeneration messages. A timestamped factory indication or independently documented PCM status can anchor validation without requiring the driver to monitor the dash. The known truck's default display reads **DPF regen · Unknown**. Numeric rows appear individually after validation and require fresh input. [Display details](ui/README.md)

**Trip follow-up.** The private full-log checker, `check_lisa_full_logs.py`, matches newly copied rlogs to the trip's reduced-log header before counting candidate cycles. Gaps interrupt a cycle. A second occurrence is supporting evidence; it cannot enable a display switch. Keep route identifiers, raw logs, and private test outputs in the analysis archive.

## Inherited RamPilot changes

These were already in the RamPilot review base `a59d954`. The installed build remains pinned separately. The implementation commits and companion documentation below account for the interval from `1f7233cb` through `a59d954`.

| Area | Inherited commits | Evidence or review link |
| --- | --- | --- |
| Lane request shaping and steering-only feedback | `928d4d4`, `2e50a09`, `8ab991e` | 38-event replay and steering-limit feedback tests |
| Blind-spot startup and timeout sound | `3b988b2`, `04f2826`, `fd3983b` | Chrysler startup interface and sound tests |
| Calibration in reverse | `e51fefb`, `bda2ccc` | Forward/reverse learner tests |
| Installer, rollback and script line endings | `e6ae3eb`, `9741fa8`, `dab2aaf` | Items 1–3 |
| Checksum, delay and loop timing | `55eee1f`, `c8c6772`, `ec0c443`, `70a9c2f` | Chrysler checksum equivalence and items 6 and 11 |
| Blind-spot availability notice | `30a7ad7`, `b11cfdf` | Item 7 |
| HUD fields, UI digest and sign | `f693d27`, `c20d4ac`, `a59d954` | Items 2–5 and 13 |
