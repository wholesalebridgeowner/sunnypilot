"""Synthetic, route-free regression cases for full source provenance."""
import pytest
from ramtools.recorded_source_integrity import require_exact_recorded_commits

PIN = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"


def test_accepts_one_or_multiple_identical_full_hashes():
    assert require_exact_recorded_commits([PIN], PIN) == 1
    assert require_exact_recorded_commits((PIN, PIN), PIN) == 2


@pytest.mark.parametrize("found", ["1", "1f7233cb", PIN[:12], PIN[:39], "", PIN + "-dirty", PIN.upper(), PIN + " ", PIN[:-1] + "0", None, 1])
def test_ambiguous_or_malformed_metadata_rejected_without_leak(found):
    with pytest.raises(ValueError) as exc:
        require_exact_recorded_commits([found], PIN)
    assert "1f7233" not in str(exc.value)
    assert "dirty" not in str(exc.value)


def test_multi_header_mismatch_never_hidden_by_valid_first():
    with pytest.raises(ValueError):
        require_exact_recorded_commits([PIN, PIN[:-1]+"0"], PIN)


def test_missing_or_invalid_expected_fails_closed():
    for commits, expected in (([], PIN), (None, PIN), ((), PIN), ([PIN], PIN[:12]), ([PIN], "x" * 40), ([PIN], None)):
        with pytest.raises(ValueError):
            require_exact_recorded_commits(commits, expected)


def test_does_not_emit_raw_source_metadata():
    # The only successful output is a count, suitable for a privacy-safe tally.
    assert require_exact_recorded_commits([PIN, PIN, PIN], PIN) == 3


def test_private_rlog_decoder_uses_exact_source_gate(monkeypatch):
    """Exercise the real private analyzer entry point without any actual logs."""
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace as S

    module_path = Path(__file__).resolve().parents[1] / "private_full_route_cruise_audit.py"
    spec = importlib.util.spec_from_file_location("ram_private_gate_fixture", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_log_bytes", lambda _: (b"synthetic", None))

    class Events:
        @staticmethod
        def read_multiple_bytes(_data):
            return [
                S(which=lambda: "initData", logMonoTime=0,
                  initData=S(gitCommit=Events.commit)),
                S(which=lambda: "carState", logMonoTime=100000000, valid=True,
                  carState=S(canValid=True, cruiseState=S(enabled=False, available=True),
                             vEgo=0.0, gearShifter="park", steeringRateDeg=0.0,
                             steeringPressed=False, steerFaultTemporary=False,
                             steerFaultPermanent=False))
            ]

    schema = S(Event=Events)
    Events.commit = PIN
    summary, steering, count, pins = module.safe_decode_segment("unused", schema)
    assert summary["carStateRows"] == 1
    assert steering["eligibleSamples"] == 0
    assert (count, pins) == (2, 1)

    for untrusted in (PIN[:8], PIN[:12], PIN[:-1] + "0"):
        Events.commit = untrusted
        with pytest.raises(ValueError, match="not an exact source match") as exc:
            module.safe_decode_segment("unused", schema)
        assert untrusted not in str(exc.value)
