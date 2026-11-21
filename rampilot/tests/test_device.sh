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

# Upstream: baseline plus candidate branches
UP="$W/upstream"; mkdir -p "$UP/selfdrive/ui" "$UP/panda"; cd "$UP"
g init -q -b staging-tici
echo "x = 1" > selfdrive/a.py; printf '\x00\x01\x02' > panda/fw.bin; echo base > README
printf '\x7fELF\x00stock screen' > selfdrive/ui/ui; chmod 755 selfdrive/ui/ui
STOCK_UI_SHA="$(sha256 selfdrive/ui/ui)"
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
g checkout -q staging-tici
for n in UIOK UIBAD UINOEXEC UIMODEONLY BINL; do g branch -f rampilot-tici "${!n}"; g bundle create -q "$W/$n.bundle" "$BASE..rampilot-tici" 2>/dev/null; done
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

echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
