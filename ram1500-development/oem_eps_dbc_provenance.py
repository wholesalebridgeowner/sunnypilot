#!/usr/bin/env python3
"""Offline source-pinned EPS status audit; never reads CAN logs or sends vehicle commands.

Both input files must come from sunnypilot@1f7233cb's opendbc_repo paths.
A DBC names signals, not message transmitters, OEM permission semantics, or bus topology.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

DRIVE_COMMIT = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
COMMON_BLOB = "df913561de2fad009ba27288cd8d5ec00a36ca02"
MAPPING_BLOB = "fa408f1315f34feffad23c4637c7df905b5f8d7c"
TARGET = "_stellantis_common_ram_dt_generated.dbc"
EXPECTED = {
  "LKAS_STATE": "23|4@0+",
  "TORQUE_OVERLAY_STATUS": "6|4@0+",
  "AUTO_PARK_HAS_CONTROL_2": "51|1@0+",
}


class SourceAuditError(ValueError):
  pass


def git_blob_sha(data: bytes) -> str:
  """Same SHA-1 object calculation as git hash-object, including blob header."""
  return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\x00" + data).hexdigest()


def _source_text(data: bytes, expected: str, label: str) -> str:
  if git_blob_sha(data) != expected:
    raise SourceAuditError(f"{label} git blob SHA differs from pinned drive-build source")
  try:
    return data.decode("utf-8")
  except UnicodeDecodeError as exc:
    raise SourceAuditError(f"{label} is not UTF-8") from exc


def _dbc_signals(text: str) -> dict[str, str]:
  # Require one exact original Stellantis frame; avoid matching the following frame.
  matches = list(re.finditer(r"(?m)^BO_\s+(\d+)\s+(\w+):[^\n]*$", text))
  chosen = [i for i, m in enumerate(matches) if m.group(1) == "544" and m.group(2) == "EPS_2"]
  if len(chosen) != 1:
    raise SourceAuditError("expected one source EPS_2 frame (544)")
  i = chosen[0]
  block = text[matches[i].end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
  signals: dict[str, str] = {}
  for name, layout in re.findall(r"(?m)^\s*SG_\s+(\w+)\s*:\s*(\d+\|\d+@[01][+-])\b", block):
    if name in signals:
      raise SourceAuditError(f"duplicate EPS_2 signal {name}")
    signals[name] = layout
  for name, layout in EXPECTED.items():
    if signals.get(name) != layout:
      raise SourceAuditError(f"{name} layout disagrees with pinned EPS_2")
  return signals


def _ram_mapping(text: str) -> dict[int, int]:
  try:
    tree = ast.parse(text)
    assignments = [node.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "chrysler_to_ram" for t in node.targets)]
    if len(assignments) != 1:
      raise SourceAuditError("missing or ambiguous Chrysler address mapping")
    mapping = ast.literal_eval(assignments[0])[TARGET]
  except (SyntaxError, ValueError, TypeError, KeyError) as exc:
    raise SourceAuditError("cannot statically resolve RAM DT mapping") from exc
  if not isinstance(mapping, dict) or mapping.get(544) != 49:
    raise SourceAuditError("EPS_2 address is not mapped 544 -> 49")
  if list(mapping.values()).count(49) != 1:
    raise SourceAuditError("multiple source addresses map to EPS_2 CAN address")
  return mapping


def audit(common: bytes, generator: bytes, *,
          expected_common: str = COMMON_BLOB,
          expected_generator: str = MAPPING_BLOB) -> dict:
  source = _source_text(common, expected_common, "Stellantis common DBC")
  generated = _source_text(generator, expected_generator, "RAM mapping generator")
  signals = _dbc_signals(source)
  address_map = _ram_mapping(generated)
  return {
    "recordedBuild": DRIVE_COMMIT,
    "sourceBlobSha": expected_common,
    "generatorBlobSha": expected_generator,
    "sourceFrame": "EPS_2",
    "sourceAddressDecimal": 544,
    "ramDtAddressDecimal": address_map[544],
    "ramDtAddressHex": "0x31",
    "statusSignals": {key: signals[key] for key in EXPECTED},
    "evidenceScope": "DBC layout/address mapping only; observational EPS status decoding",
    "notEstablished": [
      "PAM CAN frame identity or ECU sender authentication",
      "CAN-C visibility at the Comma harness",
      "undocumented EPS status ordinal semantics",
      "authorization or feasibility of third-party low-speed actuation",
    ],
  }


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--common-dbc", type=Path, required=True,
                      help="pinned _stellantis_common.dbc from recorded 1f7233cb tree")
  parser.add_argument("--ram-generator", type=Path, required=True,
                      help="pinned _stellantis_common_ram.py from recorded 1f7233cb tree")
  args = parser.parse_args()
  try:
    result = audit(args.common_dbc.read_bytes(), args.ram_generator.read_bytes())
  except (OSError, SourceAuditError) as exc:
    parser.error(str(exc))
  print(json.dumps(result, indent=2))


if __name__ == "__main__":
  main()
