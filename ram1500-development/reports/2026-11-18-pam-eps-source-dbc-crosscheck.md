# November 18, 2026 — independent PAM/EPS source and DBC cross-check

**Scope:** offline reference audit only. This report does not decode a command, establish a third-party control path, or authorize any low-speed actuation. Main branch `ram1500-custom-development` and vehicle software are unchanged.

## Independent public service-information corroboration

Three separate descriptions in reproduced **2021 Ram 1500 service information** agree at the *module architecture* level:

1. [PAM inputs and outputs, Ram 1500 HFE](https://www.shinevfn.com/Dodge%20and%20Ram/2021/1500%20HFE/Repair%20and%20Diagnosis%20%28Single%20Page%29/Accessories%20%26%20Equipment/Drivers%20Assistance%20Systems%20-%20ADAS/Chime%2FBuzzer%2FDriver%20Assist%20%28Service%20Information%29%20-%201500/Description/Park%20Assist%20System/Park%20Assist%20Module%20%28PAM%29/): identifies a **steering-control output from the Park Assist Module to EPS**. This is a documented module role, not a known CAN ID.
2. [PAM description and operation, Ram 1500 HFE](https://www.shinevfn.com/Dodge%20and%20Ram/2021/1500%20HFE/Repair%20and%20Diagnosis%20%28Single%20Page%29/Accessories%20%26%20Equipment/Communication%20Devices/Electronic%20Control%20Modules%20%28Service%20Information%29%20-%201500/Module%2C%20Park%20Assist%20%28PAM%29/Description/Description%20And%20Operation/): describes PAM **CAN-C (chassis CAN)** transmission and broadcast of parking-system messages.
3. [EPS park-assist description, 2021 Ram 1500 Big Horn](https://lemon-manuals.org.ua/Dodge%20and%20Ram/2021/1500%20Big%20Horn%2C%204D%20Pickup%20Crew%20Cab%2C%205.7L%20Eng%20VIN%20T%2C%20Eng%20CD%20EZH%2C%204WD/Repair%20and%20Diagnosis%20%28Single%20Page%29/Accessories%20%26%20Equipment/Warning%20Systems/Chime%2FBuzzer%2FDriver%20Assist%20%28Service%20Information%29%20-%201500/Description/Park%20Assist%20System/Electronic%20Power%20Steering%20%28EPS%29%20Module/): says EPS ramps assist toward a **PAM-requested torque value** when a park maneuver is permitted and mentions OEM hands-on monitoring.

**Conclusion bounded by these sources:** PAM-to-EPS steering-request ownership is described by 2021 model-family service documentation, and PAM communicates over CAN-C. The EPS-side request is described as an OEM function. This is stronger than arbitrary observed CAN frame-activity correlations. Nevertheless this is a cross-trim/year architectural comparison, **not** proof of this truck's exact module wiring, firmware semantics, frame sender identity, security authorization, or Comma-harness visibility.

## Independent exact-recorded-build DBC/address verification

I separately fetched these **complete public source files** through GitHub at the recorded device build `sunnypilot/sunnypilot@1f7233cb983f3c13fdbba47a86f8fbc796ad9211`:

| Source file | Git blob SHA-1 | Verified source facts |
|---|---|---|
| `opendbc_repo/opendbc/dbc/generator/chrysler/_stellantis_common.dbc` | `df913561de2fad009ba27288cd8d5ec00a36ca02` | `BO_ 544 EPS_2: 8`; `AUTO_PARK_HAS_CONTROL_2 51|1@0+`, `LKAS_STATE 23|4@0+`, `TORQUE_OVERLAY_STATUS 6|4@0+` |
| `opendbc_repo/opendbc/dbc/generator/chrysler/_stellantis_common_ram.py` | `fa408f1315f34feffad23c4637c7df905b5f8d7c` | RAM-DT generator maps original EPS_2 address decimal 544 to decimal **49 = 0x31** |

The new `oem_eps_dbc_provenance.py` script accepts **only those exact git-blob hashes** by default. It statically inspects the DBC field layouts and generator address dictionary using `ast.literal_eval` (no Python source execution); it fails closed on source drift, missing/duplicate fields, changed bit layout, ambiguous messages or changed address mappings. It reports only layout, hashes and explicitly unproven questions; **it never reads a raw CAN log, VIN or location**.

Run from a private copy of the complete pinned upstream source files:

```sh
python3 ram1500-development/oem_eps_dbc_provenance.py \
  --common-dbc /path/to/1f7233cb/_stellantis_common.dbc \
  --ram-generator /path/to/1f7233cb/_stellantis_common_ram.py
```

The parallel `test_oem_eps_dbc_provenance.py` fixture suite requires no network or private driving data. GitHub source SHA retrieval is independently confirmed; the new CLI's execution against the two downloaded full files remains a separate validation gate.

**Important topology limitation:** a DBC frame label `EPS_2` proves neither which ECU originally transmitted a frame nor where it originated on the physical truck network. The generator's address mapping does **not** specify physical bus 0, which is only established in the recorded Comma capture. CAN-C at the OEM PAM is not synonymous with complete CAN-C visibility through a Comma harness or permitted third-party transmission.

## Connection to the independently decoded factory parking episode

The [earlier private R-log report](2026-11-18-factory-parking-rlog-findings.md) measured **1,460 EPS status observations over 14.6 s** with `AUTO_PARK_HAS_CONTROL_2=1`, `LKAS_STATE=1` and `TORQUE_OVERLAY_STATUS=1`, near-zero-speed steering, and zero Comma lateral/native steering command. These are real measurements from the owner's November 18 factory parking maneuver, unlike the synthetic regression tests.

Only the **AUTO_PARK_HAS_CONTROL_2 name**, not a trusted OEM enumeration for the other two ordinals, is supported by the recorded DBC. No finding here establishes that the unlabeled `0xA9`, `0x81`, `0x23` or `0xB5` traffic originated at PAM. Do not identify a command from payload variability or infer causal controller timing from retrospectively matched rate-limited outputs.

## Blockers and safest next evidence

1. Obtain licensed model-year/VIN-specific topology and scan-tool **read-only** PAM/EPS mode diagnostics to confirm module path and status semantics. Do not publish identifying results.
2. Establish which bus(ses) are observable from the existing Comma harness; absent CAN frames cannot rule out traffic on another private or gateway-isolated bus.
3. Run this SHA-pinned source check on an independently obtained full source snapshot and confirm the workflow CI result at the new head.
4. Keep Nov 14 +171 s R-log and ACC/lane comfort work isolated on their existing branches. Do not merge control changes or alter safety software.

**Release gate:** independent review, full pinned replay/build, parked device diagnostics and controlled hardware safety validation are not complete. There is no installable candidate.
