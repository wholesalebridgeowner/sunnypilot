# Saturday, November 14, 2026 — Ram 1500 steering investigation
Status: **evidence-backed preliminary analysis**; not a confirmed steering root-cause diagnosis.
Source: private Comma Q-logs evaluated read-only in Railway; source-code audit of pinned `opendbc_repo` commit `df807f8be394231b2366a1ecd9c9265ab51d2400` and development branch.
No private log content or identifying driving telemetry is committed here.

## Recorded evidence
- Focus route: `000003cc--27a46b2116`, 18:29:53–19:19:55 Central (approximately 50 minutes), 51 Q-log segments.
- Existing parser decoded 728,719 Cap'n Proto messages without framing errors, including 30,000 samples of each `carState`, `carControl`, `controlsState`, 30,000 `carOutput`, and 11,995 `liveTorqueParameters`.
- A single **`steerSaturated` alert sample** appears near +171 seconds (18:32:44 Central), segment 2.
- Six `controlsMismatch` event samples (startup/end), two `canError` samples at +3002 seconds (end), and one `locationdTemporaryError` near end; reverse gear, seatbelt and door events also occur near end. This is **consistent with** a shutdown/parking transition but **does not prove** the faults are harmless.
- `steerOverride` 1,675 and `gasPressedOverride` 916 **event samples**, not unique incidents; a 1-second-gap episode grouping is provided as an operational heuristic in the analyzer.
- Startup system log contains `modeld` dropped-frame warning and `selfdrived.initialized` reporting `canValid=true` with other missing/invalid startup signals. No causal connection to +171 sec established.
- The recorded `staging-tici` build reported a dirty working tree, so exact effective software cannot be reconstructed simply from the reported commit.

## Source-code constraints, not measurements of the truck's behavior
Source: `opendbc_repo` at `df807f8be394231b2366a1ecd9c9265ab51d2400`.
- `opendbc/car/chrysler/values.py`: `RAM_DT` controller parameters set `STEER_MAX=350`, `STEER_DELTA_UP=6`, `STEER_DELTA_DOWN=6`, `STEER_STEP=2`, `STEER_ERROR_MAX=80`. The controller runs at nominal 50 Hz for these steering-command steps.
- `opendbc/car/chrysler/carcontroller.py`: normalized `CC.actuators.torque` is scaled by `STEER_MAX`, then passed through `apply_meas_steer_torque_limits` using `CS.out.steeringTorqueEps`; sends a zero actuation torque when LKAS is inactive and reports `torqueOutputCan` in the resulting `CarOutput` data.
- `opendbc/safety/modes/chrysler.h`: RAM DT transmit checks independently enforce `max_torque=350`, `max_rt_delta=112`, `max_rate_up/down=6`, and `max_torque_error=80`. These are design constraints **not** evidence that any specific threshold fired in the +171 sec event.
- `opendbc/car/chrysler/carstate.py`: `steeringTorque` is column/driver torque; `steeringTorqueEps` is motor torque; `steeringPressed` uses threshold 120 in native CAN units. Distinguish these signals in charts.
- `selfdrive/controls/lib/latcontrol_torque.py`: controller computes desired and actual lateral acceleration and records `torqueState.output`, `torqueState.error`, and `torqueState.saturated`. Its saturation check includes steering limits and other conditions, so **an alert is not a direct measurement of factory EPS ceiling**.
- `opendbc/car/chrysler/interface.py` and `opendbc/sunnypilot/car/chrysler/carcontroller_ext.py` contain firmware-dependent low-speed permissions, minimum engagement conditions and LKAS gating. Changes are not justified without actual ECU firmware identification and approval.

## Hypotheses for +171 second saturation (unranked; not proven)
1. A curve or demanded lateral acceleration exceeded permitted controller output.
2. Vehicle-side motor-torque feedback or rate limiting constrained the command.
3. A driver override was present during the same window.
4. Trajectory/curvature, steering calibration, roll/friction estimate or controller tuning contributed.

There is insufficient time-aligned numerical evidence to order these causes. In particular, do **not** increase steering limits based solely on the event.

## Deliverable: reproducible event-window exporter
`ram1500-development/analyze_route.py` now:
- Reads ordered local Q-/R-log files through the repository's `LogReader`.
- Emits per-record time-aligned **CSV** and JSON episode summaries.
- Captures observed vehicle speed, angle, driver and EPS motor torque, normalized commanded torque, applied CAN torque, lateral torque-controller output/error/saturation, desired/actual lateral acceleration and live torque parameters.
- Keeps missing fields blank, explicitly distinguishes samples from event episodes, and performs no network calls or device control.
- Supports `--focus-seconds 171 --window 30`; pass **all 51 segments** in ascending order so times are relative to the start of the selected route.

Usage, from a correctly provisioned matching fork (example paths only):
```bash
python3 ram1500-development/analyze_route.py /private/sat-route/*/qlog.zst \
  --focus-seconds 171 --window 30 \
  --output-prefix /tmp/ram-171
python3 -m unittest discover -s ram1500-development -p 'test_analyze_route.py' -v
```
**Validation required:** Execute on the pinned build/schema and actual files, check numerical units/field availability and reconcile timestamps before interpreting a chart. The output generator is committed; a numeric +171-second commanded-vs-applied torque table is **not yet verified**.

## Broader architecture — not constrained to the original development code
The entire platform is open for evaluation:
1. **Data and replay:** match the on-device modified build, improve raw-CAN capture and reproducibility, source-of-truth time alignment.
2. **Lateral perception/planning:** road geometry, curvature anticipation, lane positioning and curve entry/exit behavior; quantify tracking error.
3. **Control:** tune controller or model only after comparing normalized command, applied torque, actual motor torque, driver input, steering-angle and curvature response.
4. **OEM integration:** ECU firmware, steering authorization, allowed vehicle messages, hardware constraints. Parking assist capability alone does not imply authorized hands-free LKAS at 0 mph.
5. **Hardware:** confirm actual connected device model; assess capability for real-time models, thermal margin and data collection without assuming it is Comma 2/3 based only on prior conversation.
6. **Longitudinal/navigation:** separate future feasibility studies requiring independently validated braking, positioning, route planning, intersection interactions and fail-safe behavior.
7. **Safety:** preserve Panda and OEM safety gates, supervision requirements, and disengagement behavior. No public-road deployment of unvalidated changes.

## Prioritized next work
- **P0:** Run the new analyzer against the privately stored 51 Saturday segments with the build-matched schema; inspect +/-30 seconds around +171. Extract and compare `carControl.actuators.torque`, `carOutput.actuatorsOutput.torqueOutputCan`, `carState.steeringTorqueEps`, and `controlsState.lateralControlState.torqueState.*`.
- **P0:** Validate if `steerSaturated` coincides with `torqueState.saturated`, steering demand, driver torque or limited applied CAN output. Compute 5s/10s slopes and overlays without inferring causation prematurely.
- **P1:** Inspect +2990–3005 sec vehicle state, CAN validity and shutdown transition to classify trailing CAN/controls mismatch events.
- **P1:** Audit firmware / safety configuration in the device's actual `carParams` and compare to the pinned source, as the working tree was dirty.
- **P2:** Build offline baseline replay/tests and measure performance across more than one route before design proposals.

## Explicit outstanding items
- Numerical torque-command vs EPS-response timeline has not been executed and checked against the recorded private data.
- R-logs for this Saturday route not yet confirmed uploaded; Q-logs are sufficient for an initial controller-level investigation but not definitive raw CAN fault isolation.
- No validated controller patch or driving-software build exists. Review and test any change separately before considering vehicle deployment.
