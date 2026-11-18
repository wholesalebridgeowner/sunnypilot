# November 18 — anchored lane-model re-entry after unobserved intervals

**Status:** Verified offline analyzer correction and private read-only replay. Research-only; no vehicle-control software, Panda safety, OEM authorization, or hardware changed.

## Scope, source and coordination

- Repository: `wholesalebridgeowner/sunnypilot`, draft PR #11, stacked on draft PR #10. Existing `ram1500-custom-development`, Claude's review branch, PR #1 PAM/steering branch and PR #2 remain unchanged.
- Source modifications: `ram1500-development/ramtools/lane_phase_quality.py` commit `3ff35e3aa0546311b7e459b8b26b006945c211da`; regression fixtures commit `d50b5df64abd0e26d9246827dc221f6740a57302`; CI branch inclusion `698d250900bd313250f48015ca35a096db5da3d9`.
- Historical installed drive-build decoder pinned to `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. The on-device dirty working tree has **not** been independently validated.

## Defect and conservative fix

Previous phase-interruption handling rejected the *same* active model phase reappearing after a message gap, invalid observation or segment boundary. However, if the next readable model sample already reported a **different** active state, such as `laneChangeFinishing` after a missing `laneChangeStarting` transition, the analyzer could mistake the remaining portion of that phase for a fully observed entry and publish its comfort metrics.

The new read-only analyzer requires an observed, valid `off` or `preLaneChange` state to re-anchor model chronology after an interruption. Neither unknown states nor a different active lane-change phase qualify as this anchor. Previously censored states remain excluded, and the analyzer counts previously unobserved alternative-phase entries under `censoredByUnverifiedEntry`. A subsequent new phase entered after a verified neutral state can still qualify. This is observational model-phase accounting, not actual wheel/vehicle crossing or a steering change.

Four added synthetic tests cover a different active phase after a model gap, a different active phase after a segment boundary, a genuinely new phase after a verified neutral anchor, and unknown states that cannot reset the gate. Two prior fixtures were updated to reject unverified data or provide a verified same-segment neutral anchor.

## Verified validation

- **GitHub Actions:** [Ram pinned comfort fixture CI run 44383024850](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44383024850), SUCCESS on exact tested commit `698d250900bd313250f48015ca35a096db5da3d9`. **116/116** pinned comfort fixtures passed; manifest and non-installable parameter experiment steps also passed.
- **Railway isolated private one-shot:** deployment `26c3b6f7-99fd-4bd4-80e9-1b6147646b30`, SUCCESS on **the same commit**. Docker build ran **77/77** targeted tests and then decoded **316,310 messages** from **three completed private R-logs**, with **0 truncated** and **3/3 source-commit observations** matching the historical pin.
- **Actual replay outcome:** No newly rejected post-gap *different-phase* entries in these selected three R-logs. The qualified model `laneChangeStarting` count remains **0**; completed starting transitions **2**, both rejected for steering override/unknown. Qualified `laneChangeFinishing` phases remain **2**, one left and one right, with median observed duration **1.0 s**. One starting phase remains censored at a recording boundary.
- **Longitudinal controls comparison:** following episodes remain **3** totaling **40.507 s**, median observed sample headway **1.179 s**; no-confirmed-lead observed acceleration-derivative proxy p90 remains **0.198 m/s³**. These measurements are *not* OEM ACC programmed distances, issued throttle/brake commands, or independent drive trials.

The private replay has not demonstrated the new invalid-history pattern; the regression coverage is synthetic and anticipatory. No per-sample data, GPS, exact timestamps, route identifiers, VIN, raw CAN frames, S3 keys or private credentials are published.

## Limits and remaining release gates

This gate handles **interruptions after an observed model history**. An active phase already present in the first available model record still lacks a separately verified entry and remains a future coverage issue. Neither a completed logged model phase nor steering-wheel rate demonstrates physical lane-boundary crossing or comfortable recentering; the reported steering-rate derivative is *not* chassis lateral jerk.

Outstanding: Nov 14 +171 s saturation high-resolution verification (matching R-logs not yet confirmed), Nov 17 torque-limit causal attribution, factory ACC actuation ownership, PAM→EPS command identity and exact vehicle bus topology, parked Comma resource profiling with operator consent, reproducible Comma 3 software build, independent safety review and hardware validation. No installation candidate or installer URL is authorized.
