#!/usr/bin/env bash
# RamPilot install and rollback on the comma three. Runs ON the device, as the comma user.
#
#   device.sh preflight
#   device.sh install <bundle> <commit-sha> [--ui-sha256 HASH] [--reboot]
#   device.sh rollback [--reboot]
#   device.sh status
#   device.sh verify        (after the reboot: truck in Park, ignition on)
#
# install  fetches the RamPilot branch from a git bundle copied to the device, checks it is exactly the
#          expected commit and sits on the pinned baseline. The optional screen executable also requires
#          an independent SHA-256. After checkout it disables updates and sets aside any staged update.
# rollback returns to the recorded branch and baseline commit and restores DisableUpdates.
# Neither touches Panda firmware, /data/continue.sh or the advisor. Both refuse while onroad.
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

record_installed_target() {
  local state
  state="$(mktemp -d "$STATE_DIR/state-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
  if ! cat "$STATE_DIR/rollback.env" > "$state/state.env" ||
     ! printf 'target=%s\n' "$1" >> "$state/state.env" ||
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

check_candidate() {  # $1 = sha already available, $2 = externally approved UI digest, if any
  local sha="$1" approved="$2" bad binary expected actual ui_entry
  git_op merge-base --is-ancestor "$BASELINE_SHA" "$sha" || die "$sha does not contain baseline $BASELINE_SHA"
  bad="$(git_op diff --name-only "$BASELINE_SHA" "$sha" | grep -E "$DENY_RE" || true)"
  [ -z "$bad" ] || die "candidate touches protected paths: $(echo "$bad" | tr '\n' ' ')"
  binary="$(git_op diff --no-renames --numstat "$BASELINE_SHA" "$sha" -- . ":(exclude)$UI_PATH" | awk -F '\t' '$1 == "-" || $2 == "-" {print $3}')"
  [ -z "$binary" ] || die "candidate changes binary files: $binary"
  ui_entry="$(git_op ls-tree "$sha" -- "$UI_PATH")"
  if [ "$ui_entry" != "$(git_op ls-tree "$BASELINE_SHA" -- "$UI_PATH")" ]; then
    [[ "$ui_entry" == '100755 blob '* ]] || die "$UI_PATH must have Git mode 100755"
    [ -n "$approved" ] || die "changed $UI_PATH requires --ui-sha256 from the reviewed artifact"
    expected="$(git_op cat-file blob "$sha:rampilot/ALLOWED_BINARIES" 2>/dev/null | awk -v p="$UI_PATH" 'NF == 2 && $2 == p {print $1}')" ||
      die "$UI_PATH is not pinned in rampilot/ALLOWED_BINARIES"
    actual="$(git_op cat-file blob "$sha:$UI_PATH" | sha256sum | cut -c1-64)"
    [ "$actual" = "$expected" ] || die "$UI_PATH is not pinned to its blob digest in rampilot/ALLOWED_BINARIES"
    [ "$actual" = "$approved" ] || die "independent --ui-sha256 does not match $UI_PATH"
    say "pinned binary ok: $UI_PATH sha256 $actual"
  elif [ -n "$approved" ]; then
    die "--ui-sha256 supplied but the candidate does not change $UI_PATH"
  fi
  say "changed files from baseline:"
  git_op diff --stat "$BASELINE_SHA" "$sha" | sed 's/^/  /'
}

check_installed_ui() {
  [ -n "$1" ] || return 0
  local path="$OP_DIR/$UI_PATH"
  [[ -f "$path" && ! -L "$path" && -x "$path" ]] || die "checked-out $UI_PATH is not a regular executable"
  [ "$(sha256sum "$path" | cut -c1-64)" = "$1" ] || die "checked-out $UI_PATH does not match the approved digest"
}

install() (
  local bundle="${1:-}" sha="${2:-}" approved="" reboot=""
  [ -n "$bundle" ] && [ -n "$sha" ] || die "usage: install <bundle> <commit-sha> [--ui-sha256 HASH] [--reboot]"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "commit sha must be the full 40-character hash"
  shift 2
  while (($#)); do
    case "$1" in
      --reboot) [ -z "$reboot" ] || die "duplicate --reboot"; reboot=--reboot ;;
      --ui-sha256)
        [ -z "$approved" ] || die "duplicate --ui-sha256"
        [[ "${2:-}" =~ ^[0-9a-fA-F]{64}$ ]] || die "--ui-sha256 needs a 64-character hex digest"
        approved="${2,,}"; shift ;;
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
  check_candidate "$sha" "$approved"
  if [ "$original_head" = "$sha" ]; then
    check_installed_ui "$approved"
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
  if ! cmp -s "$PARAMS_DIR/DisableUpdates" <(printf 1); then param_put DisableUpdates 1; changed_updates=1; fi
  if [ -f "$MARKER" ]; then
    local aside="$MARKER.rampilot-set-aside"
    if [ -e "$aside" ]; then aside="$(mktemp "$aside.XXXXXX")"; fi
    mv -T "$MARKER" "$aside"; moved_marker="$aside"
    say "set aside staged update"
  fi
  record_installed_target "$sha"
  trap - EXIT
  say "installed $TARGET_BRANCH at $sha; DisableUpdates=1. Reboot to start it."
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
  [ "$reboot" = "--reboot" ] && $REBOOT_CMD
  return 0
}

verify() {  # onroad processes start on the installed commit and stay up
  local state="$STATE_DIR/last/state.env" target p pid1 pid2 failed=0
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
  [ "$failed" -eq 0 ] || die "one or more driving processes are not stable; consider rollback"
  say "verify ok on $target"
}

status() {
  say "branch=$(head_branch) head=$(head_sha)"
  say "DisableUpdates=$(param_get DisableUpdates) IsOnroad=$(param_get IsOnroad)"
  say "staged_update=$([ -f "$MARKER" ] && echo yes || echo no) set_aside=$([ -f "$MARKER.rampilot-set-aside" ] && echo yes || echo no)"
  [ -f "$STATE_DIR/last/state.env" ] && { say "last install state:"; sed 's/^/  /' "$STATE_DIR/last/state.env"; } || say "no install state recorded"
}

cmd="${1:-}"; shift || true
if [[ "$cmd" == preflight || "$cmd" == status || "$cmd" == verify ]]; then
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
  verify) verify ;;
  *) echo "usage: $0 {preflight|install <bundle> <sha> [--ui-sha256 HASH] [--reboot]|rollback [--reboot]|status|verify}" >&2; exit 64 ;;
esac
