import pytest
from conftest import DEV


def test_refuses_output_inside_repo(tmp_path):
  import investigate_route
  with pytest.raises(SystemExit):
    investigate_route.main(["--schema-dir", str(tmp_path), "--out", str(DEV / "reports" / "private"), "x.zst"])
  assert not (DEV / "reports" / "private").exists()


def test_refuses_unverified_schema(tmp_path):
  import investigate_route
  with pytest.raises(SystemExit):
    investigate_route.main(["--schema-dir", str(tmp_path / "empty"), "--out", str(tmp_path / "out"), "x.zst"])
