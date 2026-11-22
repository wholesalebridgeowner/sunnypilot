"""Owner-request domains for the pinned sunnypilot settings screen, build 1f7233cb.

The CLI parses text before signing. Both service and agent validate the exact
JSON types here. Device Params.put also distinguishes bool, int and float.
Advisor-generated suggestions have their own narrower guard in settings_guard.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re

from settings_guard import GuardError


OWNER_BOOL_KEYS = frozenset({
    "AutoLaneChangeBsmDelay", "BlindSpot", "CustomAccIncrementsEnabled",
    "DisengageOnAccelerator", "DynamicExperimentalControl", "GsmMetered",
    "GsmRoaming", "IsLdwEnabled", "LagdToggle", "LaneTurnDesire", "Mads",
    "MadsMainCruiseAllowed", "MadsUnifiedEngagementMode", "NeuralNetworkLateralControl",
    "OpenpilotEnabledToggle", "QuietMode", "RainbowMode", "RecordFront", "ShowAdvancedControls",
})

# The values written to Params can differ from the index displayed on screen.
# BlinkerPauseLateralControl is registered as INT even though its widget is a toggle.
INT_VALUES = {
    "AutoLaneChangeTimer": range(-1, 6),
    "BlinkerMinLateralControlSpeed": range(0, 256, 5),
    "BlinkerPauseLateralControl": (0, 1),
    "Brightness": (0, 1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100),
    "ChevronInfo": range(5),
    "CustomAccLongPressIncrement": (1, 5, 10),
    "CustomAccShortPressIncrement": range(1, 11),
    "DeviceBootMode": (0, 1),
    "DevUIInfo": (0, 1, 2),
    "HyundaiLongitudinalTuning": (0, 1, 2),
    "InteractivityTimeout": range(0, 121, 10),
    "LongitudinalPersonality": (0, 1, 2),
    "MadsSteeringMode": (0, 1, 2),
    "MaxTimeOffroad": (0, 5, 10, 15, 30, 60, 120, 180, 300, 600, 1440, 1800),
}

# OptionControlSP stores each scaled value to two decimal places. LaneTurnValue
# is always stored in mph; the metric menu changes the step, not the unit.
FLOAT_BOUNDS = {"LagdToggleDelay": (0.05, 0.50), "LaneTurnValue": (5.0, 20.0)}
TEXT_VALUES = {"LanguageSetting": frozenset({
    "main_en", "main_de", "main_fr", "main_pt-BR", "main_es", "main_tr",
    "main_ar", "main_th", "main_zh-CHT", "main_zh-CHS", "main_ko", "main_ja",
})}
OWNER_INT_KEYS = frozenset(INT_VALUES)
OWNER_FLOAT_KEYS = frozenset(FLOAT_BOUNDS)
OWNER_TEXT_KEYS = frozenset(TEXT_VALUES)
OWNER_KEYS = OWNER_BOOL_KEYS | OWNER_INT_KEYS | OWNER_FLOAT_KEYS | OWNER_TEXT_KEYS

# Qt checks key + "Lock" for standard toggles. Only this companion is a
# registered Params key on this build; Python Params rejects unregistered names.
OWNER_NATIVE_LOCKS = {"RecordFront": "RecordFrontLock"}

# The menu contains alternatives which alter protected disengagement, override
# or actuator behavior. These individual values preserve the protective state.
# DisengageOnAccelerator is an ordinary owner setting (Mark, 2026-11-22: "keep
# cruise and steering on with a gas pedal press"): off makes the pedal an
# override, the brake still disengages, and off is openpilot's own default.
# selfdrived reads it live but MADS reads it at startup, so it is applied in
# Park like every owner change and takes full effect at the next start.
PROTECTIVE_VALUES = {
    "Mads": (False, "stock_disengagement"),
    "MadsMainCruiseAllowed": (False, "independent_lateral_engagement"),
    "MadsUnifiedEngagementMode": (False, "independent_lateral_engagement"),
    "MadsSteeringMode": (2, "brake_must_disengage"),
    "QuietMode": (False, "disengagement_chime"),
    "HyundaiLongitudinalTuning": (0, "actuator_limits"),
}

_BLOCKED_GROUPS = {
    "access_or_identity": {
        "AdbEnabled", "GithubUsername", "SshEnabled", "SunnylinkEnabled", "PrimeType",
        "HasAcceptedTerms", "CompletedTrainingVersion",
    },
    "software_or_update": {
        "CarPlatformBundle", "DisableUpdates", "GitBranch", "GitCommit", "GitCommitDate",
        "GitDiff", "GitRemote", "IsDevelopmentBranch", "IsReleaseBranch", "IsTestedBranch",
        "ModelManager_ActiveBundle", "QuickBootToggle", "UpdaterTargetBranch",
    },
    "managed_selection": {"ModelManager_Favs", "OsmLocal", "OsmStateName", "OsmStateTitle"},
    "no_installed_menu_setting": {"RecordAudioFeedback"},
    "runtime_or_readout": {
        "AthenadPid", "AthenadUploadQueue", "CarBatteryCapacity", "ControlsReady",
        "CurrentBootlog", "FirmwareQueryDone", "GithubRunnerSufficientVoltage", "InstallDate",
        "IsEngaged", "IsOffroad", "IsOnroad", "IsRhdDetected", "LagdValueCache",
        "LastUpdateUptimeOnroad", "MapdVersion", "ModelRunnerTypeCache", "NetworkMetered",
        "OsmDownloadedDate", "OSMDownloadProgress", "SunnylinkdPid", "TermsVersion",
        "TrainingVersion", "UbloxAvailable", "UpdateAvailable", "UpdaterCurrentDescription",
        "UpdaterCurrentReleaseNotes", "UpdaterFetchAvailable", "UpdaterNewDescription",
        "UpdaterNewReleaseNotes", "UpdaterState", "UptimeOffroad", "UptimeOnroad", "Version",
    },
}
BLOCKED_REASONS = {key: reason for reason, keys in _BLOCKED_GROUPS.items() for key in keys}


def _require_key(key):
    if type(key) is not str:
        raise GuardError("invalid_setting_key")
    if key not in OWNER_KEYS:
        raise GuardError(f"key_not_allowed:{key}:{BLOCKED_REASONS.get(key, 'unknown_key')}")


def _validate_value(key, value):
    if key in OWNER_BOOL_KEYS:
        if type(value) is not bool:
            raise GuardError(f"bool_required:{key}")
    elif key in OWNER_INT_KEYS:
        if type(value) is not int:
            raise GuardError(f"int_required:{key}")
        if value not in INT_VALUES[key]:
            raise GuardError(f"outside_menu_values:{key}")
    elif key in OWNER_FLOAT_KEYS:
        lo, hi = FLOAT_BOUNDS[key]
        if type(value) is not float or not math.isfinite(value):
            raise GuardError(f"finite_float_required:{key}")
        if not lo <= value <= hi or round(value, 2) != value:
            raise GuardError(f"outside_menu_values:{key}")
    elif type(value) is not str or value not in TEXT_VALUES[key]:
        raise GuardError(f"outside_menu_values:{key}")

    restriction = PROTECTIVE_VALUES.get(key)
    if restriction and value != restriction[0]:
        raise GuardError(f"protected_setting:{key}:{restriction[1]}")


def validate_owner_change(change: dict) -> None:
    """Validate all entries in a proposed write; leave the signed change untouched."""
    if not isinstance(change, dict) or not change:
        raise GuardError("empty_change")
    for key, value in change.items():
        _require_key(key)
        _validate_value(key, value)


def parse_owner_value(key: str, raw: str):
    """Parse one CLI argument to the exact Python type used by the pinned Params API."""
    _require_key(key)
    if type(raw) is not str or len(raw) > 64:
        raise GuardError(f"invalid_setting_text:{key}")
    if key in OWNER_BOOL_KEYS:
        choices = {"true": True, "1": True, "false": False, "0": False}
        if raw.lower() not in choices:
            raise GuardError(f"bool_required:{key}")
        value = choices[raw.lower()]
    elif key in OWNER_INT_KEYS:
        if not re.fullmatch(r"[+-]?[0-9]+", raw):
            raise GuardError(f"int_required:{key}")
        value = int(raw)
    elif key in OWNER_FLOAT_KEYS:
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", raw):
            raise GuardError(f"finite_float_required:{key}")
        try:
            numeric = Decimal(raw)
            value = float(numeric)
            if not math.isfinite(value) or numeric != Decimal(str(value)):
                raise GuardError(f"outside_menu_values:{key}")
        except InvalidOperation as error:
            raise GuardError(f"invalid_setting_text:{key}") from error
    else:
        value = raw
    _validate_value(key, value)
    return value
