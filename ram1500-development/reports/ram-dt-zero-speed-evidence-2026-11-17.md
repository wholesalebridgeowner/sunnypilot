# RAM DT low-speed LKAS: source-proven eligibility audit

*November 17, 2026. Read-only source review; no connected-vehicle firmware identity or low-speed engagement verified.*

**Bottom line:** the 32 mph figure is the **default** documented RAM_1500_5TH_GEN software configuration, **not** proof that every Ram DT uses a hard 32 mph EPS actuator limit. The exact pinned integration has explicitly coded lower-speed exceptions. None establishes that this particular truck qualifies, nor that its factory parking controller uses the same command authorization path.

## Reproducibility

Analyzed the **pinned historical** `commaai/opendbc` revision `df807f8be394231b2366a1ecd9c9265ab51d2400`, referenced by the November 14 route's source review. This is not necessarily byte-for-byte the installed car code: the Comma reported a **dirty working tree**, and independent firmware/vehicle configuration has not been captured.

| Condition in source | Observed software effect | Code location |
|---|---|---|
| RAM_1500_5TH_GEN default CarSpecs | `minSteerSpeed = 14.5` m/s (~32.4 mph) | [values.py L86–89](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/car/chrysler/values.py#L86-L89) |
| EPS firmware has prefix inside `b"68" < fw.fwVersion[:4] <= b"6831"` | `minSteerSpeed = 0` in base interface | [interface.py L68–73](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/car/chrysler/interface.py#L68-L73) |
| RAM DT with an EPS firmware string exactly matching one of `68273275AF`, `68273275AG`, `68312176AE`, `68312176AG` | `minEnableSpeed = 0`, overriding the usual 14.5 m/s in Sunnypilot's extension | [interface.py L94–99](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/car/chrysler/interface.py#L94-L99) |
| CAN address `0x4FF` found in bus-0 fingerprint | Sets `NO_MIN_STEERING_SPEED` and `minSteerSpeed = 0` (not proof of EPS support) | [interface.py L110–112](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/car/chrysler/interface.py#L110-L112) |
| `NO_MIN_STEERING_SPEED` flag | Extension's LKAS control bit follows `CC.latActive` | [carcontroller_ext.py](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/sunnypilot/car/chrysler/carcontroller_ext.py) |
| RAM DT without that flag | Extension controls LKAS bit based on `minEnableSpeed` and speed window; also applies a gear check when `minEnableSpeed >= 14.5` | [carcontroller_ext.py](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/sunnypilot/car/chrysler/carcontroller_ext.py) |
| Steering command issued in recorded-source controller | Torque command is clipped by measured-torque limits and zeroed when lateral/LKAS inactive; an additional re-enable lockout exists | [carcontroller.py L57–90](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/car/chrysler/carcontroller.py#L57-L90) |
| Panda Chrysler RAM DT safety implementation | Independent torque/rate/feedback checks on outgoing LKAS command; not evidence that zero-speed LKAS authorization is possible | [chrysler.h L117–150](https://github.com/commaai/opendbc/blob/df807f8be394231b2366a1ecd9c9265ab51d2400/opendbc/safety/modes/chrysler.h#L117-L150) |

**Important distinction:** `minSteerSpeed` and `minEnableSpeed` are **different parameters**. A zero value for one does not establish unrestricted engagement or operation. Even a source-based zero-speed path is an *eligibility hypothesis* until validated against this device's actual EPS firmware, safety config, observed vehicle state, and controlled hardware behavior.

### Passive evidence needed from this truck

1. Sanitized actual `CarParams.carFw` entry for the EPS ECU, with firmware bytes/part number only (no VIN or vehicle identification). Compare **exact** bytes to the source, not model-year assumptions.
2. Observed bus-0 fingerprint indicating whether address `0x4FF` is present; an observation of the ID alone doesn't validate the signal's intended feature semantics.
3. The actual `CarParams.minEnableSpeed`, `minSteerSpeed`, `safetyConfigs`, and `CarParamsSP.flags` produced by the *installed* build.
4. Read-only log coverage from controlled low-speed OEM parking action versus ordinary LKAS, with vehicle stopped and no autonomous-command injection. Identify actual command origin/bus/ECU authorization if this evidence is obtainable.
5. Separate measured lane-centering/ACC behavior, errors and disengagements from the above static configuration.

**What this review does not establish:** exact actual firmware variant, that parking-assist uses LKAS `0xA6`, that 0 mph engagement is supported on the device, feasibility of route-following steering, or that any changed software is ready to install. No firmware spoofing, CAN injection, EPS unlock, or bypass of safety protections is proposed.

### Engineering consequence

Before investigating firmware changes or actuator constraints, gather the truck's read-only `CarParams` and verify whether the **existing** implementation already recognizes a supported low-speed variant. This is more discriminating than simply assuming Comma's compatibility-table value is a physical limitation.

### Independent issue: navigation-guided steering

None of the Chrysler firmware logic above establishes navigation maneuver capability. It remains a separate model/planner/route-data/control validation effort; low-speed steering alone does not make turn-by-turn navigation safe or functional.

## Passive evidence from the truck — collected by Claude, 2026-11-17 (read-only SSH, truck parked)
Decoded on the Comma from `/data/params/d/CarParamsPersistent` and `CarParamsSP` with the device's own cereal; VIN not read
out. Device runs `sunnypilot/sunnypilot@1f7233cb` (v0.10.1, prebuilt, clean tree — see `DEVICE_DIAGNOSTICS.md`).

| Item requested above | Value on the truck | Consequence |
|---|---|---|
| 1. EPS firmware (`carFw`, ecu `eps`, 0x75A) | **`68466110AB`** | Prefix `6846` is above the `<= 6831` "older EPS steers to zero" rule, and the string is **not** one of `68273275AF/AG`, `68312176AE/AG`. |
| 2. `0x4FF` in the fingerprint | **Not present** (`CarParamsSP.flags = 0`, so `NO_MIN_STEERING_SPEED` unset) | The flag-based zero-speed path does not apply. |
| 3. Installed `minEnableSpeed` / `minSteerSpeed` / safety / flags | **14.5 m/s** / **0.5 m/s** / `chrysler`, param 1 (RAM_DT) / `CarParams.flags = 0`, `CarParamsSP.flags = 0` | Engagement requires ≥ 14.5 m/s (~32 mph) on this truck. |

Other ECUs (part numbers only): fwdRadar `68475160AG`, combinationMeter `68453505AD`, srs `68500728AA`, abs `68728727AA`,
engine `68500630AF`, transmission `68502994AD`.

**Correction to the source basis above:** `df807f8` is the `opendbc_repo` submodule of this fork's April 2026 master
(`sunnypilot/opendbc`), not the code the truck runs. In the installed build `1f7233cb`, the four-firmware
`minEnableSpeed = 0` exception **does not exist at all** (it was added later), and the "older EPS steer to zero" result is
overwritten to 0.5 m/s by sunnypilot's `_get_params_sp`. Verified by diffing the two trees (`interface.py`).

**Behaviour in the installed build for this truck** (`carcontroller_ext.py:14-24` at `1f7233cb`): the LKAS control bit
latches on only while passing 14.5–15.0 m/s, then stays on below that until the gear leaves Drive; openpilot stops
lateral control below 0.5 m/s (`controlsd.py:112`). So after engaging above ~32 mph, steering commands continue down to
~1 mph. Whether this EPS firmware *honours* torque at those speeds is not shown by configuration; it is visible in logs
as `carControl.latActive` with low `vEgo` plus a responding `carState.steeringTorqueEps`. Full logs of drives since
Nov 14 are on the device (the Nov 14 route itself has been rotated off), so this can be measured passively.

**Bottom line for this truck:** no supported zero-speed engagement path exists for EPS `68466110AB` in either the
installed build or this fork's master. Low-speed engagement stays a research question; nothing here justifies
spoofing a firmware string, injecting `0x4FF`, or changing limits.

Installed control settings read at the same time (context for log interpretation): `NeuralNetworkLateralControl=0`
(stock torque controller; latAccelFactor 2.000, friction 0.050 placeholder confirmed in the device CarParams),
`LagdToggle=1` with learned lateral delay 0.32 s (configured `steerActuatorDelay` 0.2 s), `Mads=1`,
`DisengageOnAccelerator=1`, `BlinkerPauseLateralControl=1`, `openpilotLongitudinalControl=False`, `pcmCruise=True`.
