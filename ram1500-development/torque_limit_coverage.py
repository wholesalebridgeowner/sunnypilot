#!/usr/bin/env python3
"""Read-only separation of torque-controller saturation from native LKAS command limits.

The controller's torqueState.output and carControl.actuators.torque are
dimensionless. carOutput.actuatorsOutput.torqueOutputCan is a separate native
CAN-command scale. Never infer an ECU limit or physical torque from equality
of these values. This module only counts observations in a time window.
"""
from __future__ import annotations

import math


def finite(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def flag(value):
    if value is True or str(value).strip().lower() in ("1", "true"):
        return True
    if value is False or str(value).strip().lower() in ("0", "false"):
        return False
    return None


def audit(rows, *, begin_seconds=1800.0, end_seconds=1807.0,
          normalized_ceiling=0.995, steer_max_native=350.0,
          near_native_fraction=0.95):
    """Aggregate *independent* native and normalized streams (no causal join).

    The device's source-level STEER_MAX is a reference, not a measured
    physical EPS ceiling, and a rate-limited LKAS command may be smaller.
    """
    parameters = (begin_seconds, end_seconds, normalized_ceiling,
                  steer_max_native, near_native_fraction)
    if any(finite(v) is None for v in parameters):
        raise ValueError("all audit parameters must be finite numbers")
    if not (0 <= begin_seconds <= end_seconds and
            0 < normalized_ceiling <= 1 and steer_max_native > 0 and
            0 < near_native_fraction <= 1):
        raise ValueError("invalid audit time interval or limit")
    result = {
        "intervalSeconds": [begin_seconds, end_seconds],
        "referenceNativeLimit": steer_max_native,
        "normalizedCeilingThreshold": normalized_ceiling,
        "nearNativeReferenceFraction": near_native_fraction,
        "controller": {"samples": 0, "validOutput": 0,
                       "outputAtNormalizedCeiling": 0,
                       "saturatedFlagTrue": 0, "maxAbsOutput": None},
        "request": {"samples": 0, "validTorque": 0, "latActiveTrue": 0,
                    "torqueAtNormalizedCeiling": 0, "maxAbsTorque": None},
        "canCommand": {"samples": 0, "validNativeTorque": 0,
                       "nearNativeReference": 0, "maxAbsNativeTorque": None},
        "discardedInvalidMessages": 0,
        "unknownValidityMessages": 0,
        "caveat": ("Sample counts are per-stream, not matched pairs. Saturated normalized "
                   "output does not mean the CAN command reached the reference STEER_MAX "
                   "or that EPS torque/road-wheel movement saturated."),
    }
    configurations = {
        "controlsState": ("controller", "lateralOutput",
                          "outputAtNormalizedCeiling", "maxAbsOutput"),
        "carControl": ("request", "requestedTorque",
                       "torqueAtNormalizedCeiling", "maxAbsTorque"),
        "carOutput": ("canCommand", "outputTorqueCan",
                      "nearNativeReference", "maxAbsNativeTorque"),
    }
    for row in rows:
        config = configurations.get(row.get("stream"))
        if config is None:
            continue
        when = finite(row.get("seconds"))
        if when is None or not begin_seconds <= when <= end_seconds:
            continue
        validity = flag(row.get("valid"))
        if validity is False:
            result["discardedInvalidMessages"] += 1
            continue
        if validity is None:
            result["unknownValidityMessages"] += 1
        group_name, column, counter_name, maximum_key = config
        group = result[group_name]
        group["samples"] += 1
        if group_name == "controller" and flag(row.get("lateralSaturated")) is True:
            group["saturatedFlagTrue"] += 1
        if group_name == "request" and flag(row.get("carControlLatActive")) is True:
            group["latActiveTrue"] += 1
        value = finite(row.get(column))
        if value is None:
            continue
        valid_key = {"controller": "validOutput", "request": "validTorque",
                     "canCommand": "validNativeTorque"}[group_name]
        group[valid_key] += 1
        magnitude = abs(value)
        group[maximum_key] = max(magnitude, group[maximum_key] or 0)
        threshold = (near_native_fraction * steer_max_native
                     if group_name == "canCommand" else normalized_ceiling)
        if magnitude >= threshold:
            group[counter_name] += 1
    if result["canCommand"]["maxAbsNativeTorque"] is not None:
        result["canCommand"]["maxFractionOfReferenceLimit"] = round(
            result["canCommand"]["maxAbsNativeTorque"] / steer_max_native, 4)
    else:
        result["canCommand"]["maxFractionOfReferenceLimit"] = None
    return result
