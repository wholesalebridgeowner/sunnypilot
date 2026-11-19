# Ram 1500 (5th gen) research on sunnypilot

An open research project on how [sunnypilot](https://github.com/sunnypilot/sunnypilot), a fork of comma.ai's
[openpilot](https://github.com/commaai/openpilot), drives a 5th-generation Ram 1500 (DT, 2019 onward). It works
from one owner's recorded drives and the public source code.

> [!WARNING]
> **Research only. There is nothing here to install on your truck.**
> No driving code has been changed, no safety limit has been raised, and there is no build or install link.
> The tools here read recorded logs and public source code. None of them sends commands to a vehicle.

To run sunnypilot on your Ram, use the official project: [docs.sunnypilot.ai](https://docs.sunnypilot.ai/) and the
[sunnypilot community forum](https://community.sunnypilot.ai/). Upstream's unmodified code is on this fork's
[`master`](https://github.com/wholesalebridgeowner/sunnypilot/tree/master) branch.

## What this project looks at

- **Steering strength:** why sunnypilot sometimes runs out of steering in curves.
- **Following distance and throttle:** how smoothly the truck follows traffic.
- **Lane changes:** how lane changes start and finish.
- **Low-speed steering:** why steering stops below about 32 mph, and whether some trucks can steer slower.
- **Factory self-parking:** how the truck's own parallel-park assist turns the wheel when nearly stopped.

## Findings so far

These come from one truck with a Comma 3 running sunnypilot 0.10.1. Results on your truck can differ.

1. **Following distance and throttle belong to the truck's own cruise control.** On this Ram, sunnypilot steers and
   sends cruise cancel and resume button presses. It doesn't press the gas or brake; the factory adaptive cruise
   control does all of that. Following-distance and throttle smoothness are mostly factory cruise behavior, not
   sunnypilot tuning.
   ([report](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/2026-11-14-following-throttle-lane-change.md),
   [installed-build check](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/drive-build-acc-lanechange-crossreview.md))

2. **The 32 mph steering cutoff is a software default, not a proven hardware limit.** sunnypilot's Ram 1500
   settings start with a minimum steering speed of about 32 mph. The same source code sets that minimum to zero for
   certain power-steering (EPS) firmware versions, listed in the report. That is what the code says, not a result
   tested on a truck, and whether a given truck qualifies depends on its exact EPS firmware.
   ([report](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/ram-dt-zero-speed-evidence-2026-11-17.md))

3. **In tight curves, sunnypilot asks for full steering but the truck gets less.** In one tight stretch of a
   recorded drive, the steering controller asked for its full effort in 219 of 700 samples. The steering command
   actually sent to the truck never went above 280, against a software ceiling of 350. Something between the
   request and the sent command holds it back; which limit that is hasn't been identified yet.
   ([report](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/2026-11-18-private-rlog-torque-cap-separation.md))

4. **The factory self-parking steers the wheel at a standstill.** During a factory parallel-park maneuver, the
   power-steering module reported "factory auto-park has control" for about 14.6 seconds. In one five-second stretch
   below 0.7 mph, the steering wheel swept through about 354°. sunnypilot wasn't steering at the time. Ram service
   information describes the park-assist module asking the power-steering module for steering torque. This shows the
   steering hardware turns the wheel at a standstill under the factory's own control. It doesn't show that
   sunnypilot is allowed to.
   ([recorded-drive report](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/2026-11-18-factory-parking-rlog-findings.md),
   [source and service-information check](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/2026-11-18-pam-eps-source-dbc-crosscheck.md))

5. **Lane-change timing comes from planner settings.** In the installed build, lane changes need at least 20 mph,
   and lane-line guidance fades out over 0.5 seconds at the start and back in over about 1 second at the end. These
   are design settings, not measured comfort. Measuring real lane changes from recorded drives is in progress.
   ([installed-build check](https://github.com/wholesalebridgeowner/sunnypilot/blob/ram1500-steering-forensics-20261117/ram1500-development/reports/drive-build-acc-lanechange-crossreview.md))

## Still unknown

- Which limit holds the steering command below its maximum in tight curves.
- Whether this truck's EPS firmware is one of the low-speed versions.
- Whether the truck permits a third-party system to steer at low speed at all. Nobody here is trying to get around
  the truck's protections to find out.

## Where the work is

- [`ram1500-development/`](../ram1500-development/) on this branch: the
  [project plan](../ram1500-development/PROJECT_PLAN.md) and original notes.
- [Draft PR #1](https://github.com/wholesalebridgeowner/sunnypilot/pull/1): the main toolkit. It holds the log
  analyzers, a read-only CAN logger for a parked Comma, the reports above, and an offline test suite that runs in
  GitHub Actions.
- [Draft PRs #2 onward](https://github.com/wholesalebridgeowner/sunnypilot/pulls): follow-on analysis, stacked one on
  top of the next, each tightening the checks on the evidence.

Most of the code and analysis is written by AI coding assistants (OpenAI Codex and Anthropic Claude) that review
each other's work. Tests and recorded data decide what stays.

## Privacy

Private drive logs are analyzed in a private environment. Only anonymous totals and counts are published here: no
VINs, GPS or locations, device IDs, raw CAN data or log files.

## Ram owners: compare notes

Comment on [PR #1](https://github.com/wholesalebridgeowner/sunnypilot/pull/1) with your model year, whether your
truck has factory self-parking, and your power-steering (EPS) firmware part number if you know it. Please don't post
your VIN, location or log files.

## Safety

Don't raise steering limits, spoof firmware or inject CAN messages based on anything here. Nothing in this
repository is validated for use on public roads.

## Credits

Built on [sunnypilot](https://github.com/sunnypilot/sunnypilot) and [openpilot](https://github.com/commaai/openpilot),
both MIT-licensed. This fork keeps upstream's [license](../LICENSE).
