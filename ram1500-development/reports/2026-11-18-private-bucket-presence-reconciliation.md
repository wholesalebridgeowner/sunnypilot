# November 18, 2026 — private-bucket R-log presence reconciliation

**Scope: read-only private S3 listing plus independent synthetic fixture tests.** This is an object-presence check, **not** a replay, a decoded CAN finding, a controller change, proof of cloud-wide absence, or a vehicle safety authorization. No raw log, object key, device identifier, VIN, route token, or location appears in this report.

## Source / provenance

- Historical actual driving-build source pin for any later decode: sunnypilot commit 1f7233cb983f3c13fdbba47a86f8fbc796ad9211 (reported dirty working tree remains unverified). No schema or private log was decoded in this audit.
- Existing **private** Railway project: ram-drive-log-bridge, private bucket ram-drive-logs.
- Existing read-only one-shot Railway function: ram-private-log-presence-audit, deployment b0981f35-0238-4381-b141-0d359be5e8b4 on November 18. Its completed JSON appeared in **structured log attributes**, not the empty message field; reading only message strings can incorrectly suggest that no inventory result was produced.
- The listing used prefix logs/. It reported **1 completed page**, **612 object keys**, **0 unclassified keys**, **11 distinct route groups**, and **no pagination truncation**. This is a point-in-time view of this specific bucket and prefix; it is not the entire Comma cloud archive.
- The separately published historical Nov 14 Q-log analysis decoded 51/51 segments from another data path. **Do not confuse that earlier Q-log access with current presence in this Railway S3 bucket.**

## Presence result — exact scope

| Target and question | Private inventory result | What it establishes |
|---|---|---|
| Historical **November 14** saturated-alert drive, focus segment 2 | **0 matching route groups** in this bucket's completed logs/ listing | The requested focus R-log cannot be read from *this bucket in its current listed contents*. It does **not** prove that Comma cloud, device storage, another bucket, or an earlier Q-log copy lacks the recording |
| **November 17** steering-cap drive, segment 30 | **1 matching route group**, 45 distinct Q-log segments and 45 distinct R-log segments (0–44), including segment 30 | The selected private objects are present. Presence alone does not establish file integrity or that a particular controller mechanism caused the measured cap |
| Expected initial Nov 17 coverage window, segments 0–35 | **36/36 Q-logs and 36/36 R-logs present**, plus 9 additional segments of each kind | The earlier 36-segment requirement was a minimum coverage window, **not** the total route length |

**Important:** the Nov 17 controller finding from an earlier independent private decoder remains: 219/700 normalized request samples reached abs(output) ≥0.995 during the focused 1800–1807 s window, while the observed native LKAS output maximum was 280 against a 350-unit historical reference. This listing did **not** remeasure those values and did not independently determine an EPS hardware cap or controller phase.

## Reproducible offline privacy gate

New ram1500-development/storage_presence_integrity.py consumes an **in-memory private listing** and a private short target selector. It returns *only* counts and conservative status strings; raw input keys, route identifiers, object names, dates and locations are never returned. It does not access S3, CAN, the truck, or any network. Call it only in a trusted private workspace; never commit its inputs.

It distinguishes a complete scoped no-match from an incomplete paginated listing, detects ambiguous multiple matching routes rather than picking one, deduplicates segment extensions, validates selectors and segment arguments, rejects malformed objects, and reports missing/extra segment counts. Synthetic tests contain **no real route identifiers**. This Python helper is a reproducible independent check of listing interpretation, **not** evidence that it processed live Railway data in this run; the live listing came from the existing Bun function.

Validation: **10/10 local synthetic unittest cases passed** before commit. New independent GitHub Actions workflow runs the same test suite on Python 3.12. Check the run for the final report commit separately before calling CI successful.

## Next safe steps / release block

1. For the Nov 14 +171 s event, use the existing authorized Comma/Athena upload pipeline to *check availability* and, **if genuinely available**, privately collect only the relevant completed segment 2 and adjacent segments. Verify actual object arrival, downloaded decompression, and 1f7233cb schema before attributing the alert. A previously accepted upload request does not confirm a stored R-log.
2. Keep the Nov 17 normalized-vs-native ceiling, variable controller timing, and OEM auto-park evidence distinct. Rate-limited retrospective torque matches are observational and not a causal phase diagnosis.
3. The Nov 18 factory parking finding remains one OEM auto-park-active episode (1,460 EPS status frames / about 14.6 s) with Comma lateral commands inactive; this audit adds no PAM-to-EPS command identity, firmware semantics or low-speed third-party authorization.
4. Factory ACC brake/throttle attribution, physical lane crossings, parked Comma logger CPU/RSS acceptance, reproducible Comma 3 builds, independent review and safety tests remain open.

**Vehicle/software safety:** baseline branch, running device, Panda safety, CAN transmission, brake/throttle/steering actuators, Railway collector and private bucket are unchanged. No installer candidate is authorized.
