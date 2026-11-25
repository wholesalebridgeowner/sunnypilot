# Installing RamPilot on the comma three, and going back

`rampilot/device.sh` runs on the comma. It installs the RamPilot branch from a git bundle, and rolls back to the pinned sunnypilot baseline (`1f7233cb`, branch `staging-tici`). Its offline tests are `rampilot/tests/test_device.sh` (Linux or WSL).

**Before you start**
- The truck is parked, in Park, and switched off, so the comma is offroad.
- The comma is on home or cabin Wi-Fi. SSH does not work over the phone hotspot.
- The comma's address and SSH access are known.
- For a release that changes `selfdrive/ui/ui`, retain the SHA-256 of the independently reviewed screen executable and its approval alongside the approved commit. Use that recorded digest during installation.
- For a release that carries new Panda firmware, retain the firmware's SHA-256 and signature prefix from the two matching laptop builds, and Mark's go for each step. See "Releases with Panda firmware" below.

For an approved screen rebuild, the [screen builder](ui/README.md) locates the device tools in `/usr/local/venv/bin` even in a non-login shell. If it reports missing SCons, check that the device toolchain is present before retrying.

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

Installation and rollback require a readable `IsOnroad` file containing exactly `0`. The installer checks the clean tracked checkout, the bundle branch against the supplied commit, baseline ancestry and protected paths. A changed screen executable must have Git mode `100755`. Its Git blob and the entry in `rampilot/ALLOWED_BINARIES` must match the independently supplied digest. The installer also checks the executable on disk. Other binary changes are refused, except the one pinned Panda firmware file described below.

The first install records the baseline branch, commit and current `DisableUpdates` value. Upgrades reuse that rollback record. The installer checks out the target before disabling the updater and setting aside a staged update. A successful install records the new target for verification. A checkout failure leaves the previous checkout, updater setting and staged marker in place. If an install is interrupted, retry the same approved commit and screen digest, if applicable, before rebooting.

## 3. Verify (truck in Park, ignition on)

```bash
bash /data/ram-diag/rampilot/device.sh verify
```

This checks that the checkout matches the installed commit and that the same process IDs for controlsd, card, selfdrived, paramsd, soundd and the native UI are present at the start and end of a 30-second interval. It also shows the blind-spot startup log line. After a release with Panda firmware it also checks the Panda (next section).

## Releases with Panda firmware

The Panda is the safety chip inside the comma; every message to the truck passes through it. This truck's comma three has the F4 type of internal Panda, so its firmware file is `panda/board/obj/panda.bin.signed` (checked on the truck: the Panda reports the stock signature of that file, `1eb681e4608e1425`). A RamPilot release that changes the Panda's rules carries one new copy of that file, built on the laptop from the release's own safety code. Each step below is a separate go from Mark, with the truck in Park and switched off unless the step says otherwise.

**What the installer accepts.** It accepts the firmware file only when all of these hold, and refuses the release otherwise:
- `rampilot/ALLOWED_BINARIES` in the release pins the firmware's SHA-256, and pins the Git tree of `opendbc_repo/opendbc/safety` the firmware was built from.
- The firmware file, its pin and the `--panda-sha256` digest you type are the same SHA-256, before the checkout and on disk after it.
- The release changes the safety code, and only these safety files: `modes/chrysler.h`, the packet-forwarding hook in `safety.h` and `safety_declarations.h`, and the tests under `tests/` (all in `opendbc_repo/opendbc/safety/`). That safety code matches the pinned tree exactly.
- Every Panda driver patch in `rampilot/panda-patches/` is a plain file with its own SHA-256 pin in `rampilot/ALLOWED_BINARIES`, and every patch pin names a file in the release. These are the exact patches the laptop build applied to the Panda source for this firmware. The comma does not use them; the pins record where the firmware came from.
- The file has the signed firmware layout (length field, `VERS` marker, 128-byte signature) and a signature different from stock.
- Nothing else under `panda/` or `opendbc_repo/opendbc/safety/` changes. The firmware for the other Panda type (`panda_h7.bin.signed`) and both bootstub files stay stock.
- The comma is the one this firmware was tested for: the Panda's own report (`PandaSignatures`) right now equals the signature of the F4 firmware file already on the comma, or of the stock F4 file. Any other report is refused. If the comma has not reported the Panda yet, the installer says so; wait a minute after start-up and run the install again.

Safety code without the firmware is refused, firmware without a safety code change is refused, and a release without new firmware that adds or changes a Panda patch is refused: firmware, safety code and patches always change together. The digital signature itself is checked on the laptop before the release is approved.

**Install** (offroad, without `--reboot`, so the changed-files list can be read before the Panda changes):

```bash
cd /data/ram-diag/rampilot
bash device.sh install rampilot.bundle <SHA> --panda-sha256 <APPROVED_PANDA_SHA256>
```

Add `--ui-sha256 <APPROVED_UI_SHA256>` when the release also changes the screen. The installer does not flash the Panda. It prints the firmware's signature prefix and the stock one, and each pinned Panda patch with its SHA-256. It records both signature prefixes, with the firmware's SHA-256 and the safety tree, in the install record (`bash device.sh status`).

**Restart and check** (truck still off):

```bash
sudo reboot
# wait 3 minutes, then reconnect
bash /data/ram-diag/rampilot/device.sh panda-status
```

At each start the comma compares the Panda's firmware with the file and, when they differ, installs the file into the Panda by itself. `panda-status` only reads files: it does not talk to the Panda, stop anything or change anything, and it works with the ignition on too. It shows:
- the firmware file's SHA-256 and signature prefix, and whether that file is stock or the pinned firmware of the last install;
- the Panda's own report: `MATCH` means the Panda runs that file; `MISMATCH` means the Panda still runs other firmware; `NOT REPORTED` means the comma has not finished checking the Panda since it started (wait a minute and run it again);
- the comma's latest Panda log lines, without the Panda's serial number. After new firmware, expect "Panda firmware out of date, update required" followed by "Done flashing". The time between those two lines is how long the update took. "Flashed firmware not booting", "Panda still not booting" or "Version mismatch after flashing" means the new firmware failed: roll back.

**Verify** (ignition on, in Park): `bash /data/ram-diag/rampilot/device.sh verify` checks the processes as above, then checks that the firmware file on disk has the recorded SHA-256 and that the Panda reports the new signature (`ok   the Panda runs the installed firmware`). If the Panda shows anything else, verify prints `FAIL` lines and ends with `REFUSED: the Panda does not show the installed firmware (FAILED)`; roll back.

## Going back

```bash
bash /data/ram-diag/rampilot/device.sh rollback --reboot
```

Rollback returns to the original baseline branch and restores its `DisableUpdates` value even after several RamPilot upgrades. A new install after rollback records the current baseline settings again. For installs made with the older script, rollback uses the archived install history to find the baseline record. If the history is incomplete, the script reports that it cannot establish the original state. `device.sh status` shows the latest install record.

The baseline checkout also puts back the stock Panda firmware file. Rollback prints the stock signature prefix (`1eb681e4608e1425` on this truck's baseline). At the next start the comma puts stock firmware back into the Panda by itself. After that restart, run `bash /data/ram-diag/rampilot/device.sh panda-status` and look for `MATCH` on the stock signature.

**Neither direction touches:**
- the Panda directly: the installer only changes the file, and the comma installs it into the Panda at the next start
- `/data/continue.sh`
- the advisor in `/data/ram-diag`
- sunnypilot settings other than DisableUpdates
