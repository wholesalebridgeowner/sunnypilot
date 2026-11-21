# Installing RamPilot on the comma three, and going back

`rampilot/device.sh` runs on the comma. It installs the RamPilot branch from a git bundle, and rolls back to the pinned sunnypilot baseline (`1f7233cb`, branch `staging-tici`). Its offline tests are `rampilot/tests/test_device.sh` (Linux or WSL).

**Before you start**
- The truck is parked, in Park, and switched off, so the comma is offroad.
- The comma is on home or cabin Wi-Fi. SSH does not work over the phone hotspot.
- The comma's address and SSH access are known.

## 1. Copy the bundle and script (laptop)

```bash
BASE=1f7233cb983f3c13fdbba47a86f8fbc796ad9211
SHA=$(git rev-parse rampilot-tici)
git bundle create rampilot.bundle "$BASE..rampilot-tici"
ssh comma@COMMA_IP 'mkdir -p /data/ram-diag/rampilot'
scp rampilot.bundle rampilot/device.sh comma@COMMA_IP:/data/ram-diag/rampilot/
echo "$SHA"
```

## 2. Check, then install (on the comma)

```bash
cd /data/ram-diag/rampilot
bash device.sh preflight
bash device.sh install rampilot.bundle <SHA> --reboot
```

Install refuses, and changes nothing, if:
- the truck is onroad
- the checkout has local edits
- the bundle is not exactly `<SHA>`
- the branch does not contain the baseline
- the change touches Panda, safety, model or hardware paths
- the change includes binary files

It records the current state, turns the sunnypilot updater off and sets aside any staged update. Then it checks out `rampilot-tici` and reboots.

## 3. Verify (truck in Park, ignition on)

```bash
bash /data/ram-diag/rampilot/device.sh verify
```

This checks that the comma runs the installed commit and that controlsd, card, selfdrived, paramsd and soundd stay up for 30 s. It also shows the blind-spot startup log line.

## Going back

```bash
bash /data/ram-diag/rampilot/device.sh rollback --reboot
```

This returns to the recorded branch at the baseline commit and restores the previous DisableUpdates value. `device.sh status` shows the current state at any time.

**Neither direction touches:**
- Panda firmware
- `/data/continue.sh`
- the advisor in `/data/ram-diag`
- sunnypilot settings other than DisableUpdates
