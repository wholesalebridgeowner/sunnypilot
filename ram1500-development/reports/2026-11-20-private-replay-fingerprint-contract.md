# Byte-exact private replay fingerprints — independent held-out evidence (November 20, 2026)

**Status:** offline reproducibility helper and synthetic tests only. No actual private R-log has yet been fingerprinted by this helper, no independently reviewed held-out comparison passed, and no vehicle calibration or release is authorized.

## Problem being addressed

Draft PR #56 requires both a matching frozen `routeInputSha256` and a matching `eligibleWindowSha256` for baseline/candidate replay, but had no source-controlled algorithm for computing those values. Typed-in digests from incompatible input orderings or window definitions are not reproducible evidence.

This stacked draft provides a small, **pure Python, local-only, read-only** helper, `private_replay_fingerprints.py`. It does not download R-logs, parse private CAN, access the truck, communicate with Railway or write results. Its caller is responsible for running it **only in a private approved analysis environment**.

- `hash_route_inputs`: SHA-256 of the pinned recorded-build source plus ordered segment indices and separately hashed **exact byte streams and lengths**. A fixed versioned domain prefix and unambiguous length framing prevent accidental concatenation/segment-order equivalence; varying chunk sizes does not change the result. Duplicated, reordered, empty or nonbinary input fails closed.
- `hash_eligible_window`: separately domain-separated SHA-256 bound to the route input fingerprint and the exact frozen sequence of (segment index, carState ordinal, original monotonic nanoseconds). Duplicate, reordered, nonmonotonic or malformed eligible anchors fail closed.
- Only canonical lowercase 64-hex digests are returned to private callers. The helper never emits identifiers, timestamps, raw payloads, keys or per-sample values to logs or public files. **Do not commit privately computed digests**, as they can function as stable correlators.

## Reproducibility contract

In the private environment, independently verify actual source/schema commit `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, exact route and completed segment provenance, then freeze a byte-identical serialized replay-input stream for **both** the baseline and each proposed candidate. Use increasing segment indices and the same underlying binary bytes when calling `hash_route_inputs`. Different decompression/re-encoding produces a different digest by design.

Freeze eligible `carState` observation anchors *before* candidate fitting. Those anchor triples must be independently extracted/verified against that frozen input; the function does not verify that they really appear in the log. Feed precisely the same frozen selection to both paired replays and recompute hashes independently at review. Different selected samples with the same count and duration must not compare as identical inputs.

These are commitments to byte content and selection only: not proof of route authenticity, source reconstruction from the device's dirty working tree, physical lane position, model lane crossings, independent drives, controller causality, genuine independent reviewer approval or better steering. If selection changes because a candidate changes the quality gate, fail paired comparison rather than silently substitute different windows.

## Tests and boundaries

Run from the repository root:

```bash
python -m unittest discover -s ram1500-development -p 'test_private_replay_fingerprints.py' -v
python -m unittest discover -s ram1500-development -p 'test_heldout_stability_gate.py' -v
```

Six synthetic fixture methods check byte-chunk invariance, segment framing and changes, source/ordering/duplicate validation, window source/anchor binding, malformed/nonmonotonic anchors and matched frozen selection. The existing held-out-evidence GitHub Actions workflow was extended for both modules, without creating an additional scheduled job or modifying production services.

The full 11-stage plan remains active: independent multiple Comma-`latActive=true` route measurements, physical lane tracking and recentering, matched factory ACC observation, route-held-out stability and oscillation regressions, independent review, approved parked diagnostic acceptance, Panda/OEM safety and supervised vehicle tests remain mandatory. No numerical tuning, on-device installation, CAN actuation or safety-control change is in scope.
