# November 18, 2026 — isolated pinned ACC/lane R-log audit

**Status: offline research, not a vehicle control candidate.** This work is intentionally
stacked on PR #3 → PR #2 → Claude's independent review, separately from PR #1
steering/PAM investigations. The primary \`ram1500-custom-development\` branch is unchanged.

## Why this additional analysis exists

Synthetic following-distance, acceleration and lane-change fixtures are passing,
but they cannot establish the actual distributions on the November 17 drive.
This read-only job connects the existing private Railway object store to
\`ramtools\` and emits bounded aggregates only. It does **not** control the vehicle,
connect to the Comma, or observe factory throttle/brake commands.

## Exactly pinned provenance and reproducible checks

- Recorded-source target: Sunnypilot \`1f7233cb983f3c13fdbba47a86f8fbc796ad9211\`,
  with reported **dirty** on-device working tree. No claim that source exactly
  matches all installed runtime files.
- \`Dockerfile.comfort\` shallow-fetches that exact historical Git commit's cereal
  directory. The independent historical \`car.capnp\` vendor copy is downloaded
  from immutable fork commit \`a392b782e3975ea5f1e1262743a7580200fb7686\`
  and verified against its original Git blob
  \`d237e353d549800b2db6aff58f313e89799df958\`.
- \`ramtools.schema.verify_schema_dir\` checks *all five* recorded-build schema
  hashes before Python/Cap'n Proto decoding. Unknown/mismatched build markers
  are reported as unverified, not silently treated as matched.
- Python and dependency versions are pinned in \`Dockerfile.comfort\`. Building
  the container requires public GitHub/PyPI access but **no private log keys**.
- [GitHub Actions fixture + Docker build](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44328049793)
  **PASS** at \`a699b71585fab8c5733a68ac7dd7908d2b72767b\`:
  45 synthetic fixtures passed on the host; Docker independently passed all
  45 and source/schema integrity checks. No real R-logs are used by CI.

## Private, one-shot execution

The job \`private_comfort_rlog_probe.py\` uses environment-only Railway S3
credentials, a private eight-hex route selector and an explicit comma-separated
list of at most four completed segment numbers. The initial focus is three
adjacent November 17 high-resolution segments centered on the previously
measured controller torque-cap activity; this is only a **three-minute
sample**, not the full drive.

Object selection fails closed for missing, duplicate, ambiguous, oversized or
incomplete inputs. Each selected file is downloaded into an owner-only
temporary directory and deleted when finished. File paths are synthetic
(non-identifying) so the decoded records retain segment boundaries without
exporting real route keys. A failed decode stops the job before reporting
quality measurements. The container has no public port, cron or restart loop.

The **only intentional output** is one JSON line prefixed
\`RAM_PRIVATE_COMFORT\` with fixed-key aggregates: source match gate,
record/sample counts, episode continuity quality, observed headway and
ACC-on acceleration / finite-difference jerk, and model-phase steering
rate/derivative summaries. It emits no device identifier, original object
key, VIN, GPS, frame payload, absolute timestamp or individual event sequence.
Unknown model direction strings are collapsed into an \`other\` count.

## Interpretation / release boundaries

1. \`carState.aEgo\` measures *estimated observed motion*, not factory ACC
   commanded acceleration, throttle or brake activity. The factory ACC target
   headway setting is not reconstructed. \`radarState\` lead context is an
   openpilot perception observation, not proof of the OEM ACC controller's
   selected target.
2. A lane model's \`laneChangeStarting\`/\`laneChangeFinishing\` label does not
   independently establish crossing a physical road marking or settled
   vehicle position. Steering-rate derivative is not chassis lateral jerk.
3. A few minutes from one drive have serially correlated observations, not
   dozens of independent comfort experiments. No actuator tuning proposal
   is justified until robustness across routes, logged modes, and controls
   is established.
4. The November 14 +171-second \`steerSaturated\` Q-log event remains a
   separate incomplete R-log and controller-runtime provenance question.
5. No installed Comma software, brake/throttle/steering settings, Panda safety
   gates, ECU permissions or factory parking commands are touched.

Independent review, actual-hardware build testing, safety case and full
replay gates remain open. Do not publish a Comma installer URL.
