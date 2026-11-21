#!/usr/bin/env bash
# Build the stock Qt screen plus the RamPilot patches in a disposable source tree.
set -euo pipefail
export LC_ALL=C
# A calling Git hook can export repository-specific paths that override git -C.
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

SOURCE_COMMIT="${SOURCE_COMMIT:-737a6c4236e843034680c951005b38d15815363f}"
SOURCE_URL="${SOURCE_URL:-https://github.com/sunnypilot/sunnypilot.git}"
OP_DIR="$(realpath -e -- "${OP_DIR:-/data/openpilot}")"
PARAMS_DIR="$(realpath -m -- "${PARAMS_DIR:-/data/params/d}")"
WORK="$(realpath -m -- "${WORK:-/data/rampilot-ui-build}")"
STATE_DIR="$(realpath -m -- "${STATE_DIR:-/data/ram-diag/rampilot}")"
HERE="$(dirname -- "$(realpath -e -- "${BASH_SOURCE[0]}")")"
die() { echo "REFUSED: $*" >&2; exit 2; }
inside() { [[ "$1" == "$2" || "$1" == "${2%/}/"* ]]; }
overlap() { inside "$1" "$2" || inside "$2" "$1"; }
digest() { local sum; sum="$(sha256sum < "$1")" || return 1; printf '%s\n' "${sum%% *}"; }
require_offroad() {
  local flag="$PARAMS_DIR/IsOnroad"
  [[ -f "$flag" && -r "$flag" ]] && cmp -s -- "$flag" <(printf 0) ||
    die "need a readable IsOnroad containing exactly 0"
}

require_offroad
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "need a full source commit SHA"
[[ -f "$OP_DIR/cereal/custom.capnp" ]] || die "no cereal schema in $OP_DIR"
for writable in "$WORK" "$STATE_DIR"; do
  overlap "$writable" "$OP_DIR" && die "build paths must be separate from OP_DIR"
  overlap "$writable" "$PARAMS_DIR" && die "build paths must be separate from PARAMS_DIR"
done
overlap "$WORK" "$STATE_DIR" && die "WORK and STATE_DIR must be separate"

mkdir -p -- "$WORK"
exec 9<"$WORK"
flock -n 9 || die "another build is using $WORK"
marker="$WORK/.rampilot-ui-work-v1"
marker_contents() { printf 'RamPilot UI build workspace v1\n%s\n' "$WORK"; }
if [[ -e "$marker" || -L "$marker" ]]; then
  [[ -f "$marker" && ! -L "$marker" ]] && cmp -s -- "$marker" <(marker_contents) ||
    die "invalid build workspace marker"
else
  shopt -s nullglob dotglob
  entries=("$WORK"/*)
  shopt -u dotglob
  (( ${#entries[@]} == 0 )) || die "WORK must be empty or marked by this script"
  marker_contents > "$marker"
fi
shopt -s nullglob
free_mb="$(df -Pm -- "$WORK" | awk 'END {print $4}')"
[[ "$free_mb" =~ ^[0-9]+$ && "$free_mb" -ge 6000 ]] || die "need 6 GB free on the WORK filesystem"

run="$(mktemp -d -- "$WORK/run.XXXXXXXX")"
stage=""
cleanup() {
  # Both names come from mktemp. Never clean a pre-existing workspace, checkout or artifact.
  [[ -z "$stage" ]] || rm -rf -- "$stage"
  rm -rf -- "$run"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
src="$run/source"
git init -q "$src"
git -C "$src" remote add origin "$SOURCE_URL"
git -C "$src" fetch -q --depth 1 origin "$SOURCE_COMMIT"
git -C "$src" checkout -q --detach FETCH_HEAD
head="$(git -C "$src" rev-parse HEAD)"
[[ "$head" == "$SOURCE_COMMIT" ]] || die "fetched source commit differs"
git -C "$src" submodule update -q --init --recursive --depth 1

manifest="$run/manifest.txt"
printf 'source_commit=%s\n' "$head" > "$manifest"
patches=("$HERE"/0*.patch)
(( ${#patches[@]} > 0 )) || die "no screen patches found"
for patch in "${patches[@]}"; do
  # Apply and hash the same snapshot even if the installed files change during the build.
  cp -- "$patch" "$run/input.patch"
  git -C "$src" apply --whitespace=nowarn "$run/input.patch"
  patch_hash="$(digest "$run/input.patch")"
  printf 'patch_sha256=%s  %s\n' "$patch_hash" "${patch##*/}" >> "$manifest"
done
schemas=("$OP_DIR"/cereal/*.capnp)
(( ${#schemas[@]} > 0 )) || die "no installed cereal schemas found"
for schema in "${schemas[@]}"; do
  name="${schema##*/}"
  # car.capnp is a symlink into opendbc in the pinned build. Preserve that link and
  # copy the installed bytes to its target inside the disposable source checkout.
  installed="$(realpath -e -- "$schema")"
  dest="$(realpath -m -- "$src/cereal/$name")"
  inside "$installed" "$OP_DIR" && [[ -f "$installed" ]] || die "invalid installed schema $name"
  inside "$dest" "$src" || die "schema destination escapes the build tree"
  cp -- "$installed" "$dest"
  schema_hash="$(digest "$dest")"
  printf 'schema_sha256=%s  cereal/%s\n' "$schema_hash" "$name" >> "$manifest"
done

# The pinned SConstruct uses clang++ and locates its Qt installation via qmake.
compiler="$(clang++ --version)"
compiler="${compiler%%$'\n'*}"
qt_version="$(qmake -query QT_VERSION)"
[[ -n "$compiler" && "$qt_version" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "could not identify clang++ and Qt"
printf 'compiler=%s\nqt_version=%s\n' "$compiler" "$qt_version" >> "$manifest"
output="$src/selfdrive/ui/ui"
[[ "$(realpath -m -- "$output")" == "$output" ]] || die "linked build output path"
[[ ! -e "$output" ]] || die "source checkout already contains a built UI"
require_offroad
(cd "$src" && scons -j"$(nproc)" "cache_dir=$run/cache" selfdrive/ui/ui)
[[ "$(realpath -e -- "$output")" == "$output" && -f "$output" && -x "$output" && -s "$output" ]] ||
  die "build produced no regular executable UI"
sha="$(digest "$output")"
printf 'output_sha256=%s\n' "$sha" >> "$manifest"
manifest_sha="$(digest "$manifest")"
file="ui-${sha:0:12}"

require_offroad
[[ "$(realpath -m -- "$STATE_DIR")" == "$STATE_DIR" ]] || die "artifact directory path changed during build"
mkdir -p -- "$STATE_DIR"
stage="$(mktemp -d -- "$STATE_DIR/.ui-build.XXXXXXXX")"
install -m 0755 -- "$output" "$stage/$file"
install -m 0644 -- "$manifest" "$stage/manifest.txt"
[[ "$(digest "$stage/$file")" == "$sha" && "$(digest "$stage/manifest.txt")" == "$manifest_sha" ]] ||
  die "staged files differ from the completed build"
published="$STATE_DIR/ui-build-$manifest_sha"
if [[ ! -e "$published" && ! -L "$published" ]]; then
  # Another workspace can finish the identical build between the check and the rename.
  mv -Tn -- "$stage" "$published" || [[ -d "$published" ]] || die "could not publish completed build"
fi
[[ -d "$published" && ! -L "$published" && -f "$published/$file" &&
   ! -L "$published/$file" && -x "$published/$file" && -f "$published/manifest.txt" &&
   ! -L "$published/manifest.txt" ]] &&
  cmp -s -- "$manifest" "$published/manifest.txt" && [[ "$(digest "$published/$file")" == "$sha" ]] ||
  die "existing artifact does not match this build"
printf 'built selfdrive/ui/ui sha256=%s\nstaged at %s/%s\nmanifest at %s/manifest.txt\n' "$sha" "$published" "$file" "$published"
