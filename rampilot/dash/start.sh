#!/usr/bin/env bash
# Explicit foreground start for an approved, parked-device installation.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: bash rampilot/dash/start.sh <private LAN IPv4 address>" >&2
  exit 2
fi
params_dir="${PARAMS_DIR:-/data/params/d}"
if ! cmp -s "$params_dir/IsOnroad" <(printf 0); then
  echo "The dashboard start step requires a confirmed offroad state." >&2
  exit 1
fi
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
export PYTHONPATH="$root:$root/opendbc_repo${PYTHONPATH:+:$PYTHONPATH}"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
exec python3 -m rampilot.dash.server serve --init-token --bind "$1"
