"""The measures of this build.

A code constant, never a remote setting: a measure is switched off by taking
it out of this tuple in the next agent build. Each entry is a Measure class
(see measure_api.py); the pipeline builds a fresh object of each for every
drive and runs the owner-facing ones first.

The first four are owner-facing and send flat fields in every summary. The
learned delay is owner-facing too; it sends one block, `delay`, from the
learner's first message of the drive on. The last five are log-only: each
sends one block, named like the measure, when it has something to say. They
are recorded and stored and nothing reads them as a flag or a verdict. If the
agent ever costs too much on the Comma, the log-only ones are taken out in
this order: lag, ay, swings, lane, curve. The owner-facing ones never are.
"""
from __future__ import annotations

from measure_brake import Braking
from measure_curve import CurveLag, CurveSums
from measure_delay import LearnedDelay
from measure_lane_change import LaneChanges
from measure_lane_sway import LaneSway
from measure_lat_rms import LateralRms
from measure_limit import SteeringLimit
from measure_steering import SteeringTime
from measure_swings import WheelSwings

MEASURES = (SteeringTime, SteeringLimit, Braking, LaneChanges, LearnedDelay,
            LaneSway, WheelSwings, LateralRms, CurveSums, CurveLag)
