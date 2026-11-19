# Full-length recorded-build source provenance gate — offline safety evidence

**Date:** November 18, 2026. **Scope:** private read-only Ram R-log research only; no vehicle or controller change.

## Why this matters

Prior `private_full_route_cruise_audit.py` used `EXPECTED_SOURCE.startswith(c)` when comparing each R-log `initData.gitCommit` with the source-pinned Sunnypilot commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. This allowed a *truncated or even one-character matching prefix* to be reported as an exact-source match. No evidence indicates the real recorded metadata was actually truncated; the existing source check simply failed to establish the length and whole-hash identity it claimed.

The change requires every observed build commit string to equal all 40 lowercase hexadecimal characters of the expected source commit. Missing, altered, additional, abbreviated, non-text or mixed `initData` source strings now fail closed with fixed privacy-safe errors. The test helper returns only an aggregate count. The replay still confirms recorded **git commit metadata**, **not** whether the installed working tree was dirty, the exact executable bytes or whether any drive was safe.

## Changed files and test contract

- `ramtools/recorded_source_integrity.py`: explicit full-hash predicate; no raw input values in errors or results.
- `private_full_route_cruise_audit.py`: replaces permissive prefix check at the actual R-log decode boundary.
- `tests/test_recorded_source_integrity.py`: synthetic exact-match, abbreviated-prefix, malformed suffix, mixed-header, invalid expected-configuration, privacy and entry-point cases. No vehicle identifiers, locations, raw logs or credentials in test fixtures.
- `Dockerfile.comfort`: includes exact-source checks in the private Railway container build.
- `.github/workflows/ram-source-integrity-tests.yml`: targeted PR/push synthetic regression workflow.

The exact-source gate is deliberately stricter than historical behavior. The earlier 20/20 and 54/54 metadata-match statements were produced with the permissive prefix predicate; they should be treated as **provisionally pinned** until this exact-match implementation is executed against the same archived R-logs.

## Verification and interpretation

The standalone synthetic provenance suite independently passed **15/15** local pytest parametrized cases before the integration entry-point fixture was added. The committed entry-point fixture and full code path require subsequent CI/private container execution; no additional success is presumed at document authoring. A private rerun must verify actual source lengths, complete segment counts and unchanged historical state/steering aggregates, reporting only anonymous summary counts. A fail-closed result means the source cannot be verified with the current metadata, not that the original recording or driver is wrong.

This integrity correction does not solve the missing November 14 +171-second high-resolution steering event, morning early segments 0–26, factory ACC actuation attribution, physical lane tracking, independent steering-delay calibration, PAM-to-EPS command ownership, parked logger CPU acceptance or any vehicle safety/release gate. The afternoon cruise-off recording and late-morning cruise-active recording must remain distinct.

**No new numeric calibration, CAN transmission, OEM authorization bypass, device install or installer is approved by this change.** Original development and concurrent PR branches remain unchanged.
