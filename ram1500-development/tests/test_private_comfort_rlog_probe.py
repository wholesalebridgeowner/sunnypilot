"""Synthetic, identifier-free tests of the private Railway comfort analysis entrypoint."""
from types import SimpleNamespace

import pytest

from private_comfort_rlog_probe import (
    PINNED_COMMIT, parse_segments, pinned_recorded_build, public_lane_summary,
    select_keys, summarize, require_comfort_segment_source_pins,
)

PREFIX = "aabbccdd"
ROUTE = PREFIX + "--0011223344"


def obj(seg, size=12345, name=None):
    return {"Key": f"logs/{name or ROUTE}/{seg}/rlog.zst", "Size": size}


def test_exact_route_and_requested_segments_only():
    got = select_keys(
        [obj(29), obj(30), obj(31), obj(45), obj(30, name="bbccddee--0011223344")],
        route_prefix=PREFIX, segments=(29, 30, 31))
    assert got == [obj(i)["Key"] for i in (29, 30, 31)]


def test_private_route_can_have_upstream_owner_prefix():
    owner_scoped = "owner-private|" + ROUTE
    got = select_keys([obj(30, name=owner_scoped)],
                      route_prefix=PREFIX, segments=(30,))
    assert got == [obj(30, name=owner_scoped)["Key"]]


@pytest.mark.parametrize("objects,segments", [
    ([obj(29)], (29, 30)),
    ([obj(29), obj(29)], (29,)),
    ([obj(29), obj(29, name=PREFIX + "--ffffffffee")], (29,)),
    ([obj(29, size=0)], (29,)),
    ([obj(29, size=30_000_001)], (29,)),
    ([obj(29, size=True)], (29,)),
    ([], (30,)),
])
def test_incomplete_ambiguous_or_invalid_objects_fail_closed(objects, segments):
    with pytest.raises(ValueError):
        select_keys(objects, route_prefix=PREFIX, segments=segments)


@pytest.mark.parametrize("value", [
    "", "29,", "29,29", "-1", "29,30,31,32,33", "1001", "29, 30",
    "1;print(secret)", "00x", "None",
])
def test_segment_selector_rejects_unbounded_or_ambiguous(value):
    with pytest.raises(ValueError):
        parse_segments(value)


def test_source_pin_requires_observed_commit_and_never_prints_dirty_detail():
    assert not pinned_recorded_build([])["recordedCommitMatchedPinned"]
    assert pinned_recorded_build([
        {"service": "initData", "gitCommit": PINNED_COMMIT, "dirty": True}
    ])["recordedCommitMatchedPinned"]
    assert not pinned_recorded_build([
        {"service": "initData", "gitCommit": PINNED_COMMIT},
        {"service": "initData", "gitCommit": "00000000"},
    ])["recordedCommitMatchedPinned"]


def test_aggregates_have_no_raw_records_or_identifiers():
    rows = [
        {"service": "initData", "gitCommit": PINNED_COMMIT,
         "params": {"privateKey": "CONFIDENTIAL_MARKER"}},
    ]
    for i in range(31):
        rows.append({
            "service": "carState", "t": i * 100_000_000, "valid": True,
            "canValid": True, "cruiseState.enabled": True, "gasPressed": False,
            "brakePressed": False, "gearShifter": "drive", "vEgo": 20.,
            "aEgo": i * 0.01, "segment": 30, "vin": "CONFIDENTIAL_MARKER",
            "gps": "CONFIDENTIAL_MARKER",
        })
    read = [SimpleNamespace(messages=9876, error=None, truncated=False)]
    output = summarize(rows, read)
    assert output["provenance"]["recordedCommitMatchedPinned"]
    assert output["quality"]["decodedMessages"] == 9876
    assert output["accMotionIntegrity"]["quality"]["qualifiedEpisodes"] == 1
    import json
    text = json.dumps(output)
    assert "CONFIDENTIAL_MARKER" not in text
    assert ROUTE not in text
    assert "samples" not in output
    assert "segment" not in output
    assert "vin" not in output
    assert "gps" not in output


def test_unknown_recorded_build_is_never_misreported_as_verified():
    out = summarize([], [])
    assert not out["provenance"]["recordedCommitMatchedPinned"]
    assert out["quality"]["selectedFiles"] == 0
    assert out["accMotionIntegrity"]["quality"]["qualifiedEpisodes"] == 0


def test_model_direction_strings_are_whitelisted_before_stdout():
    import json
    out = public_lane_summary({
        "modelSamples": 1,
        "phaseWindows": {"laneChangeStarting": {
            "counts": {"qualifiedWindows": 123},
            "qualifiedByDirection": {"SECRET_VIN_OR_LOCATION": 7, "left": 2},
        }},
    })
    assert "SECRET_VIN_OR_LOCATION" not in json.dumps(out)
    assert out["phaseWindows"]["laneChangeStarting"]["qualifiedByDirection"]["other"] == 7
    assert out["phaseWindows"]["laneChangeStarting"]["qualifiedByDirection"]["left"] == 2


def test_direction_censor_count_is_exported_without_direction_payload():
    import json
    out = public_lane_summary({
        "modelSamples": 4,
        "phaseWindows": {"laneChangeStarting": {
            "counts": {"censoredByDirectionChangeOrUnknown": 1},
            "qualifiedByDirection": {"PRIVATE_MARKER": 3},
        }},
    })
    starting = out["phaseWindows"]["laneChangeStarting"]
    assert starting["counts"]["censoredByDirectionChangeOrUnknown"] == 1
    assert starting["qualifiedByDirection"]["other"] == 3
    assert "PRIVATE_MARKER" not in json.dumps(out)

def test_following_validity_counts_only_emit_known_aggregate_reason_keys():
    import json
    secret="SECRET_RAW_LOCATION_OR_VIN"
    rows=[
        {"service": "carState", "t": 100_000_000, "segment": 30,
         "valid": False, "canValid": False, "vEgo": 20.,
         "gasPressed": False, "brakePressed": False,
         "cruiseState.enabled": True, "secret": secret},
        {"service": "radarState", "t": 120_000_000, "segment": 30,
         "valid": True, "leadOne.status": True, "leadOne.dRel": 35.,
         "leadOne.vRel": -2., "secret": secret},
        {"service": "carState", "t": 200_000_000, "segment": 30,
         "valid": True, "canValid": True, "vEgo": 20.,
         "gasPressed": False, "brakePressed": False,
         "cruiseState.enabled": True},
        {"service": "radarState", "t": 220_000_000, "segment": 30,
         "valid": None, "leadOne.status": True, "leadOne.dRel": 35.,
         "leadOne.vRel": -2., "secret": secret},
    ]
    out=summarize(rows, [])
    assert out["following"]["validityRejections"] == {
        "invalidCarStateOrCan": 1, "invalidRadarMessage": 1}
    assert secret not in json.dumps(out)
    assert set(out["following"]["validityRejections"]) == {
        "invalidCarStateOrCan", "invalidRadarMessage"}


# Synthetic, identifier-free tests for exact per-segment source provenance.
def _source_record(seg, commit=PINNED_COMMIT):
    return {"service": "initData", "segment": seg, "gitCommit": commit}


def _read_result(seg):
    return SimpleNamespace(segment=seg, messages=100, error=None, truncated=False)


def test_comfort_source_gate_accepts_full_pin_per_selected_segment():
    out = require_comfort_segment_source_pins(
        [_source_record(29), _source_record(30), _source_record(30)],
        [_read_result(29), _read_result(30)])
    assert out == {"sourceVerifiedSegments": 2, "sourceVerifiedHeaderCount": 3}


@pytest.mark.parametrize("bad_commit", [
    PINNED_COMMIT[:8], PINNED_COMMIT[:12], PINNED_COMMIT[:39],
    PINNED_COMMIT.upper(), PINNED_COMMIT + " ", None, "",
    PINNED_COMMIT[:-1] + "0",
])
def test_comfort_rejects_prefix_malformed_or_disagreeing_source(bad_commit):
    assert not pinned_recorded_build([_source_record(29, bad_commit)])[
        "recordedCommitMatchedPinned"]
    with pytest.raises(ValueError) as ex:
        require_comfort_segment_source_pins(
            [_source_record(29, PINNED_COMMIT), _source_record(30, bad_commit)],
            [_read_result(29), _read_result(30)])
    assert str(bad_commit) not in str(ex.value)
    assert "segment 30" not in str(ex.value)


def test_comfort_missing_header_in_one_segment_fails_even_with_valid_other():
    with pytest.raises(ValueError, match="missing recorded build metadata"):
        require_comfort_segment_source_pins(
            [_source_record(29)],
            [_read_result(29), _read_result(30)])


@pytest.mark.parametrize("files,records", [
    ([], []),
    ([_read_result(29), _read_result(29)], [_source_record(29)]),
    ([SimpleNamespace(segment=None)], [_source_record(29)]),
    ([SimpleNamespace(segment=True)], [_source_record(29)]),
    ([_read_result(29)], [_source_record(29), _source_record(30)]),
    ([_read_result(29)], [{"service": "initData", "segment": "29", "gitCommit": PINNED_COMMIT}]),
])
def test_comfort_rejects_missing_duplicate_or_unscoped_file_provenance(files, records):
    with pytest.raises(ValueError) as ex:
        require_comfort_segment_source_pins(records, files)
    assert "1f7233" not in str(ex.value)


def test_comfort_source_integrity_never_exports_private_record_fields():
    rows = [_source_record(29), _source_record(30)]
    rows[0]["gps"] = "SECRET_PRIVATE_GPS"
    rows[1]["vin"] = "SECRET_PRIVATE_VIN"
    import json
    output = require_comfort_segment_source_pins(
        rows, [_read_result(29), _read_result(30)])
    assert "SECRET_PRIVATE" not in json.dumps(output)
    assert set(output) == {"sourceVerifiedSegments", "sourceVerifiedHeaderCount"}
