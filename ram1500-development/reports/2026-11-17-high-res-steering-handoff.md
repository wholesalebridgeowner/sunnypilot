# November 17 high-resolution steering torque investigation — execution handoff

Status: **R-log availability verified; Python pinned offline analyzer and synthetic tests complete; real numerical analysis pending execution on a machine with the R-log and Python/pycapnp**.

## Verified input
- Moving route suffix: `000003d8--72cb4c20bf` (November 17, 2026), 36+ minutes, previously read by Claude on device.
- Saturation cluster in earlier **10 Hz Q-log** read-only scan at approximately +1801.4 to +1806.0 seconds, around segment **30**, without a `steerSaturated` alert.
- Private Railway S3 inventory independently confirmed **R-log and Q-log objects for segment 30**, with R-log compressed size **9,994,172 bytes**; adjacent segments 29 and 31 also present. Do not commit or link the private object keys, presigned URLs, VIN, device ID or raw frames.
- The recorded driving build is `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`; use Claude's `ramtools/schema.py` verified drive-build schema.

## Code delivered
- `ramtools/torque_cap.py`: runs at raw 100 Hz with a configurable cap threshold and temporal gap, and reports paired command, CAN output, EPS motor response, speed and modeled lateral acceleration errors.
- `investigate_torque_caps.py`: one-command, private local input and private anonymized JSON output outside the repo.
- `tests/test_torque_cap.py`: verifies no-alert cap runs, car-side clipping, past-only joins and separation of distinct cap episodes.
- GitHub Actions CI `Ram pinned comfort fixture tests` **passed** at `7fdca94e29df3a043d845b358736ca825e0694b4`.

## Run plan for Claude's authorized Windows SSH machine
1. Read this branch `ram1500-pinned-comfort-20261117` and PR #2; review any source changes and run fixture tests.
2. Retrieve *only* segment 29/30/31 R-logs from the device while parked or via the authorized private log store. The R-log must be transferred into a private Windows analysis folder, not a Git repository. If the older on-device files have rotated, the Railway bucket has them.
3. Use the pinned schema download/verification workflow from Claude's `investigate_route.py` and `ramtools/schema.py`. Avoid importing fork-master and pinned `capnp` schemas into the same Python process.
4. Run:
```bash
python ram1500-development/investigate_torque_caps.py \
  --schema-dir /private/schema-1f7233cb \
  --out /private/analysis/nov17-cap.json \
  /private/rlogs/000003d8--72cb4c20bf--29/rlog.zst \
  /private/rlogs/000003d8--72cb4c20bf--30/rlog.zst \
  /private/rlogs/000003d8--72cb4c20bf--31/rlog.zst
```
5. The summary intentionally does not expose absolute monotonic time or location. Compare proposed mechanism hypotheses: command vs applied CAN, EPS feedback, steeringPress/driver intervention, lateral acceleration error, and data ages. Do not infer physical EPS torque limits from the normalized 350-unit source limit.
6. Publish only aggregate findings, confidence and necessary engineering limitations in the repo.

## Separate optimized logger retest
- The passive sidecar capture ran for 2 minutes while parked. It used **21.6% of one CPU core**, above the 15% acceptance bar, and showed rising RSS.
- The current logger branch `ram1500-steering-forensics-20261117` contains optimizations and a private-vs-shared RSS split; only offline benchmarks exist for those modifications as of this report.
- This is a *separate* parked-only, operator-reviewed gate: check msgq slots before each session and monitor real Python CPU/RSS; no driving capture without separate approval.
- Do not change `/data/openpilot`, invoke `sendcan`, publish raw data, bypass safety, or install a persistent/autostart daemon.

## Remaining block
The Railway analyzer function currently runs **Bun only** and has neither Python, pip nor a Cap'n Proto compiler. Therefore it cannot execute the pinned Python analyzer directly. This is a tooling/runtime limitation, not missing R-logs; use the authorized Windows PC/Claude or an independently provisioned isolated offline Python environment.
