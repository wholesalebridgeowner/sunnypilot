# November 18 — independent-replication gate for factory ParkSense CAN candidates

This is an **independent critical-review gate**, not a claim that the truck needs another parking test. It specifically addresses a risk in the parallel PR #1 observational comparison: the Nov 18 factory parking log contains **one** authoritative EPS `AUTO_PARK_HAS_CONTROL_2` activation lasting **14.6 seconds**, not multiple independent parking trials.

An observed 0xA9 payload-change fraction of ~0.744 during reverse auto-park (versus ~0.009 before) is **only a within-maneuver correlation**. A large frame count does not create independent replications. Gear/speed stratification removes some confounds, but not steering load, input requests, ECU state, selection effects, or algorithmic time-series autocorrelation.

New module `ramtools/park_replication.py` computes separate **contiguous state episodes** per candidate, with minimum number and duration of frames, and requires at least **two distinct OEM control activations and two independent baseline episodes in the same gear** before labeling an activity difference replicated. Temporal CAN gaps and Drive↔Reverse shifts cannot count as a new factory activation. The test suite covers both positive and negative cases. This module is an **offline gate** and deliberately does not decode the parking-controller payload, identify an ECU, bypass steering safety, or read a specific private route.

**Known limitation:** the module is only valid for determining true activation count if the caller includes the *complete, reliable, time-ordered factory control-state transition timeline*; relying on a candidate ID that disappeared while EPS changed state can miss activations. The module documents that limitation and will not assert an OEM CAN command based on apparent activity alone. Absolute rates from different vehicles and differing traffic sampling frequency are not directly comparable.

**Current evidence gate: NOT REPLICATED** for factory command inference. The first factory auto-park maneuver establishes important **hardware/EPS-mode proof** by direct DBC status but remains one activation; do not conflate it with an independently replicated candidate command. Further independent source documentation, verified bus topology and safe passive additional sessions may improve confidence later. Nothing authorizes a steering-control implementation.

The replication tool lives on branch `ram1500-pinned-comfort-20261117` / draft PR #2 to avoid colliding with Claude's evolving source-pinned PAM/EPS code in PR #1.
