"""Access to the private drive dataset for the acceptance tests.

The dataset never enters the repository. An acceptance test asks here for it
and is skipped when it is not on this machine:

    ADVISOR_DATASET_DIR   folder that holds  npz/drive_NN_q.npz, npz/drive_NN_r.npz
                          and acceptance_expected.json

Every expected number lives in that one JSON file beside the dataset, one
section per test. No drive number, and nothing else derived from a drive, is
written into a test. Test support only: never installed on the Comma.

`qlog_run` gives what the pipeline sends for one drive with the measures of
this build. Each drive is replayed once per test run, however many tests ask.
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


_RUNS = {}


def qlog_run(drive: int, without=()) -> dict | None:
    """One drive's decimated-log tables through the pipeline with the measures of this build.

    Returns {"summaries": [...], "records": {type: [...]}, "end": drive-end fields}, or None when the drive has
    no table. `without` names streams to leave out. The result is kept: do not change it.
    """
    key = (drive, tuple(without))
    if key not in _RUNS:
        path = npz_path(drive, "q")
        if path is None:
            _RUNS[key] = None
        else:
            import npz_input
            from pipeline import Pipeline, PipelineConfig

            pipeline = Pipeline(PipelineConfig.for_decimated_log())
            messages = []
            for batch in npz_input.decimated_batches(npz_input.load(path), without=without):
                messages.extend(pipeline.ingest_tick(batch))
            messages.extend(pipeline.end())
            records = {}
            for kind, data in messages:
                if kind != "summary":
                    records.setdefault(kind, []).append(data)
            _RUNS[key] = {"summaries": [data for kind, data in messages if kind == "summary"],
                          "records": records, "end": pipeline.drive_end_fields()}
    return _RUNS[key]
