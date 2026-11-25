#!/usr/bin/env bash
# RamPilot install and rollback on the comma three. Runs ON the device, as the comma user.
#
#   device.sh preflight
#   device.sh install <bundle> <commit-sha> [--ui-sha256 HASH] [--panda-sha256 HASH] [--reboot]
#   device.sh rollback [--reboot]
#   device.sh status
#   device.sh panda-status  (read-only: firmware file, the Panda's own report, pandad's log lines)
#   device.sh verify        (after the reboot: truck in Park, ignition on)
#
# install  fetches the RamPilot branch from a git bundle copied to the device, checks it is exactly the
#          expected commit and sits on the pinned baseline. The optional screen executable and the optional
#          Panda firmware file each also require an independent SHA-256. After checkout it disables updates
#          and sets aside any staged update.
# rollback returns to the recorded branch and baseline commit and restores DisableUpdates.
# Neither flashes the Panda nor touches /data/continue.sh or the advisor. The checked-out Panda firmware file
# (the pinned one after install, stock after rollback) reaches the Panda through pandad's own signature check
# at the next boot. Both refuse while onroad.
set -euo pipefail

BASELINE_SHA="${BASELINE_SHA:-1f7233cb983f3c13fdbba47a86f8fbc796ad9211}"
TARGET_BRANCH="${TARGET_BRANCH:-rampilot-tici}"
OP_DIR="${OP_DIR:-/data/openpilot}"
PARAMS_DIR="${PARAMS_DIR:-/data/params/d}"
STAGING_ROOT="${STAGING_ROOT:-/data/safe_staging}"
STATE_DIR="${STATE_DIR:-/data/ram-diag/rampilot}"
REBOOT_CMD="${REBOOT_CMD:-sudo reboot}"
MIN_FREE_MB="${MIN_FREE_MB:-500}"
# Paths a RamPilot change may never touch on this device.
DENY_RE='^(panda/|opendbc_repo/opendbc/safety/|selfdrive/modeld/models/|sunnypilot/modeld/models/|third_party/|system/hardware/|tinygrad_repo/)|\.(so|bin|signed|elf|o|a|thneed|onnx|pkl|dlc)$'
MARKER="$STAGING_ROOT/finalized/.overlay_consistent"
# The only compiled file a RamPilot change may replace: the screen program, pinned by SHA-256 in
# rampilot/ALLOWED_BINARIES ("<sha256>  <path>") inside the candidate commit.
UI_PATH=selfdrive/ui/ui
# The only Panda file a RamPilot change is allowed to replace: the F4 Panda firmware, built on the laptop from
# the candidate's own safety code. This truck's comma three has an F4-type internal Panda (its PandaSignatures
# carry the stock panda.bin.signed signature), so pandad flashes panda.bin.signed, not panda_h7.bin.signed
# (panda/python/constants.py app_fn). rampilot/ALLOWED_BINARIES in the candidate pins both:
#   <sha256>  panda/board/obj/panda.bin.signed
#   <git tree hash>  opendbc_repo/opendbc/safety
# Firmware and safety code change together or not at all, and only the SAFETY_ALLOW_RE files change. The H7
# firmware, both bootstubs and every other panda/ path stay refused.
# pandad flashes the file at boot when the Panda's signature differs from the file's last 128 bytes.
PANDA_FW=panda/board/obj/panda.bin.signed
SAFETY_DIR=opendbc_repo/opendbc/safety
SAFETY_ALLOW_RE='^opendbc_repo/opendbc/safety/(modes/chrysler\.h|tests/.+)$'
PANDA_SHA256="" PANDA_SIG_STOCK="" PANDA_SIG_CANDIDATE="" PANDA_SAFETY_TREE=""
# pandad.py lines about the Panda in /data/log/swaglog.* (logmessaged writes INFO and above).
PANDAD_LOG_RE='Panda [^ ]+ connected, version: |Panda firmware out of date|Done flashing|Flashed firmware not booting|Panda still not booting|Version mismatch after flashing|Error computing expected signature|Panda in DFU mode found|protocol mismatch'
VERIFY_PROCS="${VERIFY_PROCS:-selfdrive.controls.controlsd selfdrive.car.card selfdrive.selfdrived.selfdrived selfdrive.locationd.paramsd selfdrive.ui.soundd ^\./ui$}"
VERIFY_WAIT_S="${VERIFY_WAIT_S:-30}"
LOG_DIR="${LOG_DIR:-/data/log}"

die() { echo "REFUSED: $*" >&2; exit 2; }
say() { echo "$*"; }
param_get() { [ -f "$PARAMS_DIR/$1" ] && tr -d '\0\n' < "$PARAMS_DIR/$1" || echo ""; }
param_put() {  # atomic, the same on-disk form Params uses
  local tmp; tmp="$(mktemp "$PARAMS_DIR/.tmp_$1.XXXXXX")"
  if ! printf '%s' "$2" > "$tmp" || ! sync "$tmp" || ! mv -Tf "$tmp" "$PARAMS_DIR/$1"; then
    rm -f "$tmp"; return 1
  fi
}
git_op() { git -C "$OP_DIR" "$@"; }
head_sha() { git_op rev-parse HEAD; }
head_branch() { git_op rev-parse --abbrev-ref HEAD; }
hex_of() { od -An -v -tx1 | tr -d ' \n'; }  # stdin as lowercase hex; every stage reads to the end
le32() { echo $((16#${1:6:2}${1:4:2}${1:2:2}${1:0:2})); }  # 8 hex digits, little-endian
# Signatures as 256 hex digits (128 bytes). The file's is its last 128 bytes, as pandad reads it. The Panda's is
# PandaSignatures, written by pandad after it has checked or flashed every Panda (internal Panda first) and
# cleared at each openpilot start and each pandad retry.
panda_file_sig() { [ -f "$OP_DIR/$PANDA_FW" ] && hex_of < "$OP_DIR/$PANDA_FW" | tail -c 256 || true; }
panda_running_sig() { [ -f "$PARAMS_DIR/PandaSignatures" ] && hex_of < "$PARAMS_DIR/PandaSignatures" | cut -c1-256 || true; }
pin_lookup() {  # $1 = commit, $2 = path: every hash pinned for that path in the commit's rampilot/ALLOWED_BINARIES
  { git_op cat-file blob "$1:rampilot/ALLOWED_BINARIES" 2>/dev/null || true; } |
    awk -v p="$2" '$1 !~ /^#/ && NF == 2 && $2 == p {print $1}'
}

# Older installers kept one before/after pair per install. Follow the recorded
# chain back to this installation's baseline, including an interrupted checkout.
legacy_rollback_record() {
  local last="$STATE_DIR/last/state.env" current expected file i
  local records=("$STATE_DIR"/state-*/state.env)
  [ -f "$last" ] || die "no recorded install state at $last" >&2
  current="$(head_sha)"
  if ! grep -Fxq "target=$current" "$last" && ! grep -Fxq "head=$current" "$last"; then
    die "the recorded install does not match the current checkout" >&2
  fi
  for ((i=${#records[@]}-1; i>=0; --i)); do
    [[ "${records[i]}" -ef "$last" ]] && break
  done
  for ((; i>=0; --i)); do
    file="${records[i]}"
    if [ -n "${expected:-}" ] && ! grep -Fxq "target=$expected" "$file"; then break; fi
    expected="$(sed -n 's/^head=//p' "$file")"
    if [ "$expected" = "$BASELINE_SHA" ]; then
      grep -q '^branch=.' "$file" && grep -q '^disable_updates=' "$file" || break
      printf '%s\n' "$file"; return
    fi
    [[ "$expected" =~ ^[0-9a-f]{40}$ ]] || break
  done
  die "no continuous install history back to the recorded baseline; restore by hand" >&2
}

# This file belongs to the entire install/upgrade series. A successful rollback
# retires it so a later first install captures the then-current branch and params.
ensure_rollback_record() {
  local active="$STATE_DIR/rollback.env" tmp legacy
  if [ ! -f "$active" ]; then
    mkdir -p "$STATE_DIR"
    if [ "$(head_sha)" = "$BASELINE_SHA" ]; then
      tmp="$(mktemp "$STATE_DIR/.rollback.XXXXXX")"
      { echo "branch=$(head_branch)"; echo "head=$BASELINE_SHA"; echo "disable_updates=$(param_get DisableUpdates)";
        echo "staged_update=$([ -f "$MARKER" ] && echo yes || echo no)"; } > "$tmp"
    else
      legacy="$(legacy_rollback_record)" || return 2
      tmp="$(mktemp "$STATE_DIR/.rollback.XXXXXX")"
      grep -E '^(branch|head|disable_updates|staged_update)=' "$legacy" > "$tmp"
    fi
    mv -T "$tmp" "$active"
  fi
  grep -Fxq "head=$BASELINE_SHA" "$active" || die "recorded rollback head does not match the pinned baseline"
}

record_installed_target() {  # $1 = target commit, then key=value lines for this install only
  local state target="$1"; shift
  state="$(mktemp -d "$STATE_DIR/state-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
  if ! cat "$STATE_DIR/rollback.env" > "$state/state.env" ||
     ! printf 'target=%s\n' "$target" >> "$state/state.env" ||
     ! { (($# == 0)) || printf '%s\n' "$@" >> "$state/state.env"; } ||
     ! ln -s "$(basename "$state")" "$state/next-link" ||
     ! mv -Tf "$state/next-link" "$STATE_DIR/last"; then
    rm -f "$state/next-link" "$state/state.env"
    rmdir "$state"
    return 1
  fi
  say "recorded state in $state"
}

require_offroad() {
  local flag="$PARAMS_DIR/IsOnroad"
  [ -f "$flag" ] && [ -r "$flag" ] || die "IsOnroad is missing or unreadable"
  cmp -s "$flag" <(printf 0) && return 0
  if cmp -s "$flag" <(printf 1); then die "the truck is onroad; park, turn it off and wait for offroad"; fi
  die "IsOnroad is unreadable or malformed; require exactly 0"
}

preflight() {
  [ -d "$OP_DIR/.git" ] || die "$OP_DIR is not a git checkout"
  [ -d "$PARAMS_DIR" ] || die "params directory $PARAMS_DIR missing"
  require_offroad
  local dirty; dirty="$(git_op status --porcelain --untracked-files=no)"
  [ -z "$dirty" ] || die "$OP_DIR has uncommitted changes: $(echo "$dirty" | head -5 | tr '\n' ' ')"
  local free_mb; free_mb="$(df -Pm "$OP_DIR" | awk 'NR==2 {print $4}')"
  [ "${free_mb:-0}" -ge "$MIN_FREE_MB" ] || die "only ${free_mb} MB free under $OP_DIR (need $MIN_FREE_MB)"
  say "preflight ok: branch=$(head_branch) head=$(head_sha) DisableUpdates=$(param_get DisableUpdates) free_mb=$free_mb staged_update=$([ -f "$MARKER" ] && echo yes || echo no)"
}

check_candidate() {  # $1 = sha already available, $2 = approved UI digest, $3 = approved Panda firmware digest
  local sha="$1" approved="$2" panda_approved="${3:-}" changed bad binary expected actual ui_entry
  git_op merge-base --is-ancestor "$BASELINE_SHA" "$sha" || die "$sha does not contain baseline $BASELINE_SHA"
  changed="$(git_op diff --no-renames --name-only "$BASELINE_SHA" "$sha")"
  # The firmware file and the allowed safety files pass here only to meet the lockstep checks below.
  bad="$(printf '%s\n' "$changed" | grep -vxF "$PANDA_FW" | grep -vE "$SAFETY_ALLOW_RE" | grep -E "$DENY_RE" || true)"
  [ -z "$bad" ] || die "candidate touches protected paths: $(echo "$bad" | tr '\n' ' ')"
  binary="$(git_op diff --no-renames --numstat "$BASELINE_SHA" "$sha" -- . ":(exclude)$UI_PATH" ":(exclude)$PANDA_FW" | awk -F '\t' '$1 == "-" || $2 == "-" {print $3}')"
  [ -z "$binary" ] || die "candidate changes binary files: $binary"
  ui_entry="$(git_op ls-tree "$sha" -- "$UI_PATH")"
  if [ "$ui_entry" != "$(git_op ls-tree "$BASELINE_SHA" -- "$UI_PATH")" ]; then
    [[ "$ui_entry" == '100755 blob '* ]] || die "$UI_PATH must have Git mode 100755"
    [ -n "$approved" ] || die "changed $UI_PATH requires --ui-sha256 from the reviewed artifact"
    expected="$(git_op cat-file blob "$sha:rampilot/ALLOWED_BINARIES" 2>/dev/null | awk -v p="$UI_PATH" '$1 !~ /^#/ && NF == 2 && $2 == p {print $1}')" ||
      die "$UI_PATH is not pinned in rampilot/ALLOWED_BINARIES"
    actual="$(git_op cat-file blob "$sha:$UI_PATH" | sha256sum | cut -c1-64)"
    [ "$actual" = "$expected" ] || die "$UI_PATH is not pinned to its blob digest in rampilot/ALLOWED_BINARIES"
    [ "$actual" = "$approved" ] || die "independent --ui-sha256 does not match $UI_PATH"
    say "pinned binary ok: $UI_PATH sha256 $actual"
  elif [ -n "$approved" ]; then
    die "--ui-sha256 supplied but the candidate does not change $UI_PATH"
  fi
  check_candidate_panda "$sha" "$panda_approved" "$changed"
  say "changed files from baseline:"
  git_op diff --stat "$BASELINE_SHA" "$sha" | sed 's/^/  /'
}

check_fw_layout() {  # $1 = the firmware as hex: the signed layout of panda crypto/sign.py (SETLEN=1)
  local h="$1" n len
  n=$((${#h} / 2))
  ((n >= 136)) || die "$PANDA_FW is too short to be signed Panda firmware ($n bytes)"
  len="$(le32 "${h:0:8}")"
  ((len == n - 128)) || die "$PANDA_FW length field $len is not its size less the 128-byte signature ($((n - 128)))"
  [ "${h: -272:8}" = 56455253 ] || die "$PANDA_FW has no VERS marker before its signature"
  (($(le32 "${h: -264:8}") >= 2)) || die "$PANDA_FW signing version is below 2"
}

# Sets PANDA_SHA256, PANDA_SIG_STOCK, PANDA_SIG_CANDIDATE and PANDA_SAFETY_TREE when the candidate carries
# pinned firmware. The RSA signature itself is checked on the laptop (debug key VALID); the device tree has
# no panda/certs, and the Panda's bootstub checks it again before it runs the file.
check_candidate_panda() {  # $1 = sha, $2 = approved firmware digest, $3 = changed paths
  local sha="$1" approved="$2" changed="$3" entry safety safety_src expected actual tree pinned_tree hex stock
  entry="$(git_op ls-tree "$sha" -- "$PANDA_FW")"
  safety="$(printf '%s\n' "$changed" | grep "^$SAFETY_DIR/" || true)"
  safety_src="$(printf '%s\n' "$safety" | grep -v "^$SAFETY_DIR/tests/" || true)"
  if [ "$entry" = "$(git_op ls-tree "$BASELINE_SHA" -- "$PANDA_FW")" ]; then
    [ -z "$approved" ] || die "--panda-sha256 supplied but the candidate does not change $PANDA_FW"
    [ -z "$safety" ] || die "candidate changes $SAFETY_DIR without a pinned Panda firmware change: $(echo "$safety" | tr '\n' ' ')"
    return 0
  fi
  [[ "$entry" == '100644 blob '* ]] || die "$PANDA_FW must be a regular file with Git mode 100644"
  [ -n "$approved" ] || die "changed $PANDA_FW requires --panda-sha256 from the reviewed firmware build"
  [ -n "$safety_src" ] || die "candidate changes $PANDA_FW without a safety source change in $SAFETY_DIR"
  expected="$(pin_lookup "$sha" "$PANDA_FW")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "$PANDA_FW is not pinned exactly once in rampilot/ALLOWED_BINARIES"
  actual="$(git_op cat-file blob "$sha:$PANDA_FW" | sha256sum | cut -c1-64)"
  [ "$actual" = "$expected" ] || die "$PANDA_FW is not pinned to its blob digest in rampilot/ALLOWED_BINARIES"
  [ "$actual" = "$approved" ] || die "independent --panda-sha256 does not match $PANDA_FW"
  tree="$(git_op rev-parse "$sha:$SAFETY_DIR")"
  pinned_tree="$(pin_lookup "$sha" "$SAFETY_DIR")"
  [[ "$pinned_tree" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || die "$SAFETY_DIR is not pinned exactly once in rampilot/ALLOWED_BINARIES"
  [ "$pinned_tree" = "$tree" ] || die "safety tree pin $pinned_tree does not match the candidate's $SAFETY_DIR tree $tree"
  hex="$(git_op cat-file blob "$sha:$PANDA_FW" | hex_of)"
  check_fw_layout "$hex"
  git_op cat-file -e "$BASELINE_SHA:$PANDA_FW" 2>/dev/null || die "baseline $BASELINE_SHA has no $PANDA_FW"
  stock="$(git_op cat-file blob "$BASELINE_SHA:$PANDA_FW" | hex_of)"
  [ "${hex: -256}" != "${stock: -256}" ] || die "$PANDA_FW carries the stock signature; pandad would never flash it"
  PANDA_SHA256="$actual" PANDA_SAFETY_TREE="$tree"
  PANDA_SIG_STOCK="${stock: -256:16}" PANDA_SIG_CANDIDATE="${hex: -256:16}"
  say "pinned Panda firmware ok: $PANDA_FW sha256 $actual, built from $SAFETY_DIR tree $tree"
  say "  signature $PANDA_SIG_CANDIDATE (stock $PANDA_SIG_STOCK)"
}

check_installed_ui() {
  [ -n "$1" ] || return 0
  local path="$OP_DIR/$UI_PATH"
  [[ -f "$path" && ! -L "$path" && -x "$path" ]] || die "checked-out $UI_PATH is not a regular executable"
  [ "$(sha256sum "$path" | cut -c1-64)" = "$1" ] || die "checked-out $UI_PATH does not match the approved digest"
}

check_installed_panda() {
  [ -n "$1" ] || return 0
  local path="$OP_DIR/$PANDA_FW"
  [[ -f "$path" && ! -L "$path" ]] || die "checked-out $PANDA_FW is not a regular file"
  [ "$(sha256sum "$path" | cut -c1-64)" = "$1" ] || die "checked-out $PANDA_FW does not match the approved digest"
}

# Firmware installs only: the pinned file is built and tested for this truck's F4-type internal Panda. Evidence
# that needs no Panda access: the Panda's own report (PandaSignatures) equals the signature of the F4 file in
# the checkout now, or of the stock F4 file. A Panda that reports neither is not the device this was tested for.
check_device_panda() {
  local run_sig installed stock
  run_sig="$(panda_running_sig)"
  [ -n "$run_sig" ] || die "PandaSignatures is missing: pandad has not reported the Panda since start-up; wait a minute after start-up and rerun"
  installed="$(panda_file_sig)"
  stock="$(git_op cat-file blob "$BASELINE_SHA:$PANDA_FW" | hex_of | tail -c 256)"
  if [ "$run_sig" = "$installed" ]; then say "Panda type ok: the Panda reports ${run_sig:0:16}, the signature of the checked-out $PANDA_FW"
  elif [ "$run_sig" = "$stock" ]; then say "Panda type ok: the Panda reports ${run_sig:0:16}, the signature of the stock $PANDA_FW"
  else
    die "the Panda reports signature ${run_sig:0:16}, neither the checked-out $PANDA_FW (${installed:0:16}) nor stock (${stock:0:16}); this comma is not the F4-Panda device this firmware was tested for"
  fi
}

install() (
  local bundle="${1:-}" sha="${2:-}" approved="" panda_approved="" reboot=""
  [ -n "$bundle" ] && [ -n "$sha" ] || die "usage: install <bundle> <commit-sha> [--ui-sha256 HASH] [--panda-sha256 HASH] [--reboot]"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "commit sha must be the full 40-character hash"
  shift 2
  while (($#)); do
    case "$1" in
      --reboot) [ -z "$reboot" ] || die "duplicate --reboot"; reboot=--reboot ;;
      --ui-sha256)
        [ -z "$approved" ] || die "duplicate --ui-sha256"
        [[ "${2:-}" =~ ^[0-9a-fA-F]{64}$ ]] || die "--ui-sha256 needs a 64-character hex digest"
        approved="${2,,}"; shift ;;
      --panda-sha256)
        [ -z "$panda_approved" ] || die "duplicate --panda-sha256"
        [[ "${2:-}" =~ ^[0-9a-fA-F]{64}$ ]] || die "--panda-sha256 needs a 64-character hex digest"
        panda_approved="${2,,}"; shift ;;
      *) die "unknown install option: $1" ;;
    esac
    shift
  done
  preflight
  local original_head original_branch original_target bundle_head
  original_head="$(head_sha)"; original_branch="$(head_branch)"
  if [ "$original_head" != "$sha" ]; then
    [ -f "$bundle" ] || die "bundle $bundle not found"
    bundle="$(readlink -f "$bundle")"
    git_op bundle verify "$bundle" >/dev/null 2>&1 || die "bundle failed verification (missing baseline objects or damaged file)"
    bundle_head="$(git bundle list-heads "$bundle" "refs/heads/$TARGET_BRANCH" | awk '{print $1}')"
    [ -n "$bundle_head" ] || die "bundle has no $TARGET_BRANCH branch"
    [ "$bundle_head" = "$sha" ] || die "bundle branch is $bundle_head, expected $sha"
    git_op fetch -q --no-write-fetch-head "$bundle" "refs/heads/$TARGET_BRANCH" || die "could not fetch $TARGET_BRANCH from the bundle"
  fi
  check_candidate "$sha" "$approved" "$panda_approved"
  [ -z "$panda_approved" ] || check_device_panda
  if [ "$original_head" = "$sha" ]; then
    check_installed_ui "$approved"
    check_installed_panda "$panda_approved"
    if [ ! -f "$STATE_DIR/rollback.env" ] || { grep -Fxq "target=$sha" "$STATE_DIR/last/state.env" 2>/dev/null &&
         cmp -s "$PARAMS_DIR/DisableUpdates" <(printf 1) && [ ! -e "$MARKER" ]; }; then
      say "already on $sha; nothing to do"; return 0
    fi
  fi
  original_target="$(git_op rev-parse --verify -q "refs/heads/$TARGET_BRANCH" || true)"
  local new_record=0 changed_updates=0 moved_marker="" prior_updates="" had_updates=0
  [ -f "$STATE_DIR/rollback.env" ] || new_record=1
  if [ -e "$PARAMS_DIR/DisableUpdates" ]; then
    [ -f "$PARAMS_DIR/DisableUpdates" ] && [ -r "$PARAMS_DIR/DisableUpdates" ] || die "cannot read DisableUpdates"
    prior_updates="$(param_get DisableUpdates)"; had_updates=1
  fi

  # Restore the configuration of this particular attempt on an ordinary error.
  # The original baseline record stays available if that recovery itself fails.
  undo_failed_install() {
    local failed=0
    if [ -n "$moved_marker" ]; then mv -T "$moved_marker" "$MARKER" || failed=1; fi
    if ((changed_updates)); then
      if ((had_updates)); then param_put DisableUpdates "$prior_updates" || failed=1
      else rm -f "$PARAMS_DIR/DisableUpdates" || failed=1; fi
    fi
    if [ "$(head_sha)" != "$original_head" ] || [ "$(head_branch)" != "$original_branch" ]; then
      if [ "$original_branch" = HEAD ]; then git_op checkout -q --detach "$original_head" || failed=1
      else git_op checkout -q -B "$original_branch" "$original_head" || failed=1; fi
      if [ "$original_branch" != "$TARGET_BRANCH" ] && ((!failed)); then
        if [ -n "$original_target" ]; then git_op update-ref "refs/heads/$TARGET_BRANCH" "$original_target" "$sha" || failed=1
        else git_op update-ref -d "refs/heads/$TARGET_BRANCH" "$sha" || failed=1; fi
      fi
    fi
    if ((failed)); then say "install recovery incomplete; original rollback record retained" >&2
    elif ((new_record)); then rm -f "$STATE_DIR/rollback.env"; fi
  }
  require_offroad
  trap 'if (( $? != 0 )); then undo_failed_install; fi' EXIT
  ensure_rollback_record
  if [ "$original_head" != "$sha" ]; then
    git_op checkout -q -B "$TARGET_BRANCH" "$sha" || die "checkout failed for $sha"
  fi
  [ "$(head_sha)" = "$sha" ] || die "checkout did not land on $sha"
  check_installed_ui "$approved"
  check_installed_panda "$panda_approved"
  if ! cmp -s "$PARAMS_DIR/DisableUpdates" <(printf 1); then param_put DisableUpdates 1; changed_updates=1; fi
  if [ -f "$MARKER" ]; then
    local aside="$MARKER.rampilot-set-aside"
    if [ -e "$aside" ]; then aside="$(mktemp "$aside.XXXXXX")"; fi
    mv -T "$MARKER" "$aside"; moved_marker="$aside"
    say "set aside staged update"
  fi
  local panda_fields=()
  if [ -n "$panda_approved" ]; then
    panda_fields=("panda_sha256=$PANDA_SHA256" "panda_sig_stock=$PANDA_SIG_STOCK"
                  "panda_sig_candidate=$PANDA_SIG_CANDIDATE" "panda_safety_tree=$PANDA_SAFETY_TREE")
  fi
  record_installed_target "$sha" ${panda_fields[@]+"${panda_fields[@]}"}
  trap - EXIT
  say "installed $TARGET_BRANCH at $sha; DisableUpdates=1. Reboot to start it."
  if [ -n "$panda_approved" ]; then
    say "Panda: at the next boot pandad compares the Panda with $PANDA_FW and flashes it when they differ (new signature $PANDA_SIG_CANDIDATE, stock $PANDA_SIG_STOCK)."
    say "Panda check after the reboot: bash device.sh panda-status (expect MATCH on $PANDA_SIG_CANDIDATE)"
  fi
  [ "$reboot" = "--reboot" ] && $REBOOT_CMD
  return 0
)

rollback() {
  local reboot="${1:-}" state="$STATE_DIR/rollback.env"
  [[ "$#" == 0 || ( "$#" == 1 && "$reboot" == --reboot ) ]] || die "usage: rollback [--reboot]"
  preflight
  if [ ! -f "$state" ]; then
    if [ -f "$STATE_DIR/last/rolled-back" ]; then
      [ "$(head_sha)" = "$BASELINE_SHA" ] || die "no active install state for this checkout"
      say "already rolled back to $BASELINE_SHA"; return 0
    fi
    state="$(legacy_rollback_record)" || return 2
  fi
  # shellcheck disable=SC1090
  local branch head disable_updates; branch="$(grep '^branch=' "$state" | cut -d= -f2-)"
  head="$(grep '^head=' "$state" | cut -d= -f2-)"; disable_updates="$(grep '^disable_updates=' "$state" | cut -d= -f2-)"
  [ "$head" = "$BASELINE_SHA" ] || die "recorded pre-install head $head is not the baseline $BASELINE_SHA; restore by hand"
  git_op cat-file -e "$head^{commit}" || die "baseline commit $head is missing from the checkout"
  require_offroad
  if [ "$branch" = "HEAD" ] || [ -z "$branch" ]; then git_op checkout -q --detach "$head"; else git_op checkout -q -B "$branch" "$head"; fi
  [ "$(head_sha)" = "$head" ] || die "rollback did not land on $head"
  if [ -n "$disable_updates" ]; then param_put DisableUpdates "$disable_updates"; else rm -f "$PARAMS_DIR/DisableUpdates"; fi
  if [ -f "$STATE_DIR/last/state.env" ]; then touch "$STATE_DIR/last/rolled-back"; fi
  rm -f "$STATE_DIR/rollback.env"
  say "rolled back to ${branch} at $head; DisableUpdates=${disable_updates:-unset}. Reboot to start it."
  say "a set-aside staged update stays set aside; the updater fetches a fresh one if updates are on"
  panda_rollback_note
  [ "$reboot" = "--reboot" ] && $REBOOT_CMD
  return 0
}

panda_rollback_note() {  # the rollback itself is the baseline checkout, which restores the stock firmware file
  local file_sig run_sig cand
  file_sig="$(panda_file_sig)"
  [ -n "$file_sig" ] || { say "Panda: the checkout has no $PANDA_FW"; return 0; }
  cand="$(sed -n 's/^panda_sig_candidate=//p' "$STATE_DIR/last/state.env" 2>/dev/null || true)"
  run_sig="$(panda_running_sig)"
  [ -z "$cand" ] || cand=" (the last install has $cand)"
  [ -z "$run_sig" ] || run_sig="; the Panda reports ${run_sig:0:16} now"
  say "Panda: the stock firmware file is back (signature ${file_sig:0:16}). At the next boot pandad reflashes the Panda with stock when the Panda runs other firmware$cand$run_sig."
  say "Panda check after the reboot: bash device.sh panda-status (expect MATCH on ${file_sig:0:16})"
}

# pandad's latest "Panda <serial> connected, version: V, signature S, expected E" line (16-hex prefixes) and
# everything it logged about the Panda after it, oldest first, with the Panda serial left out.
pandad_log_report() {
  local files=("$LOG_DIR"/swaglog.*) lines=() start=-1 i t
  if [ ! -e "${files[0]}" ]; then say "  no pandad log in $LOG_DIR"; return 0; fi
  mapfile -t lines < <(LC_ALL=C grep -h -F '"filename": "pandad.py"' -- "${files[@]}" 2>/dev/null |
    LC_ALL=C grep -E "$PANDAD_LOG_RE" | tail -n 50 |
    sed -E -n 's/.*"created": ([0-9]+).*"msg\$s": "([^"]*)".*/\1 \2/p' |
    sed -E 's/^([0-9]+) Panda [^ ]+ connected, /\1 Panda connected, /; s/(flashing recovery) [^ ]+$/\1/')
  if ((${#lines[@]} == 0)); then say "  no pandad Panda lines in $LOG_DIR/swaglog.*"; return 0; fi
  for i in "${!lines[@]}"; do [[ "${lines[i]}" == *" Panda connected, version: "* ]] && start=$i; done
  if ((start < 0)); then start=$((${#lines[@]} > 12 ? ${#lines[@]} - 12 : 0)); say "  (no connect line among the last 50 Panda lines)"; fi
  for ((i = start; i < ${#lines[@]}; i++)); do
    t="${lines[i]%% *}"
    say "  $(date -u -d "@$t" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "$t")  ${lines[i]#* }"
  done
}

# Read-only: two files and pandad's log. It does not talk to the Panda, stop a process or change anything.
panda_status() {
  local path="$OP_DIR/$PANDA_FW" disk stock pin="" kind file_sig="" run_sig btime last="$STATE_DIR/last/state.env"
  say "Panda firmware file $PANDA_FW (checkout $(head_sha))"
  if [ -f "$path" ]; then
    disk="$(sha256sum "$path" | cut -c1-64)"
    stock="$({ git_op cat-file blob "$BASELINE_SHA:$PANDA_FW" 2>/dev/null || true; } | sha256sum | cut -c1-64)"
    if [ -f "$last" ] && [ ! -e "$STATE_DIR/last/rolled-back" ]; then pin="$(sed -n 's/^panda_sha256=//p' "$last")"; fi
    if [ "$disk" = "$stock" ]; then kind="stock, as in baseline ${BASELINE_SHA:0:8}"
    elif [ -n "$pin" ] && [ "$disk" = "$pin" ]; then kind="the pinned firmware of the last install"
    else kind="neither stock nor a recorded install pin"; fi
    file_sig="$(panda_file_sig)"
    say "  sha256 $disk ($kind)"
    say "  signature ${file_sig:0:16} (its last 128 bytes; pandad flashes the Panda when the Panda's signature differs)"
  else
    say "  missing"
  fi
  run_sig="$(panda_running_sig)"
  if [ -z "$run_sig" ]; then
    say "NOT REPORTED: PandaSignatures is missing. pandad writes it once it has checked or flashed the Panda and clears it at each start; wait a minute and rerun."
  elif [ -n "$file_sig" ] && [ "$run_sig" = "$file_sig" ]; then
    say "MATCH: the Panda runs this file (PandaSignatures ${run_sig:0:16})"
  else
    say "MISMATCH: the Panda reports signature ${run_sig:0:16}, the file has ${file_sig:0:16}; pandad flashes the file at the next boot"
  fi
  btime="$(awk '/^btime / {print $2}' /proc/stat 2>/dev/null || true)"
  [ -z "$btime" ] || say "this boot started $(date -u -d "@$btime" '+%Y-%m-%d %H:%M:%S') UTC by the device clock"
  say "pandad log, latest Panda connect and what followed (UTC):"
  pandad_log_report
}

verify_panda() {  # $1 = install state of a firmware install; prints ok/FAIL lines, returns 1 on any FAIL
  local want sig_want disk file_sig run_sig rc=0
  want="$(sed -n 's/^panda_sha256=//p' "$1")"; sig_want="$(sed -n 's/^panda_sig_candidate=//p' "$1")"
  disk="$({ [ -f "$OP_DIR/$PANDA_FW" ] && sha256sum "$OP_DIR/$PANDA_FW" | cut -c1-64; } || true)"
  if [ "$disk" = "$want" ]; then say "ok   Panda firmware file $PANDA_FW sha256 $disk"
  else say "FAIL Panda firmware file sha256 is ${disk:-missing}; the install recorded $want"; rc=1; fi
  file_sig="$(panda_file_sig)"; run_sig="$(panda_running_sig)"
  if [ -z "$run_sig" ]; then
    say "FAIL the Panda's signature is not reported (PandaSignatures missing): pandad has not finished checking the Panda"; rc=1
  elif [ "$run_sig" = "$file_sig" ] && [ "${file_sig:0:16}" = "$sig_want" ]; then
    say "ok   the Panda runs the installed firmware: PandaSignatures matches the file, signature $sig_want"
  else
    say "FAIL the Panda reports signature ${run_sig:0:16}; the installed firmware has $sig_want"; rc=1
  fi
  say "pandad log, latest Panda connect and what followed (UTC):"
  pandad_log_report
  return "$rc"
}

verify() {  # onroad processes start on the installed commit and stay up; the Panda runs a pinned firmware
  local state="$STATE_DIR/last/state.env" target p pid1 pid2 failed=0 panda_failed=0
  [ -f "$state" ] || die "no recorded install state"
  target="$(grep '^target=' "$state" | cut -d= -f2-)"
  [ "$(head_sha)" = "$target" ] || die "running checkout is $(head_sha), installed target was $target"
  [ "$(param_get IsOnroad)" = "1" ] || die "turn the ignition on (stay in Park) so the driving processes start, then rerun"
  declare -A first
  for p in $VERIFY_PROCS; do first[$p]="$( (pgrep -f "$p" || true) | sort -n | head -1)"; done
  sleep "$VERIFY_WAIT_S"
  for p in $VERIFY_PROCS; do
    pid1="${first[$p]}"; pid2="$( (pgrep -f "$p" || true) | sort -n | head -1)"
    if [ -z "$pid1" ] || [ -z "$pid2" ]; then say "FAIL $p not running"; failed=1
    elif [ "$pid1" != "$pid2" ]; then say "FAIL $p restarted ($pid1 -> $pid2)"; failed=1
    else say "ok   $p running (pid $pid2) for ${VERIFY_WAIT_S}s"; fi
  done
  if [ -d "$LOG_DIR" ]; then
    local bsm; bsm="$(grep -rh "rampilot late BSM check" "$LOG_DIR" 2>/dev/null | tail -1 || true)"
    say "blind-spot startup check: ${bsm:-no log line found}"
  fi
  if grep -q '^panda_sha256=' "$state"; then verify_panda "$state" || panda_failed=1
  else say "Panda firmware file: not changed by this install (stock); bash device.sh panda-status shows the Panda's report"; fi
  if ((failed && panda_failed)); then
    die "driving processes are not stable and the Panda does not show the installed firmware (FAILED); consider rollback"
  fi
  [ "$failed" -eq 0 ] || die "one or more driving processes are not stable; consider rollback"
  [ "$panda_failed" -eq 0 ] || die "the Panda does not show the installed firmware (FAILED); run panda-status, consider rollback"
  say "verify ok on $target"
}

status() {
  say "branch=$(head_branch) head=$(head_sha)"
  say "DisableUpdates=$(param_get DisableUpdates) IsOnroad=$(param_get IsOnroad)"
  say "staged_update=$([ -f "$MARKER" ] && echo yes || echo no) set_aside=$([ -f "$MARKER.rampilot-set-aside" ] && echo yes || echo no)"
  [ -f "$STATE_DIR/last/state.env" ] && { say "last install state:"; sed 's/^/  /' "$STATE_DIR/last/state.env"; } || say "no install state recorded"
}

cmd="${1:-}"; shift || true
if [[ "$cmd" == preflight || "$cmd" == status || "$cmd" == verify || "$cmd" == panda-status ]]; then
  (($# == 0)) || die "$cmd takes no arguments"
fi
if [[ "$cmd" == install || "$cmd" == rollback ]]; then
  [ -d "$OP_DIR/.git" ] || die "$OP_DIR is not a git checkout"
  exec 9<"$OP_DIR/.git"
  flock -x 9
fi
case "$cmd" in
  preflight) preflight ;;
  install) install "$@" ;;
  rollback) rollback "$@" ;;
  status) status ;;
  panda-status) panda_status ;;
  verify) verify ;;
  *) echo "usage: $0 {preflight|install <bundle> <sha> [--ui-sha256 HASH] [--panda-sha256 HASH] [--reboot]|rollback [--reboot]|status|panda-status|verify}" >&2; exit 64 ;;
esac
