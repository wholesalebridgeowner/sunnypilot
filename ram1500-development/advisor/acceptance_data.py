"""Access to the private drive dataset for the acceptance tests.

The dataset never enters the repository. An acceptance test asks here for it
and is skipped when it is not on this machine:

    ADVISOR_DATASET_DIR   folder that holds  npz/drive_NN_q.npz, npz/drive_NN_r.npz
                          and acceptance_expected.json

Every expected number lives in that one JSON file beside the dataset, one
section per test. No drive number, and nothing else derived from a drive, is
written into a test. Test support only: never installed on the Comma.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

ENV = "ADVISOR_DATASET_DIR"
EXPECTED_FILE = "acceptance_expected.json"


def dataset_dir() -> Path:
    """The dataset folder; skips the calling test when there is none."""
    value = os.environ.get(ENV)
    if not value:
        raise unittest.SkipTest(f"private dataset not available (set {ENV})")
    folder = Path(value)
    if not (folder / "npz").is_dir():
        raise unittest.SkipTest(f"{ENV} does not hold an npz folder")
    return folder


def expected(section: str) -> dict:
    """One section of the expected-numbers file; skips the calling test when it is missing."""
    path = dataset_dir() / EXPECTED_FILE
    if not path.is_file():
        raise unittest.SkipTest(f"{EXPECTED_FILE} is not beside the dataset")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if section not in data:
        raise unittest.SkipTest(f"{EXPECTED_FILE} has no section {section}")
    return data[section]


def npz_path(drive: int, kind: str = "q") -> Path | None:
    """Table file of one drive ("q" decimated log, "r" full-rate log); None when that drive has none."""
    path = dataset_dir() / "npz" / f"drive_{drive:02d}_{kind}.npz"
    return path if path.is_file() else None
