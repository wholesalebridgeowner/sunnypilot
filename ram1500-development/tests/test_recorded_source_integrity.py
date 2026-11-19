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
