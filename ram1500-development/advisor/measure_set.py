"""The measures of this build.

A code constant, never a remote setting: a measure is switched off by taking
it out of this tuple in the next agent build. Each entry is a Measure class
(see measure_api.py); the pipeline builds a fresh object of each for every
drive and runs the owner-facing ones first.
"""
from __future__ import annotations

MEASURES = ()
