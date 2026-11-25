#!/usr/bin/env bash
# Offline tests for rampilot/device.sh against a simulated device (a small synthetic repo).
# Run on Linux or WSL: bash rampilot/tests/test_device.sh
set -uo pipefail
umask 022
HERE="$(cd "$(dirname "$0")" && pwd)"
DEV="$HERE/../device.sh"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
PASS=0; FAIL=0
ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL $1"; }
expect_refused() { local name="$1" why="$2"; shift 2; if out="$("$@" 2>&1)"; then bad "$name (was not refused)"; echo "$out" | sed 's/^/     /'; else echo "$out" | grep -q "REFUSED: .*$why" && ok "$name" || { bad "$name"; echo "$out" | sed 's/^/     /'; }; fi; }
expect_ok() { local name="$1"; shift; if out="$("$@" 2>&1)"; then ok "$name"; else bad "$name"; echo "$out" | sed 's/^/     /'; fi; }

g() { git -c user.name=t -c user.email=t@example.invalid "$@"; }
sha256() { sha256sum "$1" | cut -c1-64; }
# A file in the signed Panda firmware layout (panda crypto/sign.py, SETLEN=1): little-endian length of
# everything before the signature, the image ($2), "VERS" + version 2, then 128 signature bytes, here one
# repeated byte ($3, hex). No CR bytes, so the line-ending test below converts it reversibly.
mkfw() {
  local n=$((4 + ${#2} + 8)) i
  { printf "\\x$(printf %02x $((n & 255)))\\x$(printf %02x $((n >> 8 & 255)))\\x00\\x00"
    printf '%s' "$2"; printf 'VERS\x02\x00\x00\x00'
    for ((i = 0; i < 128; i++)); do printf "\\x$3"; done; } > "$1"
}
sigbytes() { local i; for ((i = 0; i < 128; i++)); do printf "\\x$1"; done; }  # 128 bytes of $1 (hex)

# Upstream: baseline plus candidate branches
UP="$W/upstream"; FW=panda/board/obj/panda.bin.signed; H7=panda/board/obj/panda_h7.bin.signed; SAFETY=opendbc_repo/opendbc/safety
mkdir -p "$UP/selfdrive/ui" "$UP/panda/board/obj" "$UP/$SAFETY/modes" "$UP/$SAFETY/tests"; cd "$UP"
g init -q -b staging-tici
echo "x = 1" > selfdrive/a.py; printf '\x00\x01\x02' > panda/fw.bin; echo base > README
printf '\x7fELF\x00stock screen' > selfdrive/ui/ui; chmod 755 selfdrive/ui/ui
STOCK_UI_SHA="$(sha256 selfdrive/ui/ui)"
# This truck's internal Panda is the F4 type: pandad flashes panda.bin.signed. The H7 file stays stock.
mkfw "$FW" $'stock f4 image\nwith the stock rules\n' 1e; mkfw "$H7" $'stock h7 image\n' 1a
printf '\x00stock h7 bootstub' > panda/board/obj/bootstub.panda_h7.bin; printf '\x00stock f4 bootstub' > panda/board/obj/bootstub.panda.bin
echo 'int chrysler_stock;' > "$SAFETY/modes/chrysler.h"; echo 'int honda_stock;' > "$SAFETY/modes/honda.h"
echo '# stock tests' > "$SAFETY/tests/test_chrysler.py"; echo '// stock lateral' > "$SAFETY/lateral.h"
echo '// stock safety' > "$SAFETY/safety.h"; echo '// stock declarations' > "$SAFETY/safety_declarations.h"
STOCK_FW_SHA="$(sha256 "$FW")"
g add -A && g commit -qm baseline
BASE="$(git rev-parse HEAD)"
g checkout -qb rampilot-tici; echo "x = 2" > selfdrive/a.py; g commit -qam good; GOOD="$(git rev-parse HEAD)"
g checkout -qb update; echo "x = 3" > selfdrive/a.py; g commit -qam update; UPDATED="$(git rev-parse HEAD)"
g checkout -qb bad-panda "$BASE"; echo "y" > panda/notes.txt; g add -A && g commit -qm panda; BADP="$(git rev-parse HEAD)"
g checkout -qb bad-binary "$BASE"; printf '\x00\xff\x00' > selfdrive/blob.dat; g add -A && g commit -qm bin; BADB="$(git rev-parse HEAD)"
g checkout -q --orphan unrelated; g rm -rqf . ; echo z > z.py; g add -A && g commit -qm orphan; ORPH="$(git rev-parse HEAD)"
g checkout -q staging-tici
g bundle create -q "$W/good.bundle" "$BASE..rampilot-tici" 2>/dev/null
g branch -f rampilot-tici "$UPDATED"; g bundle create -q "$W/update.bundle" "$BASE..rampilot-tici" 2>/dev/null
g branch -f rampilot-tici "$BADP"; g bundle create -q "$W/badp.bundle" "$BASE..rampilot-tici" 2>/dev/null
g branch -f rampilot-tici "$BADB"; g bundle create -q "$W/badb.bundle" "$BASE..rampilot-tici" 2>/dev/null
g branch -f rampilot-tici "$ORPH"; g bundle create -q "$W/orph.bundle" rampilot-tici 2>/dev/null
# Screen-program binaries: pinned (accepted), wrong pin, and a pinned binary at a path that is not allowed
pin() { printf '%s  %s\n' "$(sha256 "$1")" "$1" > rampilot/ALLOWED_BINARIES; }
printf '\x7fELF\x00ui1' > "$W/reviewed-ui"; UI_APPROVED_SHA="$(sha256 "$W/reviewed-ui")"
g checkout -qb ui-ok "$BASE"; mkdir -p rampilot; cat "$W/reviewed-ui" > selfdrive/ui/ui; pin selfdrive/ui/ui
g add -A && g commit -qm ui; UIOK="$(git rev-parse HEAD)"
g checkout -qb ui-badsha "$BASE"; mkdir -p selfdrive/ui rampilot; printf '\x7fELF\x00ui2' > selfdrive/ui/ui
UI_BAD_MANIFEST_SHA="$(sha256 selfdrive/ui/ui)"
printf '%064d  selfdrive/ui/ui\n' 0 > rampilot/ALLOWED_BINARIES; g add -A && g commit -qm uibad; UIBAD="$(git rev-parse HEAD)"
g checkout -qb ui-noexec "$UIOK"; chmod 644 selfdrive/ui/ui; g commit -qam "screen missing execute mode"; UINOEXEC="$(git rev-parse HEAD)"
g checkout -qb ui-mode-only "$BASE"; chmod 644 selfdrive/ui/ui; mkdir -p rampilot; pin selfdrive/ui/ui
g add -A && g commit -qm "stock screen lost execute mode"; UIMODEONLY="$(git rev-parse HEAD)"
g checkout -qb bin-listed "$BASE"; mkdir -p selfdrive/x rampilot; printf '\x7fELF\x00x' > selfdrive/x/prog; pin selfdrive/x/prog
g add -A && g commit -qm other; BINL="$(git rev-parse HEAD)"
# Panda firmware candidates. fwpins writes rampilot/ALLOWED_BINARIES as a reviewed Phase A build would: the
# firmware digest ($1; "-" leaves it out), the safety tree ($2; empty means the true staged values) and every
# Panda driver patch present in rampilot/panda-patches/.
mkfw "$W/rampilot-fw" $'rampilot f4 image\nwith the add-braking-only rule\n' ba; FW_SHA="$(sha256 "$W/rampilot-fw")"
BOGUS_SHA="$(printf '%064d' 7)"
PATCH=rampilot/panda-patches/0001-packet-aware-forwarding.patch
fwpins() {
  mkdir -p rampilot; g add -A
  { [ "${1:-}" = - ] || printf '%s  %s\n' "${1:-$(sha256 "$FW")}" "$FW"
    printf '%s  %s\n' "${2:-$(git write-tree --prefix="$SAFETY/")}" "$SAFETY"
    for f in rampilot/panda-patches/*; do [ ! -f "$f" ] || printf '%s  %s\n' "$(sha256 "$f")" "$f"; done; } > rampilot/ALLOWED_BINARIES
  g add -A
}
safety_edit() { echo 'int chrysler_rampilot_long;' >> "$SAFETY/modes/chrysler.h"; }
hook_edit() { echo '// optional fwd_packet hook' >> "$SAFETY/safety.h"; echo '// fwd_packet declaration' >> "$SAFETY/safety_declarations.h"; }
tests_edit() { echo '# rampilot tests' >> "$SAFETY/tests/test_chrysler.py"; }
patch_add() { mkdir -p rampilot/panda-patches; printf 'Subject: packet-aware forwarding\n--- a/board/main.c\n+++ b/board/main.c\n' > "$PATCH"; }
patch_fw() { printf "$2" | dd of="$FW" bs=1 seek="$1" conv=notrunc status=none; }  # $1 = offset, $2 = bytes
g checkout -qb fw-ok "$BASE"; safety_edit; hook_edit; tests_edit; patch_add; cp "$W/rampilot-fw" "$FW"; fwpins
PATCH_SHA="$(sha256 "$PATCH")"
# A note line shaped like a pin must be ignored.
{ printf '#%064d  %s\n' 0 "$FW"; cat rampilot/ALLOWED_BINARIES; } > "$W/pins"; cat "$W/pins" > rampilot/ALLOWED_BINARIES
g add -A && g commit -qm fw-ok; FWOK="$(git rev-parse HEAD)"; FW_TREE="$(git rev-parse "$FWOK:$SAFETY")"
g checkout -qb fw-nopin "$BASE"; safety_edit; cp "$W/rampilot-fw" "$FW"; fwpins -; g commit -qm nopin; FWNOPIN="$(git rev-parse HEAD)"
g checkout -qb fw-dup "$FWOK"; printf '%s  %s\n' "$FW_SHA" "$FW" >> rampilot/ALLOWED_BINARIES; g commit -qam dup; FWDUP="$(git rev-parse HEAD)"
g checkout -qb fw-badsha "$BASE"; safety_edit; cp "$W/rampilot-fw" "$FW"; fwpins "$BOGUS_SHA"; g commit -qm badsha; FWBADSHA="$(git rev-parse HEAD)"
g checkout -qb safety-only "$BASE"; safety_edit; g commit -qam safety-only; SAFEONLY="$(git rev-parse HEAD)"
g checkout -qb safety-tests-only "$BASE"; tests_edit; g commit -qam safety-tests-only; SAFETESTONLY="$(git rev-parse HEAD)"
g checkout -qb fw-only "$BASE"; cp "$W/rampilot-fw" "$FW"; fwpins; g commit -qm fw-only; FWONLY="$(git rev-parse HEAD)"
g checkout -qb fw-tests "$BASE"; tests_edit; cp "$W/rampilot-fw" "$FW"; fwpins; g commit -qm fw-tests; FWTESTS="$(git rev-parse HEAD)"
g checkout -qb fw-badtree "$BASE"; safety_edit; cp "$W/rampilot-fw" "$FW"; fwpins "" "$(git rev-parse "$BASE:$SAFETY")"
g commit -qm badtree; FWBADTREE="$(git rev-parse HEAD)"
g checkout -qb fw-h7 "$FWOK"; mkfw "$H7" $'rampilot h7 image\n' 3c; g commit -qam h7; FWH7="$(git rev-parse HEAD)"
g checkout -qb fw-boot "$FWOK"; printf '\x00other h7 bootstub' > panda/board/obj/bootstub.panda_h7.bin; g commit -qam boot; FWBOOT="$(git rev-parse HEAD)"
g checkout -qb fw-bootf4 "$FWOK"; printf '\x00other f4 bootstub' > panda/board/obj/bootstub.panda.bin; g commit -qam bootf4; FWBOOTF4="$(git rev-parse HEAD)"
g checkout -qb fw-other "$FWOK"; mkdir -p panda/python; echo 'x = 1' > panda/python/extra.py; g add -A && g commit -qm other; FWOTHER="$(git rev-parse HEAD)"
g checkout -qb fw-honda "$FWOK"; echo 'int honda_changed;' >> "$SAFETY/modes/honda.h"; fwpins; g commit -qm honda; FWHONDA="$(git rev-parse HEAD)"
g checkout -qb fw-mode "$FWOK"; chmod 755 "$FW"; g commit -qam mode; FWMODE="$(git rev-parse HEAD)"
g checkout -qb fw-novers "$BASE"; safety_edit; cp "$W/rampilot-fw" "$FW"; patch_fw $(($(stat -c %s "$FW") - 136)) XERS
FWNOVERS_SHA="$(sha256 "$FW")"; fwpins; g commit -qm novers; FWNOVERS="$(git rev-parse HEAD)"
g checkout -qb fw-badlen "$BASE"; safety_edit; cp "$W/rampilot-fw" "$FW"; patch_fw 0 '\x01'
FWBADLEN_SHA="$(sha256 "$FW")"; fwpins; g commit -qm badlen; FWBADLEN="$(git rev-parse HEAD)"
g checkout -qb fw-stocksig "$BASE"; safety_edit; mkfw "$FW" $'rampilot image with the stock signature\n' 1e
FWSTOCKSIG_SHA="$(sha256 "$FW")"; fwpins; g commit -qm stocksig; FWSTOCKSIG="$(git rev-parse HEAD)"
# Line-ending conversion on checkout: the blob matches every pin, the file Git writes does not.
g checkout -qb fw-crlf "$FWOK"; printf '*.signed text eol=crlf\n' > .gitattributes; g add .gitattributes && g commit -qm crlf; FWCRLF="$(git rev-parse HEAD)"
# The generic packet hook (safety.h, safety_declarations.h) and the Panda driver patches
g checkout -qb safety-h-only "$BASE"; echo '// hook' >> "$SAFETY/safety.h"; g commit -qam safety-h; SAFEHONLY="$(git rev-parse HEAD)"
g checkout -qb safety-decl-only "$BASE"; echo '// hook' >> "$SAFETY/safety_declarations.h"; g commit -qam safety-decl; SAFEDECLONLY="$(git rev-parse HEAD)"
g checkout -qb fw-hook "$BASE"; hook_edit; cp "$W/rampilot-fw" "$FW"; fwpins; g commit -qm fw-hook; FWHOOK="$(git rev-parse HEAD)"
g checkout -qb fw-lateral "$FWOK"; echo '// changed' >> "$SAFETY/lateral.h"; fwpins; g commit -qm lateral; FWLATERAL="$(git rev-parse HEAD)"
g checkout -qb patch-nofw "$BASE"; patch_add; mkdir -p rampilot; printf '%s  %s\n' "$PATCH_SHA" "$PATCH" > rampilot/ALLOWED_BINARIES
g add -A && g commit -qm patch-nofw; PATCHNOFW="$(git rev-parse HEAD)"
g checkout -qb fw-patch-nopin "$FWOK"; echo 'Subject: extra' > rampilot/panda-patches/0002-extra.patch; g add -A && g commit -qm nopin; FWPATCHNOPIN="$(git rev-parse HEAD)"
g checkout -qb fw-patch-badsha "$FWOK"; echo '+changed after review' >> "$PATCH"; g commit -qam badsha; FWPATCHBADSHA="$(git rev-parse HEAD)"
g checkout -qb fw-patch-dup "$FWOK"; printf '%s  %s\n' "$PATCH_SHA" "$PATCH" >> rampilot/ALLOWED_BINARIES; g commit -qam dup; FWPATCHDUP="$(git rev-parse HEAD)"
g checkout -qb fw-patch-stale "$FWOK"; printf '%s  %s\n' "$BOGUS_SHA" rampilot/panda-patches/0003-missing.patch >> rampilot/ALLOWED_BINARIES
g commit -qam stale; FWPATCHSTALE="$(git rev-parse HEAD)"
g checkout -qb fw-patch-mode "$FWOK"; chmod 755 "$PATCH"; g commit -qam mode; FWPATCHMODE="$(git rev-parse HEAD)"
g checkout -q staging-tici
for n in UIOK UIBAD UINOEXEC UIMODEONLY BINL FWOK FWNOPIN FWDUP FWBADSHA SAFEONLY SAFETESTONLY FWONLY FWTESTS FWBADTREE \
         FWH7 FWBOOT FWBOOTF4 FWOTHER FWHONDA FWMODE FWNOVERS FWBADLEN FWSTOCKSIG FWCRLF SAFEHONLY SAFEDECLONLY FWHOOK \
         FWLATERAL PATCHNOFW FWPATCHNOPIN FWPATCHBADSHA FWPATCHDUP FWPATCHSTALE FWPATCHMODE; do
  g branch -f rampilot-tici "${!n}"; g bundle create -q "$W/$n.bundle" "$BASE..rampilot-tici" 2>/dev/null
done
g branch -f rampilot-tici "$GOOD"

# Simulated device: only the baseline, params, a staged update
DEVICE="$W/data"; mkdir -p "$DEVICE/params/d" "$DEVICE/safe_staging/finalized" "$DEVICE/ram-diag"
git clone -q --single-branch -b staging-tici "$UP" "$DEVICE/openpilot"
printf 0 > "$DEVICE/params/d/DisableUpdates"; printf 0 > "$DEVICE/params/d/IsOnroad"; touch "$DEVICE/safe_staging/finalized/.overlay_consistent"
export OP_DIR="$DEVICE/openpilot" PARAMS_DIR="$DEVICE/params/d" STAGING_ROOT="$DEVICE/safe_staging" STATE_DIR="$DEVICE/ram-diag/rampilot"
export BASELINE_SHA="$BASE" REBOOT_CMD="touch $W/rebooted" MIN_FREE_MB=1
head() { git -C "$OP_DIR" rev-parse HEAD; }

expect_ok "preflight passes on a clean offroad device" bash "$DEV" preflight
expect_refused "rollback with no recorded install" "no recorded install state" bash "$DEV" rollback
rm "$PARAMS_DIR/IsOnroad"
expect_refused "preflight refuses missing IsOnroad" "" bash "$DEV" preflight
mkdir "$PARAMS_DIR/IsOnroad"
expect_refused "preflight refuses IsOnroad that is not a readable parameter file" "" bash "$DEV" preflight
rmdir "$PARAMS_DIR/IsOnroad"
: > "$PARAMS_DIR/IsOnroad"
expect_refused "preflight refuses empty IsOnroad" "" bash "$DEV" preflight
printf unknown > "$PARAMS_DIR/IsOnroad"
expect_refused "preflight refuses ambiguous IsOnroad" "" bash "$DEV" preflight
printf 1 > "$PARAMS_DIR/IsOnroad"
expect_refused "install refused while onroad" "onroad" bash "$DEV" install "$W/good.bundle" "$GOOD"
printf 0 > "$PARAMS_DIR/IsOnroad"
expect_refused "install refused for a short sha" "full 40-character" bash "$DEV" install "$W/good.bundle" "${GOOD:0:12}"
expect_refused "install refused for the wrong sha" "expected" bash "$DEV" install "$W/good.bundle" "$BADP"
expect_refused "install refused when Panda paths change" "protected paths: panda/notes.txt" bash "$DEV" install "$W/badp.bundle" "$BADP"
expect_refused "install refused when a binary file changes" "binary files: selfdrive/blob.dat" bash "$DEV" install "$W/badb.bundle" "$BADB"
expect_refused "install refused for a branch without the baseline" "does not contain baseline" bash "$DEV" install "$W/orph.bundle" "$ORPH"
echo "x = 9" > "$OP_DIR/selfdrive/a.py"
expect_refused "install refused with local edits" "uncommitted changes" bash "$DEV" install "$W/good.bundle" "$GOOD"
git -C "$OP_DIR" checkout -q -- selfdrive/a.py
[ "$(head)" = "$BASE" ] && [ "$(cat "$PARAMS_DIR/DisableUpdates")" = 0 ] && [ -f "$STAGING_ROOT/finalized/.overlay_consistent" ] \
  && ok "refusals changed nothing" || bad "refusals changed nothing"

expect_refused "install refused for a mismatched candidate manifest" "is not pinned" bash "$DEV" install "$W/UIBAD.bundle" "$UIBAD" --ui-sha256 "$UI_BAD_MANIFEST_SHA"
expect_refused "install refused for a pinned binary at another path" "binary files: selfdrive/x/prog" bash "$DEV" install "$W/BINL.bundle" "$BINL"

# An ordinary untracked file prevents Git from checking out the release's manifest.
mkdir -p "$OP_DIR/rampilot"
printf 'local file\n' > "$OP_DIR/rampilot/ALLOWED_BINARIES"
if ! LC_ALL=C bash "$DEV" install "$W/UIOK.bundle" "$UIOK" --ui-sha256 "$UI_APPROVED_SHA" > "$W/checkout-failure.log" 2>&1 &&
   grep -q 'would be overwritten by checkout' "$W/checkout-failure.log"; then
  ok "checkout refuses to overwrite the untracked file"
else
  bad "checkout refuses to overwrite the untracked file"
  cat "$W/checkout-failure.log"
fi
if [ "$(head)" = "$BASE" ] && [ "$(cat "$PARAMS_DIR/DisableUpdates")" = 0 ] &&
   [ -f "$STAGING_ROOT/finalized/.overlay_consistent" ] && [ ! -e "$STAGING_ROOT/finalized/.overlay_consistent.rampilot-set-aside" ] &&
   [ "$(cat "$OP_DIR/rampilot/ALLOWED_BINARIES")" = "local file" ]; then
  ok "failed checkout preserves the baseline, updater, marker and local file"
else
  bad "failed checkout preserves the baseline, updater, marker and local file"
fi
rm "$OP_DIR/rampilot/ALLOWED_BINARIES"
expect_ok "retry installs the approved screen after the conflict is removed" bash "$DEV" install "$W/UIOK.bundle" "$UIOK" --ui-sha256 "$UI_APPROVED_SHA"
[ "$(sha256 "$OP_DIR/selfdrive/ui/ui")" = "$UI_APPROVED_SHA" ] && [ -x "$OP_DIR/selfdrive/ui/ui" ] \
  && ok "installed executable has the approved bytes" || bad "installed executable has the approved bytes"
expect_ok "rollback after the screen binary" bash "$DEV" rollback
[ "$(head)" = "$BASE" ] && [ "$(sha256 "$OP_DIR/selfdrive/ui/ui")" = "$STOCK_UI_SHA" ] && [ "$(stat -c %a "$OP_DIR/selfdrive/ui/ui")" = 755 ] \
  && ok "rollback restores the stock screen SHA-256 and execute mode" || bad "rollback restores the stock screen SHA-256 and execute mode"
expect_ok "install succeeds" bash "$DEV" install "$W/good.bundle" "$GOOD"
[ "$(head)" = "$GOOD" ] && ok "device is on the candidate" || bad "device is on the candidate"
[ "$(git -C "$OP_DIR" rev-parse --abbrev-ref HEAD)" = rampilot-tici ] && ok "branch is rampilot-tici" || bad "branch is rampilot-tici"
[ "$(cat "$PARAMS_DIR/DisableUpdates")" = 1 ] && ok "updates turned off" || bad "updates turned off"
[ ! -f "$STAGING_ROOT/finalized/.overlay_consistent" ] && [ -f "$STAGING_ROOT/finalized/.overlay_consistent.rampilot-set-aside" ] \
  && ok "staged update set aside" || bad "staged update set aside"
[ ! -f "$W/rebooted" ] && ok "no reboot without --reboot" || bad "no reboot without --reboot"
expect_ok "second install is a no-op" bash "$DEV" install "$W/good.bundle" "$GOOD"
expect_ok "install a second release over the first" bash "$DEV" install "$W/update.bundle" "$UPDATED"
[ "$(head)" = "$UPDATED" ] && ok "second release checked out" || bad "second release checked out"
if status="$(bash "$DEV" status)" && [[ "$status" == *"branch=rampilot-tici head=$UPDATED"* && "$status" == *"target=$UPDATED"* ]]; then
  ok "status reports the second release as the installed target"
else
  bad "status reports the second release as the installed target"
fi

# verify: simulated driving processes (argv[0] set to the process names)
export VERIFY_PROCS="fake.controlsd fake.card" VERIFY_WAIT_S=1 LOG_DIR="$W/log"; mkdir -p "$LOG_DIR"
expect_refused "verify refused while offroad" "turn the ignition on" bash "$DEV" verify
printf 1 > "$PARAMS_DIR/IsOnroad"
( exec -a fake.controlsd sleep 30 ) & P1=$!
( exec -a fake.card sleep 30 ) & P2=$!
sleep 0.3
echo '{"event": "rampilot late BSM check", "bsm_in_fingerprint": true, "waited_s": 0.0}' > "$LOG_DIR/swaglog.1"
if verified="$(bash "$DEV" verify)" && [[ "$verified" == *"verify ok on $UPDATED"* ]]; then
  ok "verify confirms the second release with stable processes"
else
  bad "verify confirms the second release with stable processes"
fi
( sleep 0.5; kill $P2 2>/dev/null ) &
expect_refused "verify catches a stopped process" "not stable" bash "$DEV" verify
kill $P1 2>/dev/null; wait 2>/dev/null
printf 0 > "$PARAMS_DIR/IsOnroad"

expect_ok "rollback after two releases succeeds" bash "$DEV" rollback --reboot
[ "$(head)" = "$BASE" ] && ok "device is back on the baseline" || bad "device is back on the baseline"
[ "$(git -C "$OP_DIR" rev-parse --abbrev-ref HEAD)" = staging-tici ] && ok "original branch restored" || bad "original branch restored"
[ "$(cat "$PARAMS_DIR/DisableUpdates")" = 0 ] && ok "DisableUpdates restored" || bad "DisableUpdates restored"
[ -f "$W/rebooted" ] && ok "reboot requested with --reboot" || bad "reboot requested with --reboot"
[ -z "$(git -C "$OP_DIR" status --porcelain --untracked-files=no)" ] && ok "checkout clean after rollback" || bad "checkout clean after rollback"

expect_refused "UI install requires the independently approved hash" "" bash "$DEV" install "$W/UIOK.bundle" "$UIOK"
expect_refused "UI install refuses a different independently approved hash" "" bash "$DEV" install "$W/UIOK.bundle" "$UIOK" --ui-sha256 "$STOCK_UI_SHA"
expect_refused "UI install refuses a non-executable replacement" "" bash "$DEV" install "$W/UINOEXEC.bundle" "$UINOEXEC" --ui-sha256 "$UI_APPROVED_SHA"
expect_refused "UI install refuses loss of execute mode even if the bytes match" "" bash "$DEV" install "$W/UIMODEONLY.bundle" "$UIMODEONLY" --ui-sha256 "$STOCK_UI_SHA"

# --- Panda firmware: exactly one pinned file, in lockstep with the safety code (PANDA-FW-PLAN.md Phase B) ---
git -C "$UP" cat-file blob "$FWOK:rampilot/ALLOWED_BINARIES" | grep -qxF "$FW_TREE  $SAFETY" \
  && ok "fixture: the safety pin equals the candidate's committed safety tree" || bad "fixture: the safety pin equals the candidate's committed safety tree"
inst() { local n="$1"; shift; bash "$DEV" install "$W/$n.bundle" "${!n}" "$@"; }
printf 1 > "$PARAMS_DIR/IsOnroad"
expect_refused "firmware install refused while onroad" "onroad" inst FWOK --panda-sha256 "$FW_SHA"
printf 0 > "$PARAMS_DIR/IsOnroad"
expect_refused "firmware release refused without --panda-sha256" "requires --panda-sha256" inst FWOK
expect_refused "--panda-sha256 refused for a release without firmware" "does not change $FW" bash "$DEV" install "$W/good.bundle" "$GOOD" --panda-sha256 "$FW_SHA"
expect_refused "--panda-sha256 must be a full digest" "64-character" inst FWOK --panda-sha256 abc123
expect_refused "firmware without its pin" "$FW is not pinned exactly once" inst FWNOPIN --panda-sha256 "$FW_SHA"
expect_refused "firmware pinned twice" "$FW is not pinned exactly once" inst FWDUP --panda-sha256 "$FW_SHA"
expect_refused "--panda-sha256 that differs from the pin" "independent --panda-sha256 does not match" inst FWOK --panda-sha256 "$STOCK_FW_SHA"
expect_refused "pin and --panda-sha256 that differ from the file's hash" "not pinned to its blob digest" inst FWBADSHA --panda-sha256 "$BOGUS_SHA"
expect_refused "safety change without firmware" "without a pinned Panda firmware change" inst SAFEONLY
expect_refused "safety test change without firmware" "without a pinned Panda firmware change" inst SAFETESTONLY
expect_refused "firmware without a safety change" "without a safety source change" inst FWONLY --panda-sha256 "$FW_SHA"
expect_refused "firmware with only safety test changes" "without a safety source change" inst FWTESTS --panda-sha256 "$FW_SHA"
expect_refused "safety tree pin that differs from the candidate's tree" "safety tree pin" inst FWBADTREE --panda-sha256 "$FW_SHA"
expect_refused "H7 firmware change" "protected paths: panda/board/obj/panda_h7.bin.signed" inst FWH7 --panda-sha256 "$FW_SHA"
expect_refused "H7 bootstub change" "protected paths: panda/board/obj/bootstub.panda_h7.bin" inst FWBOOT --panda-sha256 "$FW_SHA"
expect_refused "F4 bootstub change" "protected paths: panda/board/obj/bootstub.panda.bin" inst FWBOOTF4 --panda-sha256 "$FW_SHA"
expect_refused "other panda/ path" "protected paths: panda/python/extra.py" inst FWOTHER --panda-sha256 "$FW_SHA"
expect_refused "safety file other than chrysler.h and tests" "protected paths: $SAFETY/modes/honda.h" inst FWHONDA --panda-sha256 "$FW_SHA"
expect_refused "firmware with execute mode" "Git mode 100644" inst FWMODE --panda-sha256 "$FW_SHA"
expect_refused "corrupted trailer (no VERS marker)" "no VERS marker" inst FWNOVERS --panda-sha256 "$FWNOVERS_SHA"
expect_refused "corrupted length field" "length field" inst FWBADLEN --panda-sha256 "$FWBADLEN_SHA"
expect_refused "firmware carrying the stock signature" "stock signature" inst FWSTOCKSIG --panda-sha256 "$FWSTOCKSIG_SHA"
# The packet hook files follow the same lockstep; other safety files and loose patches stay refused.
expect_refused "safety.h change without firmware" "without a pinned Panda firmware change: $SAFETY/safety.h" inst SAFEHONLY
expect_refused "safety_declarations.h change without firmware" "without a pinned Panda firmware change: $SAFETY/safety_declarations.h" inst SAFEDECLONLY
expect_refused "other top-level safety file with firmware" "protected paths: $SAFETY/lateral.h" inst FWLATERAL --panda-sha256 "$FW_SHA"
expect_refused "Panda patch in a release without firmware" "changes rampilot/panda-patches without a pinned Panda firmware change" inst PATCHNOFW
expect_refused "unpinned Panda patch" "rampilot/panda-patches/0002-extra.patch is not pinned exactly once" inst FWPATCHNOPIN --panda-sha256 "$FW_SHA"
expect_refused "Panda patch that differs from its pin" "$PATCH is not pinned to its blob digest" inst FWPATCHBADSHA --panda-sha256 "$FW_SHA"
expect_refused "Panda patch pinned twice" "$PATCH is not pinned exactly once" inst FWPATCHDUP --panda-sha256 "$FW_SHA"
expect_refused "pin for a Panda patch the release does not contain" "0003-missing.patch, which the candidate does not contain" inst FWPATCHSTALE --panda-sha256 "$FW_SHA"
expect_refused "Panda patch with execute mode" "$PATCH must be a regular file with Git mode 100644" inst FWPATCHMODE --panda-sha256 "$FW_SHA"
# Device check: the Panda's own report must be the F4 signature of the checked-out or the stock F4 file.
expect_refused "firmware install refused until pandad reports the Panda" "PandaSignatures is missing" inst FWOK --panda-sha256 "$FW_SHA"
sigbytes 1a > "$PARAMS_DIR/PandaSignatures"   # an H7-type Panda on its stock H7 firmware
expect_refused "firmware install refused when the Panda is not the F4 type" "not the F4-Panda device" inst FWOK --panda-sha256 "$FW_SHA"
sigbytes 00 > "$PARAMS_DIR/PandaSignatures"   # unrecorded firmware
expect_refused "firmware install refused when the Panda runs unrecorded firmware" "neither the checked-out $FW" inst FWOK --panda-sha256 "$FW_SHA"
printf '\x1e\x1e' > "$PARAMS_DIR/PandaSignatures"   # truncated report
expect_refused "firmware install refused on a truncated Panda report" "not the F4-Panda device" inst FWOK --panda-sha256 "$FW_SHA"
expect_ok "a release without firmware installs without a Panda report check" bash "$DEV" install "$W/good.bundle" "$GOOD"
expect_ok "rollback of that release" bash "$DEV" rollback
sigbytes 1e > "$PARAMS_DIR/PandaSignatures"   # this truck: F4 Panda on stock firmware
[ "$(head)" = "$BASE" ] && [ "$(sha256 "$OP_DIR/$FW")" = "$STOCK_FW_SHA" ] && [ "$(cat "$PARAMS_DIR/DisableUpdates")" = 0 ] && [ ! -e "$STATE_DIR/rollback.env" ] \
  && ok "firmware refusals changed nothing" || bad "firmware refusals changed nothing"
expect_refused "firmware that differs on disk after checkout" "checked-out $FW does not match the approved digest" inst FWCRLF --panda-sha256 "$FW_SHA"
[ "$(head)" = "$BASE" ] && [ "$(git -C "$OP_DIR" rev-parse --abbrev-ref HEAD)" = staging-tici ] && [ "$(sha256 "$OP_DIR/$FW")" = "$STOCK_FW_SHA" ] &&
  [ -z "$(git -C "$OP_DIR" status --porcelain --untracked-files=no)" ] && [ "$(cat "$PARAMS_DIR/DisableUpdates")" = 0 ] && [ ! -e "$STATE_DIR/rollback.env" ] \
  && ok "failed on-disk check restores the baseline checkout and stock file" || bad "failed on-disk check restores the baseline checkout and stock file"
# Firmware whose only source change is the packet hook in safety.h and safety_declarations.h, with no patches
if out="$(inst FWHOOK --panda-sha256 "$FW_SHA" 2>&1)" && [[ "$out" == *"Panda patches pinned: 0"* ]]; then
  ok "firmware with only the packet-hook safety change installs"
else bad "firmware with only the packet-hook safety change installs"; echo "$out" | sed 's/^/     /'; fi
expect_ok "rollback of the packet-hook firmware" bash "$DEV" rollback

if out="$(inst FWOK --panda-sha256 "$FW_SHA" 2>&1)"; then ok "pinned firmware with its safety change installs"; else bad "pinned firmware with its safety change installs"; echo "$out" | sed 's/^/     /'; fi
[[ "$out" == *"pinned Panda firmware ok: $FW sha256 $FW_SHA, built from $SAFETY tree $FW_TREE"* &&
   "$out" == *"signature babababababababa (stock 1e1e1e1e1e1e1e1e)"* && "$out" == *"panda-status (expect MATCH on babababababababa)"* &&
   "$out" == *"Panda type ok: the Panda reports 1e1e1e1e1e1e1e1e"* &&
   "$out" == *"Panda patch ok: $PATCH sha256 $PATCH_SHA"* && "$out" == *"Panda patches pinned: 1"* ]] \
  && ok "install reports the pin, both signatures and the panda-status check" || { bad "install reports the pin, both signatures and the panda-status check"; echo "$out" | sed 's/^/     /'; }
[ "$(head)" = "$FWOK" ] && [ "$(sha256 "$OP_DIR/$FW")" = "$FW_SHA" ] && ok "checked-out firmware has the pinned bytes" || bad "checked-out firmware has the pinned bytes"
st="$STATE_DIR/last/state.env"
grep -qx "panda_sha256=$FW_SHA" "$st" && grep -qx "panda_sig_stock=1e1e1e1e1e1e1e1e" "$st" && grep -qx "panda_sig_candidate=babababababababa" "$st" &&
  grep -qx "panda_safety_tree=$FW_TREE" "$st" && grep -qx "target=$FWOK" "$st" \
  && ok "install state records the firmware hash, both signature prefixes and the safety tree" || { bad "install state records the firmware hash, both signature prefixes and the safety tree"; cat "$st"; }
out="$(inst FWOK --panda-sha256 "$FW_SHA" 2>&1)" && [[ "$out" == *"Panda type ok: the Panda reports 1e1e1e1e1e1e1e1e, the signature of the stock $FW"* &&
  "$out" == *"nothing to do"* ]] && ok "re-install before the reboot accepts the stock signature while the RamPilot file is checked out" || { bad "re-install before the reboot accepts the stock signature while the RamPilot file is checked out"; echo "$out" | sed 's/^/     /'; }
sigbytes ba > "$PARAMS_DIR/PandaSignatures"   # after a boot the Panda runs the checked-out RamPilot firmware
out="$(inst FWOK --panda-sha256 "$FW_SHA" 2>&1)" && [[ "$out" == *"Panda type ok: the Panda reports babababababababa, the signature of the checked-out $FW"* &&
  "$out" == *"nothing to do"* ]] && ok "second firmware install accepts the checked-out file's signature and is a no-op" || { bad "second firmware install accepts the checked-out file's signature and is a no-op"; echo "$out" | sed 's/^/     /'; }
rm -f "$PARAMS_DIR/PandaSignatures"   # panda-status from a fresh start: pandad has not reported yet

# panda-status only reads: stubs record any call to Python (the Panda library) or to process control.
mkdir -p "$W/stubs"
for c in python python3 pkill killall systemctl sudo reboot tmux; do
  printf '#!/bin/sh\necho "%s $*" >> "%s"\n' "$c" "$W/stub-calls" > "$W/stubs/$c"; chmod +x "$W/stubs/$c"
done
snap() { (cd "$W" && find data log -printf '%p %y %s %T@ %m\n' | sort && find data log -type f -print0 | sort -z | xargs -0 sha256sum); }
pstatus() { PATH="$W/stubs:$PATH" bash "$DEV" panda-status 2>&1; }
readonly_check() {  # $1 = test name; runs panda-status into $ps_out
  local before; before="$(snap)"
  if ps_out="$(pstatus)"; then
    [ "$before" = "$(snap)" ] && [ ! -e "$W/stub-calls" ] && ok "$1" || { bad "$1 (files changed or stubs called)"; cat "$W/stub-calls" 2>/dev/null; }
  else bad "$1 (exit status)"; echo "$ps_out" | sed 's/^/     /'; fi
}
printf 1 > "$PARAMS_DIR/IsOnroad"
readonly_check "panda-status runs onroad and changes nothing (no report yet)"
[[ "$ps_out" == *"sha256 $FW_SHA (the pinned firmware of the last install)"* && "$ps_out" == *"signature babababababababa (its last 128 bytes"* &&
   "$ps_out" == *"NOT REPORTED"* && "$ps_out" == *"no pandad Panda lines"* ]] \
  && ok "panda-status before pandad reports: file hash, signature, NOT REPORTED" || { bad "panda-status before pandad reports"; echo "$ps_out" | sed 's/^/     /'; }
sigbytes 1e > "$PARAMS_DIR/PandaSignatures"   # before the reboot the Panda still runs stock
readonly_check "panda-status changes nothing (Panda still on stock)"
[[ "$ps_out" == *"MISMATCH: the Panda reports signature 1e1e1e1e1e1e1e1e, the file has babababababababa; pandad flashes the file at the next boot"* ]] \
  && ok "panda-status shows MISMATCH while the Panda runs stock" || { bad "panda-status shows MISMATCH while the Panda runs stock"; echo "$ps_out" | sed 's/^/     /'; }
# After the reboot: pandad's log (logmessaged's JSON lines) and its report.
swag() {  # $1 = file, $2 = epoch, $3 = level, $4 = message
  printf '{"ctx": {"dongle_id": "0000000000000000"}, "level": "%s", "levelnum": 20, "name": "swaglog", "filename": "pandad.py", "lineno": 37, "pathname": "/data/openpilot/selfdrive/pandad/pandad.py", "module": "pandad", "funcName": "flash_panda", "host": "localhost", "process": 1234, "thread": 1, "threadName": "MainThread", "created": %s, "msg$s": "%s", "id": "00"}\n' "$3" "$2" "$4" >> "$1"
}
T0=1795500000; T1=1795600000
swag "$LOG_DIR/swaglog.0000000002" "$T0.25" WARNING "Panda c0ffee0123 connected, version: DEV-unknown-DEBUG, signature 1e1e1e1e1e1e1e1e, expected 1e1e1e1e1e1e1e1e"
swag "$LOG_DIR/swaglog.0000000003" "$T1.25" WARNING "Panda c0ffee0123 connected, version: DEV-unknown-DEBUG, signature 1e1e1e1e1e1e1e1e, expected babababababababa"
swag "$LOG_DIR/swaglog.0000000003" "$T1.50" INFO "Panda firmware out of date, update required"
swag "$LOG_DIR/swaglog.0000000004" "$((T1 + 31)).00" INFO "Done flashing"
printf '{"filename": "other.py", "created": %s.0, "msg$s": "Done flashing", "id": "01"}\n' "$((T1 + 40))" >> "$LOG_DIR/swaglog.0000000004"
sigbytes ba > "$PARAMS_DIR/PandaSignatures"
readonly_check "panda-status changes nothing (after the flash)"
when() { date -u -d "@$1" '+%Y-%m-%d %H:%M:%S'; }
[[ "$ps_out" == *"MATCH: the Panda runs this file (PandaSignatures babababababababa)"* &&
   "$ps_out" == *"$(when "$T1")  Panda connected, version: DEV-unknown-DEBUG, signature 1e1e1e1e1e1e1e1e, expected babababababababa"* &&
   "$ps_out" == *"$(when "$T1")  Panda firmware out of date, update required"* && "$ps_out" == *"$(when $((T1 + 31)))  Done flashing"* ]] \
  && ok "panda-status shows MATCH and the latest connect, update and flash lines" || { bad "panda-status shows MATCH and the latest connect, update and flash lines"; echo "$ps_out" | sed 's/^/     /'; }
[[ "$ps_out" != *c0ffee* && "$ps_out" != *"expected 1e1e1e1e1e1e1e1e"* && "$(grep -c 'Done flashing' <<< "$ps_out")" = 1 ]] \
  && ok "panda-status leaves out the Panda serial, older connects and other programs' lines" || { bad "panda-status leaves out the Panda serial, older connects and other programs' lines"; echo "$ps_out" | sed 's/^/     /'; }

# verify on a firmware install: the file hash and the Panda's own signature report
( exec -a fake.controlsd sleep 60 ) & P1=$!
( exec -a fake.card sleep 60 ) & P2=$!
sleep 0.3
if out="$(bash "$DEV" verify 2>&1)" && [[ "$out" == *"ok   Panda firmware file $FW sha256 $FW_SHA"* &&
   "$out" == *"ok   the Panda runs the installed firmware: PandaSignatures matches the file, signature babababababababa"* &&
   "$out" == *"Done flashing"* && "$out" == *"verify ok on $FWOK"* ]]; then
  ok "verify confirms the installed firmware hash and the Panda's matching signature"
else bad "verify confirms the installed firmware hash and the Panda's matching signature"; echo "$out" | sed 's/^/     /'; fi
sigbytes 1e > "$PARAMS_DIR/PandaSignatures"
if out="$(bash "$DEV" verify 2>&1)"; then bad "verify fails when the Panda runs other firmware (was not refused)"
elif [[ "$out" == *"FAIL the Panda reports signature 1e1e1e1e1e1e1e1e; the installed firmware has babababababababa"* &&
        "$out" == *"REFUSED: the Panda does not show the installed firmware (FAILED)"* ]]; then ok "verify fails when the Panda runs other firmware"
else bad "verify fails when the Panda runs other firmware"; echo "$out" | sed 's/^/     /'; fi
rm "$PARAMS_DIR/PandaSignatures"
if out="$(bash "$DEV" verify 2>&1)"; then bad "verify fails when pandad has not reported the Panda (was not refused)"
elif [[ "$out" == *"FAIL the Panda's signature is not reported"* && "$out" == *"REFUSED: the Panda does not show the installed firmware (FAILED)"* ]]; then
  ok "verify fails when pandad has not reported the Panda"
else bad "verify fails when pandad has not reported the Panda"; echo "$out" | sed 's/^/     /'; fi
kill $P1 $P2 2>/dev/null; wait 2>/dev/null
printf 0 > "$PARAMS_DIR/IsOnroad"

# rollback: the baseline checkout returns the stock file; pandad reflashes stock at the next boot
sigbytes ba > "$PARAMS_DIR/PandaSignatures"   # the Panda runs the RamPilot firmware
if out="$(bash "$DEV" rollback 2>&1)"; then ok "rollback after the firmware install"; else bad "rollback after the firmware install"; echo "$out" | sed 's/^/     /'; fi
[[ "$out" == *"Panda: the stock firmware file is back (signature 1e1e1e1e1e1e1e1e). At the next boot pandad reflashes the Panda with stock when the Panda runs other firmware (the last install has babababababababa); the Panda reports babababababababa now."* &&
   "$out" == *"Panda check after the reboot: bash device.sh panda-status (expect MATCH on 1e1e1e1e1e1e1e1e)"* ]] \
  && ok "rollback says the Panda reflashes stock at the next boot and how to check it" || { bad "rollback says the Panda reflashes stock at the next boot and how to check it"; echo "$out" | sed 's/^/     /'; }
[ "$(head)" = "$BASE" ] && [ "$(sha256 "$OP_DIR/$FW")" = "$STOCK_FW_SHA" ] && [ -z "$(git -C "$OP_DIR" status --porcelain --untracked-files=no)" ] \
  && ok "rollback restores the stock firmware file" || bad "rollback restores the stock firmware file"
readonly_check "panda-status changes nothing after rollback"
[[ "$ps_out" == *"sha256 $STOCK_FW_SHA (stock, as in baseline ${BASE:0:8})"* &&
   "$ps_out" == *"MISMATCH: the Panda reports signature babababababababa, the file has 1e1e1e1e1e1e1e1e"* ]] \
  && ok "panda-status after rollback, before the reboot: stock file, Panda MISMATCH" || { bad "panda-status after rollback, before the reboot: stock file, Panda MISMATCH"; echo "$ps_out" | sed 's/^/     /'; }
sigbytes 1e > "$PARAMS_DIR/PandaSignatures"   # pandad reflashed stock at boot
ps_out="$(pstatus)"
[[ "$ps_out" == *"MATCH: the Panda runs this file (PandaSignatures 1e1e1e1e1e1e1e1e)"* ]] \
  && ok "panda-status after the reboot: MATCH on the stock signature" || { bad "panda-status after the reboot: MATCH on the stock signature"; echo "$ps_out" | sed 's/^/     /'; }

echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
