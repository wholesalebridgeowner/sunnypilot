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
