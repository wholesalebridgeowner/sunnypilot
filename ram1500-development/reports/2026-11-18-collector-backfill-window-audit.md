# November 18 — historical Nov 14 R-log recovery: automatic collector eligibility audit

**Status: read-only source review + synthetic tests; no new Comma archive availability check.** This report examines why the existing automatic Railway log bridge does not currently provide the missing high-resolution Nov 14 saturation segment. It does **not** establish that Comma cloud or the vehicle lacks the R-log.

## Audited source and independent observations

- On November 18, inspected the **actually deployed**, read-only-source version of Railway Function `comma-log-sync`, within the existing private `ram-drive-log-bridge` project. The function has `LOOKBACK_HOURS = 72`; its route discovery queries `/v1/devices/<device>/routes_segments` from `Date.now() - 72 hours`. It sorts by route recency and restricts processing to **four** recent routes with `.slice(0, 4)`.
- The same deployed source caps processing at **12 files per execution**. Optional archive-upload requests are disabled unless `RUN_UPLOAD_REQUESTS == "true"`, and, even when enabled, it queues at most **four** missing R-logs per selected route per pass. A request's acceptance does **not** establish upload or retrieval.
- The historic Nov 14 saturation route ended over **93 hours** before this Nov 18 audit. Its end is therefore older than the collector's current **72-hour route-discovery interval**. Under the ordinary interpretation of the provider's time filter, **the current automatic collector will not revisit that route**. The historical route's rank among eligible routes at earlier executions was not recovered; the 4-route cap is an additional *possible* exclusion mechanism, not a proven past cause.
- Verified November 18 Railway bucket inventory, separate from this source review: `ram-private-log-presence-audit` deployment `b0981f35-0238-4381-b141-0d359be5e8b4` reported 612 objects, 11 route groups, complete pagination, **no matching Nov 14 route** in this bucket. Nov 17 had 45/45 Q/R segments 0–44, including the focus segment 30. The original 51-segment Nov 14 Q-log analysis was performed through another source and is not contradicted.
- The collector's deployed function source was only **read**, not changed. No cloud log URL, route ID, VIN, object key, location or credentials are included in this report.

## Safe next retrieval and analysis gates

An authorized, separately reviewed **historical-route lookup** must query the older Nov 14 route specifically through the existing Comma archive permissions, rather than relying on the collector's rolling 72-hour window. Check whether the focused segment 2 R-log and adjacent segments have actual Comma cloud links; a previously accepted Athena upload request is insufficient. If available, retrieve only those logs into private storage, verify object arrival and decompression, match route/segment identity and decode against `1f7233cb983f3c13fdbba47a86f8fbc796ad9211` with the dirty-tree caveat. Only then can the +171-second torque event be analyzed at high resolution.

### Offline reproducibility

`collector_backfill_gate.py` is a pure Python status helper derived from the deployed collector's route lookback and top-four caps. It takes synthetic or privately supplied **aware datetimes**, optional zero-based rank and evidence flags; it returns only stable descriptive statuses and source-audited numeric limits. It never connects to the provider, reads object keys or logs, or requests an upload. Its evidence flags are **caller assertions** to be independently checked, not cryptographic or physical validation by the helper.

`test_collector_backfill_gate.py` covers outside-window exclusion, exact cutoff, uncertain or excluded rank, request-versus-cloud-versus-private-object stages, fail-closed provenance, invalid timestamps/rank, contradiction handling and privacy. All tests are fully synthetic. The separate CI workflow executes them using Python 3.12.

## Unchanged safety / outstanding work

This is an **acquisition-path blocker diagnosis**, not the +171 s steering diagnosis. The Nov 17 native-steering cap remains observational; retrospective rate-limited matches do not establish controller phase or EPS causality. OEM auto-park still demonstrates one EPS control-mode episode only; the PAM-to-EPS command and LKAS authorization are unknown. Factory ACC actuation, lane-crossing/recentering, parked logger CPU/RSS, reproducible device build and independent safety review remain unfinished. The baseline branch, Railway functions and bucket, Comma device, CAN activity, Panda protections and brake/throttle/steering software were not modified.
