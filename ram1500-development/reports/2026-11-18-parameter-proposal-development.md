# Ram 1500 — source-pinned offline parameter candidate development

**2026-11-18 | No vehicle write; no installable tuning.** The owner authorized *development of new parameters to write in the future*, and reported the truck is currently driving. Existing vehicle software must remain unchanged during the drive. Candidate numbers must be derived from independently measured evidence rather than guessed from one steering episode.

## Verified actual installed configuration

The installed Sunnypilot build is `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, EPS firmware `68466110AB`.

| Domain | Recorded value | Meaning |
|---|---:|---|
| Lateral torque `latAccelFactor` | 2.000 | Observed current calibration; identified as provisional placeholder |
| Lateral `friction` | 0.050 | Observed current calibration; provisional placeholder |
| `steerActuatorDelay` | 0.20 s | Configured latency |
| Learned lateral delay | 0.32 s | Separate learned observation; **not a safe replacement setting without causal identification** |
| `minEnableSpeed` | 14.5 m/s (~32 mph) | Initial Sunnypilot engagement threshold |
| `minSteerSpeed` | 0.5 m/s | Separate installed lateral-steering lower-speed setting |
| Longitudinal authority | Factory ACC | `openpilotLongitudinalControl=false`; Comma does not own braking/acceleration |
| OEM automatic parking | Confirmed from EPS status bit | Separate factory-authorized mode (14.6 s episode); **not proof Comma may actuate it** |

References: `ram-dt-zero-speed-evidence-2026-11-17.md`, `2026-11-18-causal-native-torque-replay.md`, `2026-11-18-comfort-episode-gates.md`, and `2026-11-18-factory-parking-rlog-findings.md` on PR #1.

## Seven workstreams, with explicit write gates

| Proposed parameter category | Work now | Vehicle-write rule |
|---|---|---|
| Lateral acceleration factor | Fit from multiple clean independent routes, isolate EPS-limited/driver override samples | Pending independent out-of-sample validation |
| Friction | Estimate left/right deadzone behavior without confusing bank, wind, driver torque, EPS rate limiting | Pending independent review |
| Steering actuator delay | Cross-correlate physically meaningful, direction-specific response using causal timestamps; compare observed 0.20 vs learned 0.32 s *offline* | No on-device update from one learned observation |
| Lane-change comfort | Quantify entry/crossing/recenter, intervene/cancel and lane margins; independent A/B replay | No speculative jerk/aggressiveness write |
| Factory following-gap behavior | Characterize measured cruise/lead gap by speed/source; independent sustained following runs | Not a Comma setpoint on this truck; no shorter/faster gap based on observations |
| Torque/rate/safety bounds | Preserve current ceilings and verify exact installed controller/panda constraints | **Locked.** Do not change from current replay |
| Low-speed engagement | Study owner-observed post-32-mph steering latch separately from OEM parking | **Locked.** No auto-park CAN replay, spoofing or safety bypass |

The source-pinned manifest is `ram1500-development/proposed_parameters.json`. The validator `ramtools/parameter_proposal.py` **rejects all non-null trial write values** and requires six release gates. `tests/test_parameter_proposal.py` verifies exact firmware identity, baseline values, factory ACC ownership, parameter uniqueness, locked safety fields and noninstallable state; the PR #2 GitHub Actions workflow enforces it.

## Evaluation strategy for candidate values

1. Collect private, existing R-logs **without any new onroad subscribers** while the truck is moving, after the normal logger finalizes segments. Confirm each route uses the pinned installed build.
2. Filter calibration samples by actual lateral-active state; exclude human steering interventions, OEM automatic parking, stale joins, invalid state, temporary faults, and EPS software torque/rate clip windows. Preserve counts of excluded and retained samples.
3. Group results by left/right sign, speed, curvature, driver torque, and route. Keep **separate held-out routes**, not just random samples from one route, to avoid false certainty from serially correlated signals.
4. Evaluate parameter sensitivity on an isolated offline controller model, initially comparing current configuration to candidate estimates. Require reproducible improvements in tracking/overshoot that also preserve warning/override safety; return **insufficient independent data** when not met.
5. Send proposed numeric values to Claude for independent source-code and control-theory review. Only after multiple validation gates and owner approval during an appropriate parked maintenance window should an installer be considered. No promise of safety from offline statistical fit.

## Explicit out-of-scope changes

No modification of active truck files, steering/braking/acceleration control code, EPS firmware, OEM ParkSense authorization, Comma/Panda safety model, CAN injection, speed-limit/gear-lockout behavior or hidden autonomous control from this proposal. No onroad diagnostics while the vehicle is moving.

**Status:** Baseline and evaluation infrastructure implemented; **no numeric improvement claim or vehicle-writable candidate exists yet**. Next deliverable is an independently measured *offline* trial grid and comparison scores from eligible routes.


## First concrete offline sensitivity trials — latency only

The new `ramtools/parameter_experiments.py` generator takes the **configured 0.20-second steering actuator delay** and the separately observed **0.32-second learned lateral delay** and constructs a *noninstallable* sensitivity grid:

| Hypothesis (seconds) | Provenance |
|---|---|
| **0.20** | Source-pinned configured baseline |
| 0.23 | Interpolated sensitivity test |
| 0.26 | Interpolated sensitivity test |
| 0.29 | Interpolated sensitivity test |
| **0.32** | Source-pinned observed learned value, **not** a validated actuator setting |

**These are five hypotheses for isolated offline model replay, not five calibrated steering parameters and not a proposed road-use change.** They are NOT yet ranked by performance. This grid does not automatically fit route logs or validate steering stability; it provides deterministic input candidates for the next analysis.

The tool revalidates `proposed_parameters.json` on each invocation, rejects changed EPS firmware or installed-source identity, refuses modified baseline authority, and emits `OFFLINE_HYPOTHESIS_NOT_INSTALLABLE` with `release=false`. The tool does **not** write a vehicle configuration file or enable model tuning on the Comma.

Run off-device:

```bash
cd ram1500-development
python -m ramtools.parameter_experiments proposed_parameters.json
```

The adjacent `tests/test_parameter_experiments.py` covers the five trial values, bad parameters, mismatched firmware, implausible learned delays, and all-other-candidates-null. GitHub CI runs those tests and the generator. Candidates for lateral acceleration factor, friction, lane-change behavior, ACC gap and low-speed authorization remain **numerically unspecified** until identifiable and reviewed; do not invent search ranges and then treat them as data.
