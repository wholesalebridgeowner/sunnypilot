# Claude Review Handoff — Ram 1500 Sunnypilot Project

You have access to the GitHub repository `wholesalebridgeowner/sunnypilot`. **Check out or explicitly use the branch `ram1500-custom-development`**, not master, for this review.

Read these files in order:
1. `ram1500-development/PROJECT_PLAN.md` — authoritative project goals, observations and acceptance criteria.
2. `ram1500-development/README.md` — existing development status and context.
3. `ram1500-development/analyze_route.py` — current log analysis tool.
4. `selfdrive/controls/lib/latcontrol_torque.py` and the Ram platform controller/interface code wherever actually located. Inspect any dependency/submodule needed for the full stack.
5. Relevant cereal schemas / decoder and safety models.

I want an **independent engineering review**, not an agreement with prior interpretations. Saturday November 14's ~50-minute evening route `000003cc--27a46b2116` has 51 Q-log segments and 728,719 decoded messages. A single `steerSaturated` alert sample occurred around +171 seconds; two `canError` samples and `controlsMismatch` samples occurred near the route end (~3002 seconds). `steerOverride` and `gasPressedOverride` counts are *samples*, not separate incidents. Full R-logs were not confirmed available at last check.

Please:
- Audit the existing script for correct schema usage, times, event counting and robust handling of missing fields.
- Develop a read-only reproducible per-sample steering timeline around +171 seconds using recorded `carState`, `carControl`, `carOutput`, `controlsState`, and torque parameters. Verify field names and units rather than guessing. Do not invent numeric torque readings when data aren't available.
- Determine whether observed limitations appear to be in the steering controller, car interface, EPS/OEM limits, safety gate, or telemetry interpretation; clearly state what cannot yet be distinguished.
- Investigate the end-of-drive faults with timestamps and context. Avoid diagnosing a dangerous CAN fault from events recorded during parking or shutdown without evidence.
- Audit the Ram vehicle interface and factory constraints. Preserve safety protections. No changes to Panda limits, OEM CAN authorization checks, or bypass of safety interlocks.
- Propose and (where reproducible) implement **offline-only analysis improvements** in a feature branch / PR, add tests, and write a report under `ram1500-development/reports/`. Do not publish a deployable driving build, install URL, or vehicle actuation changes without explicit approval and validation.

Deliver a concise engineering report with evidence, proposed root causes ranked by confidence, specific source references, unanswered questions, and the next three testable steps. If Railway/Comma private logs are not accessible through your tools, inspect the repository and prepare the decoder/tests for when the log data are supplied; don't claim to have read unavailable data. Ask for access only if truly necessary.

The long-term ambition is a reliable, smooth Ram-specific driver-assistance system with better curves and lane keeping, eventual low-speed and navigation capability where the platform actually permits it. Treat those as gated research milestones, not promised autonomous driving features.
