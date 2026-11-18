"""Pinned Cap'n Proto schema for the Nov 14 drive build, with integrity checks.

The drive ran sunnypilot commit 1f7233cb983f3c13fdbba47a86f8fbc796ad9211. Decoding its logs with this
branch's (April 2026) schema happens to keep the ordinals of the events we care about, but renames
driver-monitoring events and changes many structs. To make decoding reproducible we fetch the exact
schema files from that commit and verify each against its git blob SHA-1 from the commit's tree.

Network use: `fetch_pinned_schema` downloads five public schema files from raw.githubusercontent.com.
Nothing else in ramtools touches the network.
"""
from __future__ import annotations

import hashlib
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .constants import DRIVE_BUILD_COMMIT

# local path inside the schema dir -> (path in the sunnypilot repo at the drive commit, git blob sha1)
# cereal/car.capnp is a symlink (mode 120000) to opendbc_repo/opendbc/car/car.capnp at that commit.
PINNED_SCHEMA_FILES: dict[str, tuple[str, str]] = {
  "log.capnp": ("cereal/log.capnp", "474b4777cb766d5a26611556090d85a61e48f1fc"),
  "car.capnp": ("opendbc_repo/opendbc/car/car.capnp", "d237e353d549800b2db6aff58f313e89799df958"),
  "custom.capnp": ("cereal/custom.capnp", "8d4bc3cb82e3f858c644496491f615e664986334"),
  "legacy.capnp": ("cereal/legacy.capnp", "a8fa5e4a1f7d5d1da94ae7136773e0ec4a854987"),
  "include/c++.capnp": ("cereal/include/c++.capnp", "2bda54717920ab980bd3c2ff2c245d9a525685d3"),
}
RAW_URL = "https://raw.githubusercontent.com/sunnypilot/sunnypilot/{commit}/{path}"


class SchemaIntegrityError(RuntimeError):
  pass


def git_blob_sha1(data: bytes) -> str:
  """SHA-1 that git assigns to a file's content (`git hash-object`)."""
  return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def verify_schema_dir(schema_dir: Path) -> dict[str, str]:
  """Return {file: 'ok' | 'missing' | 'mismatch:<sha>'} for every pinned file."""
  status = {}
  for local, (_, expected) in PINNED_SCHEMA_FILES.items():
    p = Path(schema_dir) / local
    if not p.exists():
      status[local] = "missing"
      continue
    actual = git_blob_sha1(p.read_bytes())
    status[local] = "ok" if actual == expected else f"mismatch:{actual}"
  return status


def schema_is_pinned(schema_dir: Path) -> bool:
  return all(v == "ok" for v in verify_schema_dir(schema_dir).values())


def fetch_pinned_schema(dest: Path, commit: str = DRIVE_BUILD_COMMIT,
                        opener: Callable = urllib.request.urlopen) -> Path:
  """Download (if needed) and verify the pinned schema files into `dest`. Raises on any hash mismatch."""
  dest = Path(dest)
  for local, (repo_path, expected) in PINNED_SCHEMA_FILES.items():
    target = dest / local
    if target.exists() and git_blob_sha1(target.read_bytes()) == expected:
      continue
    with opener(RAW_URL.format(commit=commit, path=repo_path)) as resp:
      data = resp.read()
    actual = git_blob_sha1(data)
    if actual != expected:
      raise SchemaIntegrityError(f"{repo_path}@{commit[:8]}: expected blob {expected}, got {actual}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
  return dest


def load_log_schema(schema_dir: Path, require_pinned: bool = True):
  """Load log.capnp with pycapnp. Imported lazily so the analysis modules work without pycapnp."""
  schema_dir = Path(schema_dir)
  if require_pinned and not schema_is_pinned(schema_dir):
    raise SchemaIntegrityError(f"schema dir {schema_dir} does not match the pinned drive-build schema: "
                               f"{verify_schema_dir(schema_dir)}")
  import capnp  # noqa: PLC0415  (optional dependency)
  return capnp.load(str(schema_dir / "log.capnp"), imports=[str(schema_dir), str(schema_dir / "include")])
