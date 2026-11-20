# Phone, Raspberry Pi, and Railway access to a PARKED Comma

**Purpose:** execute the one pending **passive diagnostic-only** update in [PR #49](https://github.com/wholesalebridgeowner/sunnypilot/pull/49) without a Windows PC. It neither replaces the driving controller nor modifies EPS, ACC, Panda or the OEM safety system. This is **not** the final steering calibration or a road-use release.

**Source pins (do not switch to unpinned main):**
- Operator scripts: immutable GitHub commit `99975835d3a0255996a5cac2ce7b30b69b47145b`.
- Termius device-side Python installer Git blob `a04c069b2e7daa40c9c66f6e2191ceedad3ada84`.
- Termux/Pi host-side Bash installer Git blob `8f063e76ff8a4536aceec03be124de85d1934361`.
- Diagnostic source and acceptance scripts: pinned parent commit `dc94a83a121d7e53d7605973cb2efae3719f140d`, file hashes hard-coded and checked before copying.
- Installed Sunnypilot build required by preflight: `1f7233cb983f3c13fdbba47a86f8fbc796ad9211`. The installer refuses a different or modified installed tree.

## A. Android phone with Termius (no second PC, no new local shell app)

1. Put the Comma and Android phone on the same trusted home Wi-Fi. Open an **authorized SSH session as `comma`** in Termius. Reuse the host key only after verifying its fingerprint. A phone hotspot also works when the device actually uses it, but the local Comma address changes with the network; don't assume a saved hotspot address is its home Wi-Fi address.
2. Paste this **bootstrap** command inside the Comma's SSH terminal. It downloads one pinned public Python installer file, compares its exact Git blob hash, and does **read-only preflight**:

```sh
curl -fsSLo /tmp/ram-parked-device.py \
  https://raw.githubusercontent.com/wholesalebridgeowner/sunnypilot/99975835d3a0255996a5cac2ce7b30b69b47145b/ram1500-development/comma_device_parked.py &&
test "$(git hash-object /tmp/ram-parked-device.py)" = a04c069b2e7daa40c9c66f6e2191ceedad3ada84 &&
/usr/local/venv/bin/python3 /tmp/ram-parked-device.py preflight
```

If `curl` is unavailable on the Comma, use the **Python-only** alternative below. It makes the same pinned HTTPS request, computes the Git blob SHA-1, and will refuse to execute any changed script:

```sh
/usr/local/venv/bin/python3 -c 'import hashlib,urllib.request,pathlib; u="https://raw.githubusercontent.com/wholesalebridgeowner/sunnypilot/99975835d3a0255996a5cac2ce7b30b69b47145b/ram1500-development/comma_device_parked.py"; b=urllib.request.urlopen(u,timeout=25).read(200000); h=hashlib.sha1(b"blob "+str(len(b)).encode()+b"\\0"+b).hexdigest(); assert h=="a04c069b2e7daa40c9c66f6e2191ceedad3ada84"; pathlib.Path("/tmp/ram-parked-device.py").write_bytes(b)' &&
/usr/local/venv/bin/python3 /tmp/ram-parked-device.py preflight
```

Do not disable HTTPS or bypass the checksum.

3. If preflight reports `parked_device_preflight_pass`, the vehicle is confirmed physically parked, and you approve this **diagnostic-only** change, run:

```sh
/usr/local/venv/bin/python3 /tmp/ram-parked-device.py install --parked --approve-diagnostic-install
```

This downloads three exact source-pinned files, checks their Git hashes, and backs up originals before moving only diagnostic files under `/data/ram-diag/`. **It does not start the logger.**

4. For the supervised two-minute *parked/onroad* acceptance session, the truck must be in **Park**, ignition must permit normal onroad telemetry and device must have available msgq subscriber slots. Then run **once per Comma reboot**:

```sh
/usr/local/venv/bin/python3 /tmp/ram-parked-device.py capture --parked --approve-diagnostic-install
```

The script checks subscriber headroom without subscribing, uses a fresh owner-only capture folder, stops on motion, limits CPU/private RAM, and prints only a pass/fail aggregate. A failure means **no acceptance**, and never justifies an automatic retry; a fresh parked device reboot and preflight are necessary before another capture because msgq slots do not free when readers exit.

5. Restore the previous diagnostic files if needed (still parked):

```sh
/usr/local/venv/bin/python3 /tmp/ram-parked-device.py rollback --parked --approve-diagnostic-install
```

## B. Android Termux or Raspberry Pi Linux shell

From a local Termux/Pi terminal with `ssh`, `scp`, `git`, `curl` and an authorized SSH key:

```sh
curl -fsSLo comma_parked_remote.sh \
  https://raw.githubusercontent.com/wholesalebridgeowner/sunnypilot/99975835d3a0255996a5cac2ce7b30b69b47145b/ram1500-development/comma_parked_remote.sh
test "$(git hash-object comma_parked_remote.sh)" = 8f063e76ff8a4536aceec03be124de85d1934361
bash comma_parked_remote.sh preflight <COMMA_LAN_OR_PRIVATE_VPN_HOST>
bash comma_parked_remote.sh install <COMMA_LAN_OR_PRIVATE_VPN_HOST> I_AM_PARKED
bash comma_parked_remote.sh capture <COMMA_LAN_OR_PRIVATE_VPN_HOST> I_AM_PARKED
# Only if required:
bash comma_parked_remote.sh rollback <COMMA_LAN_OR_PRIVATE_VPN_HOST> I_AM_PARKED
```

**Connectivity requirement:** the phone or Pi must already reach TCP/22 on the Comma over a private trusted network. A Raspberry Pi at Sunnyslope is not automatically on the same LAN as a Comma at home. An already configured private VPN between the Pi and the Comma/home network can work; no public router port forwarding is required or recommended.

## C. Railway / Codex workflow

GitHub and Railway already support code review, CI, private log collection and analysis. They **cannot use the existing Comma cloud log API to execute arbitrary shell commands or copy files onto the Comma**. The Comma is behind a private network. Therefore no cloud "Install" button exists until a separately approved, authenticated **outbound** device agent or private VPN reachability is established. A one-time authenticated session from the phone is the realistic initial bootstrap. Do not expose the Comma's port 22 publicly, publish device auth secrets, or claim a cloud deployment changed the vehicle.

### Current acceptance gates

All phone/Pi workflows affect diagnostics only. The 11-step Ram development plan's numerical calibration and source-exact on-road controller-release gates are **not** complete. A completed diagnostic install is a validated telemetry tool only if its hardware acceptance test passes. A real steering/ACC code change must be held-out replayed and independently reviewed first.

