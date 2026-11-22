# RamPilot screen build for comma three

The comma three runs the compiled Qt program `selfdrive/ui/ui`. The builder starts from sunnypilot source commit `737a6c4236e843034680c951005b38d15815363f` for the installed `1f7233cb` build. The `0001-rampilot-speed-limit-sign.patch` patch draws a speed-limit sign under MAX when `carStateSP.speedLimitValid` is set and the truck is outside Reverse. The sign uses the value in `carStateSP.speedLimit` (m/s).

## EcoDiesel readouts

The second patch draws a compact panel below the speed-limit sign for the recorded Ram DT EcoDiesel engine calibration. The panel appears when the recognized truck supplies at least one fresh, validated reading. If only numeric readings are validated, the regen row reads **Unknown**. A valid fresh reading and its independent calibration switch are required for each numeric row. The underlying units are US gallons, US MPG, gallons per hour at idle, miles of range and percent DEF. The panel omits soot load. A lost or invalid message clears the panel.

The flag in the archived drives is a candidate. The 2021 Ram 1500 owner's manual describes conditional cluster messages for regeneration in progress and completion on pages 98–99. A timestamped factory message or a documented independent PCM status can validate the interpretation. The cluster can stay silent during a routine drive, and a second similar CAN trace alone cannot establish the flag's meaning. [Ram owner's manual](https://vehicleinfo.mopar.com/assets/publications/en-us/Ram/2021/1500_DT/5633293_21_DT_OM_EN_USC_DIGITAL_E4.pdf)

## Building

With approval for a parked build and the intended RamPilot cereal schemas installed, run `bash build_ui_on_device.sh` as the comma user. The builder requires a readable `IsOnroad` containing the single byte `0` and rechecks it before compilation and staging. It needs Git, SCons, clang++, qmake for the installed Qt version, GNU utilities, flock, access to the pinned source and submodules, and at least 6,000 MiB free on the build filesystem.

| Variable | Default | Role |
| --- | --- | --- |
| `OP_DIR` | `/data/openpilot` | Source of the installed `cereal/*.capnp` files |
| `PARAMS_DIR` | `/data/params/d` | Contains the offroad flag |
| `WORK` | `/data/rampilot-ui-build` | Dedicated temporary build space |
| `STATE_DIR` | `/data/ram-diag/rampilot` | Destination for completed artifacts |

For an offline host fixture, point those variables to separate directories, set `SOURCE_URL` to a local Git source, and set `SOURCE_COMMIT` to its full lowercase 40-character commit ID. The fixture can run its own copy of this script beside its test patches.

`WORK` must be empty on first use. Its marker identifies the canonical directory for subsequent builds. Both writable directories must be disjoint from one another, the installed checkout and the parameter directory, including through symlinks or parent directories. Every build checks out the pinned commit and submodules in a fresh private directory and uses a private SCons cache. The script removes its temporary files on normal exit and handled failures. A power loss can leave a private temporary directory behind. A retry creates a fresh one.

## Artifact and manifest

A successful build prints both paths under `STATE_DIR/ui-build-<manifest SHA-256>/`. The directory contains the executable `ui-<first 12 characters of output SHA-256>` and `manifest.txt`. The builder publishes them together. It reuses an existing matching pair after checking both files. A failed build leaves previous completed artifacts available.

The manifest records the checked-out source commit, ordered patch filenames and hashes, installed schema filenames and hashes as copied into the build, the clang++ and Qt versions, and the full output SHA-256. Verify the manifest's hash against its directory name and the executable against `output_sha256` after copying both to the review machine. The digest and manifest identify the particular build for review.

For a separately approved installation, record the reviewed executable's SHA-256 independently, commit the executable as `selfdrive/ui/ui` with Git mode `100755`, and add its digest to `rampilot/ALLOWED_BINARIES`. The installer requires that independent SHA-256 via `--ui-sha256` and checks it against the candidate. See [the installation instructions](../INSTALL.md).
