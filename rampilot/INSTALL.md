# Installing RamPilot on the comma three, and going back

`rampilot/device.sh` runs on the comma. It installs the RamPilot branch from a git bundle, and rolls back to the pinned sunnypilot baseline (`1f7233cb`, branch `staging-tici`). Its offline tests are `rampilot/tests/test_device.sh` (Linux or WSL).

**Before you start**
- The truck is parked, in Park, and switched off, so the comma is offroad.
- The comma is on home or cabin Wi-Fi. SSH does not work over the phone hotspot.
- The comma's address and SSH access are known.
- For a release that changes `selfdrive/ui/ui`, retain the SHA-256 of the independently reviewed screen executable and its approval alongside the approved commit. Use that recorded digest during installation.

## 1. Copy the bundle and script (laptop)

```bash
BASE=1f7233cb983f3c13fdbba47a86f8fbc796ad9211
SHA=$(git rev-parse rampilot-tici)
git bundle create rampilot.bundle "$BASE..rampilot-tici"
ssh comma@COMMA_IP 'mkdir -p /data/ram-diag/rampilot'
git show rampilot-tici:rampilot/device.sh > device.sh   # the committed copy, LF line endings
scp rampilot.bundle device.sh comma@COMMA_IP:/data/ram-diag/rampilot/
echo "$SHA"
```

## 2. Check, then install (on the comma)

```bash
cd /data/ram-diag/rampilot
bash device.sh preflight
bash device.sh install rampilot.bundle <SHA> --reboot
```

For a release that changes the screen executable, use the full approved commit and the separately recorded 64-digit screen digest.

```bash
bash device.sh install rampilot.bundle <SHA> --ui-sha256 <APPROVED_UI_SHA256> --reboot
```

Installation and rollback require a readable `IsOnroad` file containing exactly `0`. The installer checks the clean tracked checkout, the bundle branch against the supplied commit, baseline ancestry and protected paths. A changed screen executable must have Git mode `100755`. Its Git blob and the entry in `rampilot/ALLOWED_BINARIES` must match the independently supplied digest. The installer also checks the executable on disk. Other binary changes are refused.

The first install records the baseline branch, commit and current `DisableUpdates` value. Upgrades reuse that rollback record. The installer checks out the target before disabling the updater and setting aside a staged update. A successful install records the new target for verification. A checkout failure leaves the previous checkout, updater setting and staged marker in place. If an install is interrupted, retry the same approved commit and screen digest, if applicable, before rebooting.

## 3. Verify (truck in Park, ignition on)

```bash
bash /data/ram-diag/rampilot/device.sh verify
```

This checks that the checkout matches the installed commit and that the same process IDs for controlsd, card, selfdrived, paramsd, soundd and the native UI are present at the start and end of a 30-second interval. It also shows the blind-spot startup log line.

## Going back

```bash
bash /data/ram-diag/rampilot/device.sh rollback --reboot
```

Rollback returns to the original baseline branch and restores its `DisableUpdates` value even after several RamPilot upgrades. A new install after rollback records the current baseline settings again. For installs made with the older script, rollback uses the archived install history to find the baseline record. If the history is incomplete, the script reports that it cannot establish the original state. `device.sh status` shows the latest install record.

**Neither direction touches:**
- Panda firmware
- `/data/continue.sh`
- the advisor in `/data/ram-diag`
- sunnypilot settings other than DisableUpdates
