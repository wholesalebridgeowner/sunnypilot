"""Fail-closed exact installed-build ACC source provenance, no execution of source.

Checks the three exact public upstream git blobs underlying the separately
decoded Ram camera-bus ACC_ACTIVE and available flag. No vehicle connection,
VIN, CAN command generation, GPS or private route data.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

COMMIT="1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
EXPECTED={
    "carstate.py":"0aa6d83fabca773186f3e913fb5192d7dd28cfea",
    "stellantis.dbc":"df913561de2fad009ba27288cd8d5ec00a36ca02",
    "ram_generator.py":"fa408f1315f34feffad23c4637c7df905b5f8d7c",
}
PARSER_STATEMENTS={
    "ramCameraSelection":'cp_cruise = cp_cam if self.CP.carFingerprint in RAM_CARS else cp',
    "rawACCActive":'ret.cruiseState.enabled = cp_cruise.vl["DAS_3"]["ACC_ACTIVE"] == 1',
    "rawACCAvailable":'ret.cruiseState.available = cp_cruise.vl["DAS_3"]["ACC_AVAILABLE"] == 1',
    "cameraParser":'Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2)',
}
DBCS=[
    'BO_ 500 DAS_3:',
    'SG_ ACC_ACTIVE : 21|1@0+',
    'SG_ ACC_AVAILABLE : 20|1@0+',
]
RAM_MAPPING='500: 153,'


def git_blob(data: bytes) -> str:
    if not isinstance(data,bytes):
        raise ValueError("git blob requires bytes")
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()


def verify(source_files, expected=EXPECTED):
    if set(source_files)!=set(EXPECTED) or set(expected)!=set(EXPECTED):
        raise ValueError("source manifest incomplete")
    for name,sha in expected.items():
        data=source_files[name]
        if git_blob(data)!=sha:
            raise ValueError("pinned public source blob drift")
    try:
        car=source_files["carstate.py"].decode("utf-8")
        dbc=source_files["stellantis.dbc"].decode("utf-8")
        gen=source_files["ram_generator.py"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("source is not UTF-8") from None
    if any(s not in car for s in PARSER_STATEMENTS.values()):
        raise ValueError("installed Ram CarState ACC field provenance missing")
    if any(s not in dbc for s in DBCS):
        raise ValueError("installed DAS_3 DBC layout changed")
    if RAM_MAPPING not in gen:
        raise ValueError("installed Chrysler RAM remap differs")
    return {
        "status":"exact_installed_public_source_verified",
        "installedCommit":COMMIT,
        "carstateBlobSha":EXPECTED["carstate.py"],
        "dbcBlobSha":EXPECTED["stellantis.dbc"],
        "generatorBlobSha":EXPECTED["ram_generator.py"],
        "cameraBus":2,
        "das3AddressHex":"0x99",
        "accActiveBit":"21|1@0+",
        "accAvailableBit":"20|1@0+",
        "noControlWrite":True,
    }


def verify_directory(path: Path):
    folder=Path(path)
    return verify({name:(folder/name).read_bytes() for name in EXPECTED})


def main():
    import argparse
    import json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory",type=Path)
    args=p.parse_args()
    print(json.dumps(verify_directory(args.directory),sort_keys=True))


if __name__=="__main__":
    main()
