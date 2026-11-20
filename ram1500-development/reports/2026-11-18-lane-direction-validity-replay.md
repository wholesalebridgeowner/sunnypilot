# November 18 — lane-model direction and validity gates; private replay

**Status: completed offline analysis; NOT physical lane-crossing validation, vehicle control code, or an installation candidate.**

## Source and isolation

- Research-only [draft PR #8](https://github.com/wholesalebridgeowner/sunnypilot/pull/8), stacked on [draft PR #7](https://github.com/wholesalebridgeowner/sunnypilot/pull/7). Baseline `ram1500-custom-development`, steering/PAM PR #1 and Claude's review branch remain unchanged.
- Recorded driving build and decoder: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. All three observed `initData.gitCommit` values matched. The on-device dirty working tree is **not** independently byte-verified.
- Actual private Railway R-log replay: read-only one-shot service `ram-private-comfort-once`, deployment `private-railway-id` **SUCCESS**, analyzer commit [`612a72c`](https://github.com/wholesalebridgeowner/sunnypilot/commit/612a72c7400d05f2c2067706f3f20071eaad83a3). Exactly three complete private R-logs, 316,310 decoded messages, **zero** truncated, 3/3 recorded-commit observations matched. Only bounded aggregate fields left the analysis process.
- [GitHub Actions 44350616370](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44350616370) passed **105/105** fixture tests at commit [`c65bc49`](https://github.com/wholesalebridgeowner/sunnypilot/commit/c65bc4955b324543900526ae45106102e16ba055), including the private summary output whitelist test. Docker one-shot replay ran the pinned image with the test-gate implementation at `612a72c`; its private summary and Railway SUCCESS state are recorded separately from GitHub's full-repository test status.

## Integrity correction

Prior model-phase analysis required completed, same-segment car-state coverage and no `steeringPressed` signal. This patch adds two fail-closed conditions:

1. Both model and car-state `valid` must be **explicitly true**. Unknown is not treated as valid.
2. A starting or finishing model phase must have a known `left` or `right` direction and retain it **throughout that same phase**. A reversal or missing direction censors the entire phase **until state exit**. A legitimate transition out of that phase may report `direction=none` without invalidating the completed phase.

The bounded private-output schema reports only the additional `censoredByDirectionChangeOrUnknown` count; it does not report arbitrary direction strings, vehicle identifiers, event times or locations. This is an observational data-quality change, not a steering adjustment.

## Real-log post-gate results

| Observational measurement | Result |
|---|---:|
| Model phase observations | 3,600 |
| Qualified model starting phases | **0** |
| Starting phases rejected for driver intervention/unknown | 3 |
| Starting phases censored at log segment boundary | 1 |
| Starting phases censored for direction change/unknown | **0** |
| Qualified model finishing phases | **2** (one left, one right) |
| Finishing phases censored for direction change/unknown | **0** |
| Median qualified finishing phase duration | 1.000 s |
| Finishing phase p90 peak absolute steering-wheel rate | 2.500 degrees/s |

The real selected recordings did **not** trigger the newly added direction or unknown-validity gates. Therefore their previous qualified counts remain the same; this does **not** establish that model lane phases are true physical lane crossings. Model finishing likewise does not prove stable recentering.

For cross-checking longitudinal analysis under the same private replay: the **three** qualified following intervals still totaled 40.507 s with an observed sample headway median of 1.179 s. The no-confirmed-lead windowed acceleration derivative p90 remained 0.198 m/s³ (46.200 s across three observed episodes). These are not independently decoded OEM ACC distance targets, accelerator or brake commands, and successive samples are correlated.

## Outstanding release gates

- November 14 +171-second saturation still lacks independently confirmed matching high-resolution R-log in private inventory; no source-pinned causal finding is justified there.
- No externally documented PAM command message, OEM authorization semantics, or zero-speed LKAS third-party authority has been established. The 14.6-second factory parking flag is an OEM observation only.
- No parked, operator-approved Comma/Panda logger CPU and RSS validation or independently reviewed Comma 3 control build has passed.
- No control code, Panda safety, ECU authorization, live vehicle software or raw private telemetry was altered or published. **Development remains active, not installable.**
