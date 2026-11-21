# RamPilot screen changes (comma three)

The comma three's screen is the compiled Qt program `selfdrive/ui/ui`. RamPilot changes it from sunnypilot's own source for the installed build (commit `737a6c42`), so the rebuilt program matches the rest of the installed software.

- `0001-rampilot-speed-limit-sign.patch` draws a speed-limit sign under the MAX box. In mph it is the US rectangular sign; in km/h it is the round red-ring sign. It shows only when `carStateSP.speedLimitValid` is set and the truck is not reversing. The value comes from `carStateSP.speedLimit` (m/s), which card fills from the truck's own speed-limit message once its decode is added in the Chrysler car code.
- `build_ui_on_device.sh` builds the program on the comma itself, with the truck parked and offroad, after RamPilot is installed. It clones sunnypilot at `737a6c42`, applies these patches, copies the installed RamPilot schema, and runs `scons selfdrive/ui/ui`. It stages the result in `/data/ram-diag/rampilot/ui-<sha12>` and does not touch `/data/openpilot`.

The staged program is copied to the laptop and committed as `selfdrive/ui/ui`, with its SHA-256 in `rampilot/ALLOWED_BINARIES`. `rampilot/device.sh` accepts that one compiled file only when its SHA-256 matches the pin, and rollback restores the stock program.
