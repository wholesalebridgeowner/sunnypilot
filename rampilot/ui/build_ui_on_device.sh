#!/usr/bin/env bash
# Build RamPilot's screen program (selfdrive/ui/ui) ON the comma three, from sunnypilot's own source for the
# installed build plus RamPilot's screen patches. Run as the comma user with the truck parked and offroad,
# after RamPilot is installed (its cereal schema is copied into the build). Does not touch /data/openpilot.
#
#   bash build_ui_on_device.sh            builds into $WORK and stages the binary in $STATE_DIR/ui-<sha12>
set -euo pipefail
SOURCE_COMMIT="${SOURCE_COMMIT:-737a6c4236e843034680c951005b38d15815363f}"   # sunnypilot master for 1f7233cb
SOURCE_URL="${SOURCE_URL:-https://github.com/sunnypilot/sunnypilot.git}"
OP_DIR="${OP_DIR:-/data/openpilot}"
WORK="${WORK:-/data/rampilot-ui-build}"
STATE_DIR="${STATE_DIR:-/data/ram-diag/rampilot}"
HERE="$(cd "$(dirname "$0")" && pwd)"
die() { echo "REFUSED: $*" >&2; exit 2; }

[ "$(tr -d '\0\n' < /data/params/d/IsOnroad 2>/dev/null || echo 0)" != "1" ] || die "the truck is onroad"
free_mb="$(df -Pm /data | awk 'NR==2 {print $4}')"; [ "$free_mb" -ge 6000 ] || die "need 6 GB free on /data, have ${free_mb} MB"
[ -f "$OP_DIR/cereal/custom.capnp" ] || die "no cereal schema in $OP_DIR"

if [ ! -d "$WORK/.git" ]; then
  git clone --filter=blob:none --no-checkout "$SOURCE_URL" "$WORK"
fi
git -C "$WORK" fetch -q --depth 1 origin "$SOURCE_COMMIT"
git -C "$WORK" checkout -q --force "$SOURCE_COMMIT"
git -C "$WORK" submodule update -q --init --recursive --depth 1
git -C "$WORK" reset -q --hard "$SOURCE_COMMIT"
for p in "$HERE"/0*.patch; do git -C "$WORK" apply --whitespace=nowarn "$p" || die "patch $p does not apply"; done
cp "$OP_DIR"/cereal/*.capnp "$WORK/cereal/"          # the schema RamPilot runs with

cd "$WORK"
scons -j"$(nproc)" selfdrive/ui/ui
[ -x selfdrive/ui/ui ] || die "build produced no selfdrive/ui/ui"
sha="$(sha256sum selfdrive/ui/ui | cut -c1-64)"
mkdir -p "$STATE_DIR"; cp selfdrive/ui/ui "$STATE_DIR/ui-${sha:0:12}"
echo "built selfdrive/ui/ui sha256=$sha"
echo "staged at $STATE_DIR/ui-${sha:0:12}; copy it to the laptop and commit it as selfdrive/ui/ui with this sha in rampilot/ALLOWED_BINARIES"
