"""Public source blob pin and fail-closed RAM ACC parser audit tests."""
import pytest

from ramtools.acc_source_provenance import (
    EXPECTED, PARSER_STATEMENTS, DBCS, RAM_MAPPING, git_blob, verify,
)


def fake():
    return {
      "carstate.py":("\n".join(PARSER_STATEMENTS.values())+"\n").encode(),
      "stellantis.dbc":("\n".join(DBCS)+"\n").encode(),
      "ram_generator.py":("chrysler_to_ram = {500: 153,}\n"+RAM_MAPPING+"\n").encode(),
    }


def test_source_shape_and_hash_layout_positive():
    files=fake()
    hashes={name:git_blob(data) for name,data in files.items()}
    out=verify(files,expected=hashes)
    assert out["cameraBus"]==2
    assert out["das3AddressHex"]=="0x99"
    assert out["accActiveBit"]=="21|1@0+"
    assert out["noControlWrite"] is True
    assert EXPECTED["carstate.py"] != hashes["carstate.py"]


def test_changed_blob_fails_before_semantics():
    files=fake()
    hashes={name:git_blob(data) for name,data in files.items()}
    files["carstate.py"]+=b"# changed"
    with pytest.raises(ValueError,match="blob drift"):
        verify(files,expected=hashes)


def test_changes_in_carstate_acc_source_or_dbc_fail():
    for name,old,new in [
      ("carstate.py",b'ACC_ACTIVE',b'ACC_INACTIVE'),
      ("stellantis.dbc",b"21|1@0+",b"22|1@0+"),
      ("ram_generator.py",b"500: 153,",b"500: 154,"),
    ]:
      files=fake()
      files[name]=files[name].replace(old,new)
      hashes={k:git_blob(v) for k,v in files.items()}
      with pytest.raises(ValueError):
          verify(files,expected=hashes)


def test_missing_sources_rejected():
    files=fake()
    files.pop("ram_generator.py")
    with pytest.raises(ValueError):
        verify(files)


def test_nonbytes_input_rejected():
    with pytest.raises(ValueError):
        git_blob("not bytes")
