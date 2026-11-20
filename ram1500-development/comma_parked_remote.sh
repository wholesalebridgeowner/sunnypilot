#!/usr/bin/env bash
# Operator-initiated, diagnostic-ONLY Comma maintenance from Android Termux or Linux/Raspberry Pi.
# Requires authenticated SSH reachability to a parked Comma; neither Railway nor
# a Pi on a different LAN can reach a private Comma without an established private link.
set -euo pipefail
umask 077

MODE="${1:-help}"
HOST="${2:-}"
CONFIRM="${3:-}"
SOURCE_REPO="wholesalebridgeowner/sunnypilot"
SOURCE_COMMIT="dc94a83a121d7e53d7605973cb2efae3719f140d"
SOURCEDIR="ram1500-development"
PINNED_INSTALLED_COMMIT="1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
FILES=(device_telemetry.py parked_capture_acceptance.py summarize_can_capture.py)
BLOBS=(af3e8e4d832bf378820026b41fc1eb96e563b0ed
       972dcef6eac3ee5345be7b31d5b5391e850daf40
       6c28788d3d7b4278c5e6f5b5e2cc7be6e700ac2a)

usage() {
  cat <<'USAGE'
Use from Android Termux or a Raspberry Pi / Linux shell:
  bash comma_parked_remote.sh preflight <comma-host-or-LAN-IP>
  bash comma_parked_remote.sh install   <comma-host-or-LAN-IP> I_AM_PARKED
  bash comma_parked_remote.sh capture   <comma-host-or-LAN-IP> I_AM_PARKED
  bash comma_parked_remote.sh rollback  <comma-host-or-LAN-IP> I_AM_PARKED

The Comma must be reachable by authorized SSH as comma@HOST.
Preflight is read-only. Install copies only passive diagnostics to /data/ram-diag;
it does not launch any process, change driving software, or change Panda/ECU.
Capture requires ignition/onroad while parked; it runs once (120 s), then
validates CPU, RSS, msgq subscribers and faults. No automatic retry on failure.
All raw data remains on the Comma. Rollback restores only diagnostic files.
USAGE
}
[[ "$MODE" =~ ^(preflight|install|capture|rollback)$ ]] || { usage; exit 2; }
[[ "$HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,250}$ ]] || { echo "Provide one reachable host or IPv4 address (no URL, port or username)" >&2; exit 2; }
for binary in ssh scp; do command -v "$binary" >/dev/null || { echo "$binary missing on operator device" >&2; exit 2; }; done
TARGET="comma@$HOST"
SSH_OPTS=(-o ConnectTimeout=10 -o StrictHostKeyChecking=ask -o NumberOfPasswordPrompts=0)
# Do not auto-accept an unknown SSH host key or bypass public key authentication.
remote() { ssh "${SSH_OPTS[@]}" "$TARGET" /bin/sh -s; }

preflight() {
  remote <<'REMOTE'
set -eu
expected="1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
test -d /data/openpilot && test -x /usr/local/venv/bin/python3 || { echo 'NO_GO: original installation or Python missing'; exit 11; }
actual=$(git -C /data/openpilot rev-parse HEAD)
test "$actual" = "$expected" || { echo 'NO_GO: installed source differs from pinned baseline'; exit 12; }
test -z "$(git --no-optional-locks -C /data/openpilot status --porcelain)" || { echo 'NO_GO: installed tree has local changes'; exit 13; }
test "$(uname -m)" = aarch64 || { echo 'NO_GO: not the expected device architecture'; exit 14; }
test -w /data || { echo 'NO_GO: /data not writable for diagnostic files'; exit 15; }
free_mb=$(df -Pm /data | awk 'NR==2 {print $4}')
test "${free_mb:-0}" -ge 1000 || { echo 'NO_GO: insufficient free storage'; exit 16; }
printf 'READY: source verified, architecture aarch64, free /data >=1 GiB, no controller changes.\n'
REMOTE
}
case "$MODE" in
  preflight) preflight; exit 0 ;;
esac

if [[ "$CONFIRM" != "I_AM_PARKED" ]]; then
  echo "NO_GO: parked-owner confirmation missing. No change made." >&2
  exit 2
fi
preflight

if [[ "$MODE" == "install" ]]; then
  for binary in curl git; do command -v "$binary" >/dev/null || { echo "$binary needed on operator device" >&2; exit 2; }; done
  TMP="$(mktemp -d)"
  trap 'rm -rf -- "$TMP"' EXIT
  for i in "${!FILES[@]}"; do
    name="${FILES[$i]}"
    url="https://raw.githubusercontent.com/$SOURCE_REPO/$SOURCE_COMMIT/$SOURCEDIR/$name"
    curl --fail --location --silent --show-error --retry 2 --max-time 40 "$url" -o "$TMP/$name"
    observed="$(git hash-object "$TMP/$name")"
    [[ "$observed" == "${BLOBS[$i]}" ]] || { echo "NO_GO: pinned source integrity mismatch for $name" >&2; exit 21; }
  done
  # This is the ONLY upload path. Never copy into /data/openpilot.
  STAGE="$(remote <<'REMOTE'
set -eu
umask 077
mkdir -p /data/ram-diag
test ! -L /data/ram-diag || { echo 'NO_GO: diagnostic directory symlink'; exit 31; }
test ! -e /data/ram-diag/last-diagnostic-backup || { echo 'NO_GO: previous diagnostic install exists; inspect before another update'; exit 34; }
chmod 700 /data/ram-diag
mktemp -d /data/ram-diag/.incoming-XXXXXX
REMOTE
)"
  [[ "$STAGE" =~ ^/data/ram-diag/\.incoming-[A-Za-z0-9]+$ ]] || { echo 'NO_GO: unexpected staging destination' >&2; exit 31; }
  for name in "${FILES[@]}"; do
    scp "${SSH_OPTS[@]}" -q "$TMP/$name" "$TARGET:$STAGE/$name"
  done
  ssh "${SSH_OPTS[@]}" "$TARGET" /bin/sh -s -- "$STAGE" <<'REMOTE'
set -eu
umask 077
stage="$1"
case "$stage" in /data/ram-diag/.incoming-*) ;; *) exit 32;; esac
/usr/local/venv/bin/python3 -m py_compile "$stage/device_telemetry.py" "$stage/parked_capture_acceptance.py" "$stage/summarize_can_capture.py"
test "$(git hash-object "$stage/device_telemetry.py")" = af3e8e4d832bf378820026b41fc1eb96e563b0ed || exit 35
test "$(git hash-object "$stage/parked_capture_acceptance.py")" = 972dcef6eac3ee5345be7b31d5b5391e850daf40 || exit 36
test "$(git hash-object "$stage/summarize_can_capture.py")" = 6c28788d3d7b4278c5e6f5b5e2cc7be6e700ac2a || exit 37
mkdir -p /data/ram-diag/backups
backup=$(mktemp -d /data/ram-diag/backups/backup-XXXXXX)
# Back up every file before moving the first candidate into place.
for name in device_telemetry.py parked_capture_acceptance.py summarize_can_capture.py; do
  if test -f "/data/ram-diag/$name"; then
    cp -p "/data/ram-diag/$name" "$backup/$name"
  else
    touch "$backup/$name.was_absent"
  fi
done
printf '%s\n' "$backup" > /data/ram-diag/last-diagnostic-backup
for name in device_telemetry.py parked_capture_acceptance.py summarize_can_capture.py; do
  chmod 600 "$stage/$name"
  mv "$stage/$name" "/data/ram-diag/$name"
done
chmod 600 /data/ram-diag/last-diagnostic-backup
rmdir "$stage/__pycache__" 2>/dev/null || true
rm -f "$stage/__pycache__"/*.pyc 2>/dev/null || true
rmdir "$stage/__pycache__" 2>/dev/null || true
rmdir "$stage" 2>/dev/null || true
echo 'INSTALLED: read-only diagnostics staged outside /data/openpilot; no capture started'
REMOTE
  exit 0
fi

if [[ "$MODE" == "capture" ]]; then
  remote <<'REMOTE'
set -eu
test -f /data/ram-diag/device_telemetry.py &&
test -f /data/ram-diag/parked_capture_acceptance.py || { echo 'NO_GO: diagnostic package not installed'; exit 41; }
cd /data/openpilot
# This checks msgq slots before allocating even one subscriber.
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py --check-slots
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/data/ram-diag")
import device_telemetry as d
counts = d.msgq_reader_counts(d.SERVICES)
bad = sorted(name for name in d.SERVICES
             if type(counts.get(name)) is not int
             or counts[name] >= d.NUM_READERS)
if bad:
    print("NO_GO: missing or fully allocated msgq subscriber slots; do not retry before a parked reboot")
    raise SystemExit(41)
print("READY: subscriber headroom confirmed for one parked run")
PY
# Only one parked trial per Comma reboot; no --allow-moving or --all-can-ids.
mkdir -p /data/ram-telemetry
chmod 700 /data/ram-telemetry
session=$(mktemp -d /data/ram-telemetry/session-XXXXXX)
# Separate old and new sessions; no previous captures enter the acceptance gate.
PYTHONPATH=/data/openpilot timeout 150 /usr/local/venv/bin/python3 /data/ram-diag/device_telemetry.py \
 --output-dir "$session" --minutes 2 --sample-hz 2 --max-mb 8 \
 --keep-files 2 --max-cpu-pct 15 --max-private-mb 150 --drain-ms 50
/usr/local/venv/bin/python3 /data/ram-diag/parked_capture_acceptance.py \
 "$session"/ram-telemetry-*.jsonl \
 --output "$session/acceptance.json"
REMOTE
  exit 0
fi

if [[ "$MODE" == "rollback" ]]; then
  remote <<'REMOTE'
set -eu
backup=$(cat /data/ram-diag/last-diagnostic-backup 2>/dev/null || true)
case "$backup" in /data/ram-diag/backups/backup-*) ;; *) echo 'NO_GO: no valid backup marker'; exit 51;; esac
test -d "$backup" || { echo 'NO_GO: backed-up diagnostic scripts missing'; exit 52; }
for name in device_telemetry.py parked_capture_acceptance.py summarize_can_capture.py; do
  if test -f "$backup/$name"; then
    cp -p "$backup/$name" "/data/ram-diag/$name"
  elif test -f "$backup/$name.was_absent"; then
    rm -f "/data/ram-diag/$name"
  else
    echo 'NO_GO: incomplete diagnostic backup'; exit 53
  fi
done
mv /data/ram-diag/last-diagnostic-backup "$backup/rollback-complete"
echo 'ROLLED_BACK: diagnostic files only. msgq reader counts still require normal reboot before retrying.'
REMOTE
fi
