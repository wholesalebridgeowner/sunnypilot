import io
import json

import pytest
from ramtools.logio import PARAM_WHITELIST, parse_error_log, segment_of
from ramtools.schema import PINNED_SCHEMA_FILES, SchemaIntegrityError, fetch_pinned_schema, git_blob_sha1, verify_schema_dir


def test_segment_from_comma_paths():
  assert segment_of("/x/000003cc--27a46b2116--2/qlog.zst") == ("000003cc--27a46b2116", 2)
  assert segment_of(r"C:\logs\000003cc--27a46b2116--51\qlog.zst") == ("000003cc--27a46b2116", 51)
  assert segment_of("000003cb--26235ffa36--7--qlog.zst") == ("000003cb--26235ffa36", 7)
  assert segment_of("/x/qlog.zst") == (None, None)


def test_error_log_drops_device_context():
  line = json.dumps({"msg": {"event": "selfdrived.initialized", "dt": 6.01, "timeout": True, "canValid": True,
                             "invalid": ["liveCalibration"], "not_alive": [], "not_freq_ok": ["roadCameraState"]},
                     "ctx": {"dongle_id": "SECRET", "version": "0.10.1"}, "level": "ERROR", "filename": "selfdrived.py"})
  out = parse_error_log(line)
  assert out["event"] == "selfdrived.initialized" and out["timeout"] is True
  assert "SECRET" not in json.dumps(out) and "ctx" not in out
  assert parse_error_log("not json at all")["msg"] == "not json at all"


def test_param_whitelist_is_control_only():
  assert PARAM_WHITELIST.match("NeuralNetworkLateralControl")
  assert PARAM_WHITELIST.match("Mads")
  for private in ("DongleId", "GithubSshKeys", "ApiCache_Device", "NavDestination", "HardwareSerial", "CarVin"):
    assert not PARAM_WHITELIST.match(private)


def test_git_blob_sha1_matches_git():
  assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


class FakeResp(io.BytesIO):
  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False


def test_fetch_rejects_tampered_schema(tmp_path):
  with pytest.raises(SchemaIntegrityError):
    fetch_pinned_schema(tmp_path, opener=lambda url: FakeResp(b"struct Evil {}"))
  assert set(verify_schema_dir(tmp_path).values()) == {"missing"}


def test_pins_cover_every_import_of_log_capnp():
  assert set(PINNED_SCHEMA_FILES) == {"log.capnp", "car.capnp", "custom.capnp", "legacy.capnp", "include/c++.capnp"}
