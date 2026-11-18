"""Source-pinned event envelope fixtures. No private logs, payloads or device access."""
from types import SimpleNamespace

from ramtools.logio import event_to_record

class _Event:
    def __init__(self, which, payload):
        self._which = which
        self.valid = True
        self.logMonoTime = 123_000_000
        setattr(self, which, payload)

    def which(self):
        return self._which


def test_main_onroad_events_use_flat_list():
    e = _Event("onroadEvents", [SimpleNamespace(name="steerSaturated"),
                                SimpleNamespace(name="canError")])
    rec = event_to_record(e, 2)
    assert rec["names"] == ["steerSaturated", "canError"]
    assert rec["segment"] == 2


def test_sunnypilot_sp_events_use_struct_events_list():
    e = _Event("onroadEventsSP", SimpleNamespace(
        events=[SimpleNamespace(name="lkasEnable"),
                SimpleNamespace(name="silentLkasDisable")]))
    rec = event_to_record(e, 2)
    assert rec["names"] == ["lkasEnable", "silentLkasDisable"]
    assert rec["service"] == "onroadEventsSP"
    assert rec["t"] == 123_000_000


def test_empty_sp_struct_is_valid_and_not_a_truncation():
    e = _Event("onroadEventsSP", SimpleNamespace(events=[]))
    assert event_to_record(e, 0)["names"] == []
