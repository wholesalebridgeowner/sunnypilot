# Ram steering-rate reversal proxy — neutral/dwell continuity correction

**Date:** November 19, 2026. **Status:** verified offline research correction; not a controller change or install approval.

## Source/provenance

- Stacked branch: `ram1500-steering-neutral-contiguity-20261119`, based on draft PR #21 (head `0371900aa9d924278b4983a241d4dfc8a2294417`); original `ram1500-custom-development` remains unchanged.
- Changed observational detector: `ram1500-development/ramtools/lateral_stability_observer.py`, commit `cafe4fc5b3a095016c1bca3b7a5665c6b1cf0d29`.
- Synthetic regression additions: `tests/test_lateral_stability_observer.py`, commit `ac2cb41b25866df4eff73836d7876512f7057f23`.
- Independent pinned Docker run: isolated private Railway `ram-full-route-cruise-private-once` service, deployment `private-railway-id`, built from Git commit `6d3dfbb18d0c2cdbc66b68a4e0f8d2f7b32511f9`. Successful Railway build reported **127 passed in 0.28s**; deployment status **SUCCESS**. GitHub Actions has not independently reported a completed run for that commit; Docker build success is not GitHub Actions success.
- Same 20 previously retained private morning R-logs, each with a full 40-character recorded-build `initData.gitCommit` match to `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. The recorded source working tree and installed executable remain unverified. No new independent driving route was acquired.

## Identified measurement defect and correction

The previous detector kept the start of a >1.5-degree/s sign streak through arbitrary periods when the steering-wheel rate dropped below threshold. A single positive sample followed by a distant positive sample could therefore be falsely treated as >=250 ms of sustained positive steering; a multi-second near-zero interval followed by sustained negative steering could be incorrectly counted as a local sign reversal. This could **overstate** steering oscillation in future logs.

The corrected *read-only* proxy (1) requires consecutive above-threshold rate observations lasting >=250 ms to confirm each direction, (2) resets directional dwell on every near-zero observation, (3) allows a confirmed opposite direction to form a local reversal only if the previous confirmed direction was observed within 1 s, and (4) preserves existing segment, CAN/vehicle validity, operator override, steering torque and time-gap gates.

The 1-second bridge and 250-ms dwell are **detector definitions**, not measured physical steering-response values and not proposed controller tuning. A strict proxy can miss slower oscillations; only true physical lane tracking and independent good-quality drives can validate steering comfort.

Synthetic regressions established that a 3.5-s neutral interval no longer creates a false reversal, two isolated same-sign spikes do not falsely satisfy directional dwell, and a 0.2-s zero crossing between sustained opposite-rate periods still produces the expected one reversal. Existing tests exercise driver override, invalid CAN, future or stale source, torque clipping and segment boundary behavior.

## Verified private rerun — same morning data, not held-out

The complete source-pinned isolated private replay decoded **20/20** selected contiguous R-log segments with **2,005,758 messages**, **20/20 full recorded source matches**, no truncated files and no raw-identifying telemetry published. Its new steering proxy result remained unchanged from the previous run:

| Qualified steering observation | Actual pinned result |
|---|---:|
| Qualified samples | 1,903 |
| Qualified continuous exposure | 18.859 s |
| Separate continuous runs | 17 |
| Runs >=2 s | 4 |
| Longest run | 5.889 s |
| Exposure in runs >=2 s | 14.549 s |
| Confirmed sign reversals under corrected strict proxy | 0 |

Unchanged factory cruise source reconciliation on the *morning* retained window: **114,076 carState observations**, **10,861 cruise enabled**, **10,764 Comma lateral-active**, **57,006 raw bus-2 DAS_3 frames**, and **114,011 past-only decoded raw-vs-CarState comparisons with zero discrepancies**. These counts distinguish positive morning ACC from a separate afternoon cruise-inactive route. No independent factory throttle/brake command measurement is implied.

**Interpretation:** This specifically corrects an observational false-positive mechanism, and the existing 20-segment morning summary did not change. A zero under this detector does **not** establish lack of lane-centering ping-pong or better damping. This is not a held-out comparison and should not be used to set a numeric actuator delay, torque ceiling or lane-change parameter.

## Open gates / no live changes

Still missing: November 14 +171-s matching high-resolution R-log; root cause of November 17 segment-30 cap; independent held-out steering/lane tracking; factory ACC actuation attribution; exact OEM PAM-to-EPS CAN-C ownership/status semantics; owner-approved **parked-only** logger CPU/RSS measurement (earlier 21.6% of one core exceeded 15% threshold); independently reviewed reproducible control build, Panda/OEM safety acceptance and supervised closed-course assessment.

The original development branch, Claude's concurrent branches, production `comma-log-sync`, bucket contents, installed truck software, steering/brake/throttle control and OEM/Panda protections were not changed. This work used the existing isolated private Railway analysis service only. No installer has been approved or produced.
