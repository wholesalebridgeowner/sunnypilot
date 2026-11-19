# November 19 — privacy-safe failure taxonomy for quarantined Q-log bounded trials

**Scope:** Offline evidence diagnostic only. Draft PR #36 stacked on PR #35 at immutable parent `9155b954c07068d119fb9f7f735e6cc32c2897c7`. Neither prior quarantine nor any route's accepted `latest.json` is changed. No vehicle software or control authority is affected.

## Why the prior result is not a success

A dedicated, source-pinned private Q-log clock audit documented non-header timestamp inversions *within* individual recording segments, not simply replayed `initData` metadata. On the first two existing private bounded-trial executions, 66- and 9-segment records produced **`bounded_trial_rejected`**, each with the generic `bounded_order_or_source_invalid` reason. These are **not** validated route reports or controller-replay results. The 0.5-second adjacent-event inversion bands from earlier diagnostics do not prove that the maximum timestamp lag behind the running watermark stays below 2 seconds, nor that cross-segment boundaries are monotonic.

## Exact change

`auto_review_job.safe_trial_rejection_reason` accepts **only known, hardcoded internal exception tokens**, coarsening them to source, timestamp validity, lateness, event-time regression, buffer ceiling, and internal-contract categories. Unexpected error text is categorically rejected in favor of a constant generic label. The selected reason is stored only in the existing private per-fingerprint bounded-trial result and printed without any route identifiers; it never promotes a quarantined observation into a baseline comparison. Other decode/runtime exceptions still get the generic `decode_or_metrics_invalid` label. No extra raw frames are uploaded, no per-sample timestamps or payloads are published, and prior result artifacts are unchanged.

This **does not** establish why the first two bounded trials failed. Those older trial results used the previous generic format and are not retroactively relabeled. A new real-route trial must finish under this exact source before its specific failure category is considered independently verified.

## Synthetic validation and provenance

- Code commit: `ed1fef76add23437944107d27e98eb6aa6ca73ca`.
- Regression-test commit: `d0e517252dd8b02be5e6b74c7242e159e0f9b390`. Added allowlist and arbitrary exception-text privacy checks plus private-result/quarantine integration fixture.
- GitHub Actions [dedicated automated review #44584015880](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44584015880): **success, 22 tests** on exact code/test commit.
- GitHub Actions [Ram telemetry analyzer #44584015914](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44584015914): **success, 168 tests, one skipped** on exact code/test commit.
- The isolated Railway **research reviewer only** was source-pinned to the exact test commit. Deployment `478be46e-473f-4b94-8a34-90e135dd245c` reached **SUCCESS**.
- **Actual private runtime at 2026-11-19 UTC:** A previously quarantined 54-segment recording was processed by that exact deployment and returned `bounded_trial_rejected`, `reason=event_clock_regression` in a sanitized `RAM_AUTO_REVIEW` line. This is an independently observed, source-pinned **rejection result**, not a passing route review. The category intentionally merges two failure modes: (a) a cross-segment timestamp regression or (b) a violation of the emitted-event watermark ordering. It does **not** identify which one occurred or prove a specific ECU/logger fault, nor disclose route IDs or raw timing.
- This rejection is consistent with keeping that recording quarantined and leaving private `latest.json` untouched; it does not establish any driving comfort metric. Older 66- and 9-segment bounded trial failures retain only their earlier generic rejection category and are **not** retrospectively reclassified. A future independent, privacy-safe boundary-versus-watermark audit would be needed to narrow the 54-segment cause.

## Remaining limitations and release gates

No successful ordinary Q-log driving report or independent held-out controller scorecard has been established by this patch. The original November 14 high-resolution R-log steering saturation remains unavailable; observed factory ACC headway is not the selected gap or throttle/brake command; OEM PAM-to-EPS control ownership, parked Comma CPU acceptance, independent safety review and supervised vehicle validation remain open. Keep the baseline and Claude branches untouched; no OEM/Panda bypass, CAN injection, device install, steering/throttle/brake actuation or vehicle-ready calibration is authorized.
