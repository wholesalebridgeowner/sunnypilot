# November 19 — source continuity and fail-closed recovery of the private scheduled Q-log reviewer

**Status:** Offline fixture and GitHub CI verified; Railway image built successfully at the exact tested source. A completed real-route aggregate report is **not yet verified**, so this is not an end-to-end passing pipeline, calibration or vehicle release.

## Live failure that motivated the repair

Before the patch, the five-minute Railway research worker `ram-scheduled-route-review` repeatedly crashed at source `9406572cd977b80e612292581671aa3f07855238`. The captured sanitized Python exception was `ValueError: non-monotonic route: inspect route ordering/schema` in `RouteMetrics.ingest`. Failed runs included deployment `private-railway-id`. The exception stopped the queue without saving a report; each invocation could reselect the same private route. No conclusion about steering quality, source validity or route integrity follows from this failure alone.

Historical pinned Ram R-log investigations already identified that a repeated `initData` message can carry an older process timestamp than the surrounding segment. That makes replayed metadata a plausible cause of the monotonicity exception, **not a proven diagnosis for the particular Q-log**. This patch does not waive ordering checks on actual motion data.

## Exact changes (only read-only post-upload research)

- Verify each selected Q-log segment's own `initData.gitCommit` exactly equals the full recorded-build SHA `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. Missing, mixed, shortened or contradictory source metadata fails closed. Do not confuse source-commit agreement with a verified clean/identical running working tree.
- Exclude only the verified `initData` metadata messages from chronological vehicle/steering measurement; keep rejecting any actual non-monotonic non-header data.
- If source/decoder/ordering fails, store a private, fingerprint-scoped `quarantined` record containing fixed reason categories **without** exception messages, route strings, raw telemetry or credentials. Future five-minute runs skip that exact fingerprint while still reviewing eligible older/newer routes. This can also quarantine genuinely missing or bad data; it is not evidence of successful decoding.
- Count `onroadEvents` episode starts only on an **observed absence-to-presence transition**; nominal 1.002-second heartbeats are not new episodes. Missing heartbeat observations cannot establish true uninterrupted episode duration.
- Never compare an updated recording of the same route to its prior partial fingerprint as if it were an independent drive; remove misleading, effectively unreachable `largeDifference` flags and state Q-log subsampling limitations.

## Tests and provenance

- Parent (PR #31): `9406572cd977b80e612292581671aa3f07855238`; isolated draft PR #33. The owner-maintained `ram1500-custom-development`, Claude's branches and vehicle were not changed.
- Corrections: `43083560a0b620af11057231f3b0406cf07c7f83` (alert transitions / comparison semantics); `db8aa4515c1b682ce48b6ce26e3136df6e7b9275` (per-segment source/clock/quarantine); `4ba2ba5b5d8533911f9e707bbccc02dfc4521991` (synthetic tests); `c1488a2b012a83636ec495e4f3e4d29ed92e5234` (legacy fixture correction).
- On exact tested HEAD `c1488a2`, GitHub Actions [dedicated private-review tests #44569382486](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44569382486) **SUCCESS** (12 synthetic tests), and [Ram analyzer workflow #44569382499](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44569382499) **SUCCESS**.
- The previous code/test SHA `4ba2ba5` failed one old test because it assumed a repeated alert after silence with no intervening `onroadEvents` empty observation; the fixture now explicitly includes the clearance event and passed on the new SHA.
- The existing Railway read-only reviewer was source-pinned to exact `c1488a2`, image deployment `private-railway-id` built and reached **SUCCESS**. A successful build/deployment is **not** proof of actual private route completion or accurate route aggregates; verify `RAM_AUTO_REVIEW` runtime and stored private output before advancing that gate.
- No changes to the separate `comma-log-sync` collector, private raw object payloads, Comma or OEM devices, CAN transmission, Panda safety, brake/throttle/steering controls, or a public installer.

## Remaining gates

Private end-to-end worker runtime and freshness check; compare quality against already-verified private R-log observations without treating 10 Hz/2 Hz Q-log measures as equivalent to high-resolution R-log measures; independent route-held-out steering stability and ping-pong measures; Nov 14 +171 s high-resolution saturation R-log; OEM ACC actuator provenance; OEM PAM-to-EPS command ownership; user-approved parked logger CPU/private RSS acceptance; independent supervised safety review. Neither this report nor green CI authorizes a numerical calibration, hardware action or vehicle release.
