"""The settings in force on a drive, read and never written.

Every drive records the snapshot at its start and at its end, so drives can be
compared later and a silent change is noticed:

    lagdToggle         steering-delay learning switched on
    lagdToggleDelayS   the fixed software delay stored beside it (s)
    nnlc               the neural-network steering switch
    laneChangeTimer    how lane changes start: the raw setting, -1 to 5
    modelBundleId      which driving model is active: 12 hex, `default` or `unreadable`
    build              the first 12 characters of the software commit

A read that fails gives null (see device_params.py), never a default: the
report then says that the setting could not be read.
"""
from __future__ import annotations

import hashlib
import json
import re

from device_params import read_setting

KEYS = ("lagdToggle", "lagdToggleDelayS", "nnlc", "laneChangeTimer", "modelBundleId", "build")
# Read again once per summary window. The model bundle is compared at drive start and drive end only.
WATCHED = ("lagdToggle", "lagdToggleDelayS", "nnlc", "laneChangeTimer", "build")
BUNDLE_DEFAULT = "default"
BUNDLE_UNREADABLE = "unreadable"
# What makes a model bundle the same bundle. The stored object also holds fields that are not identity
# (a download status, for one), so the id is made from these alone.
_BUNDLE_IDENTITY = ("internalName", "generation", "ref", "runner", "is20hz")
_BUILD = re.compile(r"[0-9a-f]{12}")
_RAM_MODEL = "RAM1500"
_NO_MODEL = "MOCK"


def model_bundle_id(value) -> str:
    """Identity of the active model bundle.

    `value` is the setting as the typed reader gives it (the parsed object),
    or as a log header holds it (the JSON text). Empty means the default
    bundle; anything that is not a bundle object is `unreadable`.
    """
    if isinstance(value, (bytes, bytearray, str)):
        try:
            text = value if isinstance(value, str) else bytes(value).decode("utf-8")
            if not text.strip():
                return BUNDLE_DEFAULT
            value = json.loads(text)
        except (ValueError, RecursionError):
            return BUNDLE_UNREADABLE
    elif value is None or value == {}:
        return BUNDLE_DEFAULT
    if not isinstance(value, dict) or not any(key in value for key in _BUNDLE_IDENTITY):
        return BUNDLE_UNREADABLE
    try:
        identity = json.dumps({key: value.get(key) for key in _BUNDLE_IDENTITY}, sort_keys=True,
                              separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return BUNDLE_UNREADABLE
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _build(params):
    text = read_setting(params, "GitCommit", str)
    if text is None:
        return None
    text = text[:12].lower()
    return text if _BUILD.fullmatch(text) else None


def _bundle(params) -> str:
    try:
        value = params.get("ModelManager_ActiveBundle")
    except Exception:
        return BUNDLE_UNREADABLE
    return model_bundle_id(value)


def reread_watched(params, last: dict) -> dict:
    """A snapshot with the five watched settings read again and the model bundle id of `last`."""
    return {"lagdToggle": read_setting(params, "LagdToggle", bool),
            "lagdToggleDelayS": read_setting(params, "LagdToggleDelay", float),
            "nnlc": read_setting(params, "NeuralNetworkLateralControl", bool),
            "laneChangeTimer": read_setting(params, "AutoLaneChangeTimer", int),
            "modelBundleId": last.get("modelBundleId"),
            "build": _build(params)}


def read_settings(params) -> dict:
    """The full snapshot, model bundle included (drive start and drive end)."""
    return reread_watched(params, {"modelBundleId": _bundle(params)})


def changed_keys(old: dict, new: dict) -> list:
    """The snapshot keys whose value differs, in schema order. True and 1 are different values."""
    def differs(a, b):
        return type(a) is not type(b) or a != b
    return [key for key in KEYS if (key in old or key in new) and differs(old.get(key), new.get(key))]


def nnlc_model_token(name):
    """The neural-network model the build matched to the truck, as a token: `mock` when it matched none,
    `ram` for a Ram 1500 model, `other`, or None when the name could not be read."""
    if not isinstance(name, str):
        return None
    squeezed = name.upper().replace(" ", "").replace("_", "")
    if not squeezed:
        return None
    if squeezed == _NO_MODEL:
        return "mock"
    return "ram" if squeezed.startswith(_RAM_MODEL) else "other"
