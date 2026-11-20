# Held-out replay source-pairing gate (November 20, 2026)

**Status:** offline, synthetic regression validation only. No active truck/controller change, no new private route replay and no calibration authorized.

## Safety finding and scope

The held-out evidence gate introduced in draft PR #52 compared baseline/candidate `eligibleSeconds` and `eligibleSamples` and required a caller's `identicalReplayInput` attestation. Those two aggregate numbers do **not** establish that the *same observations* or identical source R-logs were compared. Two disjoint windows can have equal duration and row counts. A repeated recorded route under a different opaque token can appear to be an independent held-out route.

This draft adds an explicit v2 **offline pairing obligation**, stacked only on PR #52: every development route must carry a local `routeInputSha256`; every held-out baseline and candidate must carry matching `routeInputSha256` **and** matching `eligibleWindowSha256`. SHA-256 values must be 64-character canonical lowercase hex. The gate rejects missing, invalid or unequal paired hashes and rejects repeat input hashes across development and held-out cohorts. All prior steering-oscillation, overshoot, lane-centering, clipping, intervention and physical-lane-crossing checks remain mandatory. The gate never exports any hash, token, sample, recording identifier or timestamps. The status remains only `OFFLINE_EVIDENCE_ELIGIBLE_FOR_REVIEW` at best, with `calibrationAuthorized=false` and `vehicleReleaseAuthorized=false`.

**Private reproducibility contract:** `routeInputSha256` is the digest of the exact, frozen, byte-identical replay input stream (including pinned source and segment scoping) prepared and retained privately for the relevant route. `eligibleWindowSha256` is the digest of the exact qualified observation/window-selection representation (canonical ordering and route-specific provenance) from that replay. Baseline and candidate must use exactly the same input and eligible observations. An independent reviewer should independently recompute both hashes inside the private environment, verify unaltered contents and freeze the hold-out selection before candidate fitting. Do **not** commit these input digests or the source material to public GitHub.

## Limitations / independent validation

An operator can type a fake, syntactically correct digest. This validation **does not attest original logs, prevent fabricated summaries, prove independence across actual driving conditions or replace source-pinned R-log replay**. Hash equality alone does not establish sample comparability if different canonicalization procedures were used. Real qualified physical lane tracking/crossing measurements, multiple independent active routes, paired baseline-vs-candidate simulation with frozen windows and independent code/data review remain outstanding. The prior source-pinned morning route provided only 18.859 seconds of strictly eligible reversal-proxy observations, insufficient for release. No `latAccelFactor`, friction, steering-delay or control parameter is proposed.

## Regression verification

Run from repository root: `python -m unittest discover -s ram1500-development -p 'test_heldout_stability_gate.py' -v`. The existing GitHub Actions held-out-evidence workflow covers this test module on draft-PR changes; no new workflow was added. Added adversarial fixtures for equal-count/different-window inputs, mismatched input streams, malformed/uppercase/missing hashes, duplicate actual inputs under different route tokens and aggregate output privacy. These fixtures use fabricated digest strings only. Do not describe a pass as a matched real-road calibration validation.

No active software, CAN, Comma/Pi diagnostics, EPS/Panda/OEM configuration, private collector or Railway service was changed.
