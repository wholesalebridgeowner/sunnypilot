# November 18 — historical Nov 14 R-log recovery: automatic collector eligibility audit

**Status: read-only deployed collector review + read-only historical Comma archive metadata preflight + synthetic tests.** This report examines why the existing automatic Railway log bridge does not currently provide the missing high-resolution Nov 14 saturation segment. It does **not** establish that Comma cloud or the vehicle lacks the R-log.

## Audited source and independent observations

- On November 18, inspected the **actually deployed**, read-only-source version of Railway Function `comma-log-sync`, within the existing private `ram-drive-log-bridge` project. The function has `LOOKBACK_HOURS = 72`; its route discovery queries `/v1/devices/<device>/routes_segments` from `Date.now() - 72 hours`. It sorts by route recency and restricts processing to **four** recent routes with `.slice(0, 4)`.
- The same deployed source caps processing at **12 files per execution**. Optional archive-upload requests are disabled unless `RUN_UPLOAD_REQUESTS == "true"`, and, even when enabled, it queues at most **four** missing R-logs per selected route per pass. A request's acceptance does **not** establish upload or retrieval.
- The historic Nov 14 saturation route ended over **93 hours** before this Nov 18 audit. Its end is therefore older than the collector's current **72-hour route-discovery interval**. Under the ordinary interpretation of the provider's time filter, **the current automatic collector will not revisit that route**. The historical route's rank among eligible routes at earlier executions was not recovered; the 4-route cap is an additional *possible* exclusion mechanism, not a proven past cause.
- Verified November 18 Railway bucket inventory, separate from this source review: `ram-private-log-presence-audit` deployment `b0981f35-0238-4381-b141-0d359be5e8b4` reported 612 objects, 11 route groups, complete pagination, **no matching Nov 14 route** in this bucket. Nov 17 had 45/45 Q/R segments 0–44, including the focus segment 30. The original 51-segment Nov 14 Q-log analysis was performed through another source and is not contradicted.
- The collector's deployed function source was only **read**, not changed. No cloud log URL, route ID, VIN, object key, location or credentials are included in this report.

## New independently verified archived-route metadata lookup

After reviewing the collector, ran an isolated **one-shot, metadata-only** Railway Function `ram-historical-rlog-availability-readonly` against the **specific historical Nov 14 route** using existing authorized Comma credential references. This Function made one authenticated **GET** of Comma's `/v1/route/<private-route>/files` endpoint; it did not request an Athena upload, download a raw file, access vehicle CAN, or write any object or private URL. Its output was limited to URL-presence booleans for segments 1–3 and aggregate link counts. The route selector and credentials remained private Railway variables.

Verified completed private Railway deployment `12d85690-b883-4268-bfaf-54bb2c90cbdc` reported:
- `phase=completed_metadata_only`, HTTP success.
- **0 available R-log links** in the authorized Comma route-file listing, including **no R-log for segments 1, 2 or 3**.
- **51 Q-log links**, with Q-log links present for each of segments **1, 2 and 3**.

This is stronger and more precisely scoped than the earlier bucket-only absence: the currently authorized Comma route-file metadata also contains **no R-log download URLs** for the studied drive. It does **not** prove that the parked device, a different archival system, or future upload requests cannot recover the segment. The existing historical Q-log count is independently corroborated by this metadata lookup; no Q-log contents were downloaded or decoded here.

## Safe next retrieval and analysis gates

The authorized historical-route metadata lookup **has now confirmed no R-log links** for the Nov 14 drive. Recovery would require a separately authorized device/archive availability assessment (or fresh, independently approved upload), not the existing rolling collector; a previously accepted Athena request is insufficient. Only if a real R-log later becomes available should it be retrieved into private storage, have object arrival and decompression verified, match route/segment identity and be decoded against `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` with the dirty-tree caveat. Only then can the +171-second torque event be analyzed at high resolution.

### Offline reproducibility

`collector_backfill_gate.py` is a pure Python status helper derived from the deployed collector's route lookback and top-four caps. It takes synthetic or privately supplied **aware datetimes**, optional zero-based rank and evidence flags; it returns only stable descriptive statuses and source-audited numeric limits. It never connects to the provider, reads object keys or logs, or requests an upload. Its evidence flags are **caller assertions** to be independently checked, not cryptographic or physical validation by the helper.

`test_collector_backfill_gate.py` covers outside-window exclusion, exact cutoff, uncertain or excluded rank, request-versus-cloud-versus-private-object stages, fail-closed provenance, invalid timestamps/rank, contradiction handling and privacy. All tests are fully synthetic. The separate CI workflow executes them using Python 3.12.

## Unchanged safety / outstanding work

This is an **acquisition-path blocker diagnosis**, not the +171 s steering diagnosis. The Nov 17 native-steering cap remains observational; retrospective rate-limited matches do not establish controller phase or EPS causality. OEM auto-park still demonstrates one EPS control-mode episode only; the PAM-to-EPS command and LKAS authorization are unknown. Factory ACC actuation, lane-crossing/recentering, parked logger CPU/RSS, reproducible device build and independent safety review remain unfinished. The baseline branch, existing Railway collector and bucket, Comma device, CAN activity, Panda protections and brake/throttle/steering software were not modified. One isolated, unscheduled, metadata-only Railway Function was created for the authorized historical GET lookup; it retains no downloaded logs and performs no vehicle-side action.
