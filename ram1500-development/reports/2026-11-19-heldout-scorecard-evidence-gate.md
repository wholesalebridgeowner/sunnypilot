# November 19 — held-out lateral / lane-change evidence release gate

**Status: offline integrity tool passing synthetic tests; not a measured steering calibration, hardware acceptance or installable release.** This report is source-pinned to recorded Sunnypilot `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`, and to this branch's source/test commit `2336c334e210aa8ba8dffe3515518741e8be7ec0`. Current dirty on-device working-tree contents and controller phase remain unverified.

## Why this step

The owner's eleven-stage plan (draft PR #18, `2026-11-18-end-to-end-development-plan.md`) requires **multiple independent, held-out** active and healthy driving routes, separate lane-centering, ping-pong/reversal and overshoot scorecards, lane-change comfort, steering safety caps, and independent review **before** any numerical tuning or vehicle installation. Better steering response or reduced delay is not a sufficient objective if steering oscillation increases.

The existing scheduled private reviewer is a sampled Q-log **observational** trend reporter, not source-complete lateral-error ground truth or a replay of proposed controller changes. Its historical raw wheel-rate and lane model-phase metrics must not be substituted for independent physical trajectory, recentering, or EPS command validation.

## New read-only testable deliverable

`heldout_stability_gate.py` validates a **private, locally prepared aggregate-only paired offline replay manifest**. The gate is intentionally strict:
- Require exact `1f7233cb` source, the explicit versioned **R-log independent trajectory and lane-crossing qualification**, verified segment isolation, fresh Comma lateral ownership/healthy CAN, a uniquely determined controller phase and no unexplained gaps. The per-route flags are **external attestations, not cryptographic verification**; an independent source/log review must substantiate them.
- Require at least two disjoint development and two independent, *precommitted* held-out routes with an independently completed review. This is a conservative screening policy, not a statistical proof of safe vehicle operation.
- Compare the same vehicle/log inputs under baseline and candidate replay, matching sampled exposure exactly, with at least 60 eligible seconds per held-out route as an initial screening threshold; again not a vehicle approval threshold.
- Fail closed on any increase in route-wise lane-centering RMSE, 95th-percentile overshoot, steering reversal rate, oscillation energy, native command clipping, steering saturation/override episodes, measured lane-change entry lateral acceleration or physical recentering error. Absence of **any** actual improvement also blocks readiness.
- Emit no input route tokens, private samples, candidate numerical controller parameters or installation payload. The highest possible result is `OFFLINE_EVIDENCE_ELIGIBLE_FOR_REVIEW`, with `vehicleReleaseAuthorized=false` and `calibrationAuthorized=false`.

**Tests:** 16 synthetic unit tests passed on source/test/workflow commit `2336c334e210aa8ba8dffe3515518741e8be7ec0` — GitHub Actions [held-out workflow #44676218586](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44676218586). The independent existing [offline analyzer #44676218630](https://github.com/wholesalebridgeowner/sunnypilot/actions/runs/44676218630) also passed 227 tests (one skipped), including exact installed EPS DBC blob/address verification. Synthetic tests cannot establish the presence of qualifying real R-log trajectory ground truth.

## Recent independent route and Pi evidence (private Railway)

- Existing production `ram-scheduled-route-review`, source-pinned at `8d86107587cb3bd0d8bb5f0679d62bd5b85df01e`, saved **one new, 44-segment observational Q-log review** on November 19 at 03:21 UTC November 20. Its safe aggregate counted **16,443 laterally active samples**, **11 model lane changes**, and **one steering event episode**; `comparisonBaseline=false`. These are **not** independently confirmed lane crossings, a calibration candidate, validated held-out oscillation measures or evidence of a steering improvement. Subsequent scheduled scans reported 17 eligible inventory candidates and no further new eligible route. Raw recordings and private route identity remain in Railway storage.
- Separate Railway `ram-pi-tailnet-probe` source `c3029b8f90ebdc9e058f9d2226b5f9601cba1445` confirmed userspace Tailscale registration and **TCP/22 reachability** to the existing Sunnyslope Raspberry Pi, but the strictly read-only `true` SSH authentication probe returned `pi_ssh_auth_not_confirmed`. Network reachability is **not** an authenticated command channel, nor proof the Pi can reach the Comma; no Pi or truck commands executed. See the private Railway project service log and the public *non-secret* `ram1500-development/tailnet_bridge/README.md` on that branch.
- The Pi probe's Tailscale API key was previously supplied in chat. It should be treated as exposed and revoked/replaced with a narrowly scoped administrative credential. No tokens, network locators, keys or raw data are contained in this report.

## Stage status and next independent evidence

**Steps 1–4** retain the separately bounded original installed-source, ACC bit-decoder and morning/afternoon route observations. **Step 5** has conditional sampled steering proxies only. **Steps 6 and 8** now have an adversarial *offline scorecard qualification gate*, **not** a fitted candidate or passed held-out controller replay. **Step 7** still measures factory-ACC observed headway/jerk only, not direct OEM throttle/brake actuation or the OEM selected distance. **Step 9** requires an operator-approved parked maintenance session and a passing real 120-second diagnostic acceptance; PR #49 is not installed. **Step 10** remains prohibited pending independent review, controller/source and OEM/Panda safety, rollback and supervised vehicle tests. **Step 11** continues.

The next defensible private experiment is to identify multiple genuinely independent, source-pinned, laterally active R-log routes and reconstruct actual lateral tracking, crossing/recentering and oscillation-energy evidence with complete confidence/quality masks. Do not compare current Q-log model-phase counts as if they were physical lane changes. No delay/friction/torque parameter should be selected based on this gate or one steering warning.

**No live actuation code, active branch, PANDA safeguards, ECU, raw CAN, vehicle software, VIN/location, or device logger was touched.**
