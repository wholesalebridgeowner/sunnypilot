# Nov 19 — source-pinned investigation of private Q-log quarantine cascade

**Research-only, not a vehicle release or recovered driving metric.**

## Trigger: independent private runtime evidence

Railway's existing `ram-scheduled-route-review` service, source-pinned to `c1488a2b012a83636ec495e4f3e4d29ed92e5234` and deployed in `21e1ef32-37f8-4a3e-b4b6-1d509e0566aa`, did stop crashing. But the first **nine** complete observed post-fix invocations returned `route_quarantined / decode_or_order_invalid` (segment counts 66, 9, 54, 2, 1, 3, 14, 29, 1), without any verified `review_saved` result. This is a **real pipeline reliability problem**; success of the Docker build and CI did not validate its private-route computation. None of those generic categories establishes why the recordings failed. In particular, excluding repeated `initData` clock headers did not recover these routes.

The original branch and vehicle remain unchanged. Private S3 objects and their identifiers are not disclosed. Runtime logs provided only anonymous status, segment counts and a fixed failure class.

## Isolated diagnostic, draft PR #34

- Starting parent: draft PR #33, `9d00b4c28c61e0f0819c5dafab78daebcb7c4104`.
- Tested source: `a0faf506eb9b021c7c78b78aae2b4e8b72eab815`.
- New `auto_review_clock_audit.py` performs constant-memory, source-pinned inspection of timestamp order on one **previously quarantined** private Q-log fingerprint per scheduled execution. Every selected segment must publish an exact `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` recorded-source header. Historical `initData` message time is excluded from motion timing.
- Diagnostics count non-header timestamp inversions over 500 ms, distinguishing within-segment from cross-segment occurrences and coarse size bands. The scanner never records precise timestamps, route names, GPS, VIN, CAN payloads, driver paths or decoder exception messages. The audit is **not** a sort, fix, replay result or source of vehicle commands.
- Existing quarantines are not lifted. The audit writes separately to a private, hashed fingerprint prefix; it selects one prior quarantine before consuming more unexamined routes. A source mismatch or reader failure is reported as a fixed category, not an untrusted error string. The diagnostic never presents a failed route as qualified driving evidence.
- Synthetic tests cover repeated old metadata timestamps, actual clock inversions inside and between segments, exact source rejection, privacy and irreversible quarantine isolation. No additional GitHub Actions workflow was created.

## Independent validation

GitHub Actions on the **exact code SHA** `a0faf506eb9b021c7c78b78aae2b4e8b72eab815`: [Ram automated review offline tests](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44579540286) **SUCCESS**, and [general Ram offline analyzer tests](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44579540375) **SUCCESS**. This verifies synthetic behavior only. The Railway research service was repinned to the same exact code/test commit in deployment `8414ff7a-ec5a-47f5-9c98-bdc33220fd2d` (**SUCCESS** build). These green statuses do **not** establish a completed real-route audit or successful saved drive review.

## Gate status and follow-up

Inspect anonymized `RAM_AUTO_REVIEW` runtime and the private diagnostic record after a completed cron execution. If the failure is genuinely non-header timestamp inversion, determine magnitude/boundary prevalence before proposing an isolated, bounded and explicitly provenance-preserving observation reorder. If there are no inversions, investigate schema/reader and the other rejected record types without bypassing the source or safety gates. Existing quarantines must remain invalid until corrected analysis is independently tested against private source-verified recordings.

No modifications to the production `comma-log-sync` collector, Comma, Panda/OEM safety, CAN injection, controls, raw telemetry, public installer, or baseline `ram1500-custom-development` branch. Nov 14 high-resolution saturation, held-out steering validation, OEM ACC actuation, PAM→EPS command ownership, parked device performance and independent release acceptance remain blocked.
