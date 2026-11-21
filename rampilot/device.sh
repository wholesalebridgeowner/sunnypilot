#!/usr/bin/env bash
# RamPilot install and rollback on the comma three. Runs ON the device, as the comma user.
#
#   device.sh preflight
#   device.sh install <bundle> <commit-sha> [--reboot]
#   device.sh rollback [--reboot]
#   device.sh status
#   device.sh verify        (after the reboot: truck in Park, ignition on)
#
# install  fetches the RamPilot branch from a git bundle copied to the device, checks it is exactly the
#          expected commit, sits on the pinned baseline, and changes only text files outside Panda, safety,
#          model and hardware paths. It records the current state, turns sunnypilot's updater off
#          (DisableUpdates) and sets aside any staged update so the launcher cannot swap it in, then checks
#          out the branch. Nothing changes until every check passes.
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
ALLOWED_BINARY_PATHS="${ALLOWED_BINARY_PATHS:-selfdrive/ui/ui}"
VERIFY_PROCS="${VERIFY_PROCS:-selfdrive.controls.controlsd selfdrive.car.card selfdrive.selfdrived.selfdrived selfdrive.locationd.paramsd selfdrive.ui.soundd ^\./ui$}"
VERIFY_WAIT_S="${VERIFY_WAIT_S:-30}"
LOG_DIR="${LOG_DIR:-/data/log}"

die() { echo "REFUSED: $*" >&2; exit 2; }
say() { echo "$*"; }
param_get() { [ -f "$PARAMS_DIR/$1" ] && tr -d '\0\n' < "$PARAMS_DIR/$1" || echo ""; }
param_put() {  # atomic, the same on-disk form Params uses
  local tmp; tmp="$(mktemp "$PARAMS_DIR/.tmp_$1.XXXXXX")"
  printf '%s' "$2" > "$tmp" && sync "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$PARAMS_DIR/$1"
}
git_op() { git -C "$OP_DIR" "$@"; }
head_sha() { git_op rev-parse HEAD; }
head_branch() { git_op rev-parse --abbrev-ref HEAD; }

preflight() {
  [ -d "$OP_DIR/.git" ] || die "$OP_DIR is not a git checkout"
  [ -d "$PARAMS_DIR" ] || die "params directory $PARAMS_DIR missing"
  [ "$(param_get IsOnroad)" != "1" ] || die "the truck is onroad; park, turn it off and wait for offroad"
  local dirty; dirty="$(git_op status --porcelain --untracked-files=no)"
  [ -z "$dirty" ] || die "$OP_DIR has uncommitted changes: $(echo "$dirty" | head -5 | tr '\n' ' ')"
  local free_mb; free_mb="$(df -Pm "$OP_DIR" | awk 'NR==2 {print $4}')"
  [ "${free_mb:-0}" -ge "$MIN_FREE_MB" ] || die "only ${free_mb} MB free under $OP_DIR (need $MIN_FREE_MB)"
  say "preflight ok: branch=$(head_branch) head=$(head_sha) DisableUpdates=$(param_get DisableUpdates) free_mb=$free_mb staged_update=$([ -f "$MARKER" ] && echo yes || echo no)"
}

check_candidate() {  # $1 = sha already fetched
  local sha="$1" bad binary
  git_op merge-base --is-ancestor "$BASELINE_SHA" "$sha" || die "$sha does not contain baseline $BASELINE_SHA"
  bad="$(git_op diff --name-only "$BASELINE_SHA" "$sha" | grep -E "$DENY_RE" || true)"
  [ -z "$bad" ] || die "candidate touches protected paths: $(echo "$bad" | tr '\n' ' ')"
  binary="$(git_op diff --numstat "$BASELINE_SHA" "$sha" | awk '$1 == "-" || $2 == "-" {print $3}')"
  local b expected actual
  for b in $binary; do
    case " $ALLOWED_BINARY_PATHS " in *" $b "*) ;; *) die "candidate changes binary files: $b" ;; esac
    expected="$(git_op show "$sha:rampilot/ALLOWED_BINARIES" 2>/dev/null | awk -v p="$b" '$2 == p {print $1}')"
    actual="$(git_op show "$sha:$b" | sha256sum | cut -c1-64)"
    [ -n "$expected" ] && [ "$expected" = "$actual" ] || die "binary $b (sha256 $actual) is not pinned in rampilot/ALLOWED_BINARIES"
    say "pinned binary ok: $b sha256 $actual"
  done
  say "changed files from baseline:"
  git_op diff --stat "$BASELINE_SHA" "$sha" | sed 's/^/  /'
}

install() {
  local bundle="${1:-}" sha="${2:-}" reboot="${3:-}"
  [ -n "$bundle" ] && [ -n "$sha" ] || die "usage: install <bundle> <commit-sha> [--reboot]"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "commit sha must be the full 40-character hash"
  preflight
  if [ "$(head_sha)" = "$sha" ]; then say "already on $sha; nothing to do"; return 0; fi
  [ -f "$bundle" ] || die "bundle $bundle not found"
  git_op bundle verify "$(readlink -f "$bundle")" >/dev/null 2>&1 || die "bundle failed verification (missing baseline objects or damaged file)"
  git bundle list-heads "$(readlink -f "$bundle")" | grep -q " refs/heads/$TARGET_BRANCH$" || die "bundle has no $TARGET_BRANCH branch"
  git_op fetch -q "$(readlink -f "$bundle")" "+refs/heads/$TARGET_BRANCH:refs/rampilot/candidate" || die "could not fetch $TARGET_BRANCH from the bundle"
  [ "$(git_op rev-parse refs/rampilot/candidate)" = "$sha" ] || die "bundle branch is $(git_op rev-parse refs/rampilot/candidate), expected $sha"
  check_candidate "$sha"

  local ts state; ts="$(date -u +%Y%m%dT%H%M%SZ)"; state="$STATE_DIR/state-$ts"
  mkdir -p "$state"
  { echo "branch=$(head_branch)"; echo "head=$(head_sha)"; echo "disable_updates=$(param_get DisableUpdates)";
    echo "staged_update=$([ -f "$MARKER" ] && echo yes || echo no)"; echo "target=$sha"; } > "$state/state.env"
  ln -sfn "$state" "$STATE_DIR/last"
  say "recorded state in $state"

  param_put DisableUpdates 1
  if [ -f "$MARKER" ]; then mv -f "$MARKER" "$MARKER.rampilot-set-aside"; say "set aside staged update"; fi
  git_op checkout -q -B "$TARGET_BRANCH" "$sha"
  [ "$(head_sha)" = "$sha" ] || die "checkout did not land on $sha"
  say "installed $TARGET_BRANCH at $sha; DisableUpdates=1. Reboot to start it."
  [ "$reboot" = "--reboot" ] && $REBOOT_CMD
  return 0
}

rollback() {
  local reboot="${1:-}" state="$STATE_DIR/last/state.env"
  [ -f "$state" ] || die "no recorded install state at $state"
  preflight
  # shellcheck disable=SC1090
  local branch head disable_updates; branch="$(grep '^branch=' "$state" | cut -d= -f2-)"
  head="$(grep '^head=' "$state" | cut -d= -f2-)"; disable_updates="$(grep '^disable_updates=' "$state" | cut -d= -f2-)"
  [ "$head" = "$BASELINE_SHA" ] || die "recorded pre-install head $head is not the baseline $BASELINE_SHA; restore by hand"
  git_op cat-file -e "$head^{commit}" || die "baseline commit $head is missing from the checkout"
  if [ "$branch" = "HEAD" ] || [ -z "$branch" ]; then git_op checkout -q --detach "$head"; else git_op checkout -q -B "$branch" "$head"; fi
  [ "$(head_sha)" = "$head" ] || die "rollback did not land on $head"
  if [ -n "$disable_updates" ]; then param_put DisableUpdates "$disable_updates"; else rm -f "$PARAMS_DIR/DisableUpdates"; fi
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
case "$cmd" in
  preflight) preflight ;;
  install) install "$@" ;;
  rollback) rollback "$@" ;;
  status) status ;;
  verify) verify ;;
  *) echo "usage: $0 {preflight|install <bundle> <sha> [--reboot]|rollback [--reboot]|status|verify}" >&2; exit 64 ;;
esac
