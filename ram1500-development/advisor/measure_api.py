"""What a measure plugs into the pipeline with.

A measure is one small object per drive. The pipeline builds a fresh one for
every drive, calls the hooks below in a fixed order, and asks for its fields
when a summary window closes. A hook that raises switches that one measure off
for the rest of the drive; the other measures carry on.

Order of work on each Sample (`g` is the Sample with its shared flags, see
gates.py):

  1. every window that ends at or before the Sample's time is closed
     (`window_fields` of every measure), and the next one opens;
  2. `on_stream` for each new raw message a measure asked for, then
     `on_sample`, then `on_model` for a new model message that is due;
  3. `on_evaluate` for each earlier Sample this Sample releases (6.0 s late).

Everything a hook books goes to the window that is open while it runs: the
window of the Sample that triggered the call (`ctx.w`). So a measure only keeps
sums for "the open window" and hands them over in `window_fields`. Nothing
else is reset at a window boundary: an episode that crosses one counts once.

At a gap Sample, step 3 comes first: every waiting Sample goes to
`on_evaluate`, then `on_gap` runs, and only then is the gap Sample itself
handed to `on_sample` as the first Sample of a new run.
"""
from __future__ import annotations


class Context:
    """What a hook may use besides its arguments."""
    __slots__ = ("w", "t", "out")

    def __init__(self):
        self.w = 0          # index of the open window
        self.t = 0.0        # time of the Sample that triggered this call
        self.out = []       # messages of this tick

    def emit(self, kind: str, data: dict) -> None:
        """Send an event record (a braking episode, a lane change, a limit event) now."""
        self.out.append((kind, data))


class Measure:
    name = ""               # block name: listed in agent.measures, health.failed and drive_end.failed
    owner_facing = False    # owner-facing measures run before the log-only ones
    streams = ()            # streams wanted raw through on_stream, for example ("liveDelay",)

    def on_stream(self, ctx: Context, stream: str, t: float, msg) -> None:
        """A new message of a stream named in `streams` (each message once, newest per tick)."""

    def on_sample(self, ctx: Context, g) -> None:
        """Every Sample, at once. Only the flags known at once are set on `g`."""

    def on_model(self, ctx: Context, m, g) -> None:
        """Every new ModelMsg, once, right after the first Sample at or after its time (`g`)."""

    def on_evaluate(self, ctx: Context, g) -> None:
        """A Sample at least 6.0 s old, with every flag final. Oldest first, each Sample once."""

    def on_gap(self, ctx: Context) -> None:
        """A gap Sample arrived and every waiting Sample has been evaluated.
        Release whatever this measure itself still holds back, then reset its run state."""

    def on_drive_end(self, ctx: Context) -> None:
        """The drive is over and every waiting Sample has been evaluated.
        Close or drop open episodes; the last window is closed right after."""

    def window_fields(self) -> dict:
        """Fields and blocks of the closing window; reset the per-window sums.
        Leave a block out when it has nothing to say."""
        return {}

    def drive_totals(self) -> dict:
        """Entries for `drive_end.totals`."""
        return {}

    def drive_end_fields(self) -> dict:
        """Other fields for `drive_end`."""
        return {}


# --- helpers for measures that send a block of sums ---------------------------

def closing_block(booked: dict):
    """The one block booked while the closing window was open, or None.

    `booked` is what a core hands over: {booking key: block}. A measure books
    under the index of the open window, so at most one key can be there when
    that window closes. More than one means the measure booked to a window it
    was not in; that is a fault, and raising it switches the measure off.
    """
    if not booked:
        return None
    if len(booked) > 1:
        raise RuntimeError("sums booked to more than one window")
    return next(iter(booked.values()))


def significant(value: float, digits: int = 5) -> float:
    """Rounded to so many significant digits (a sum of squares has no fixed scale)."""
    return float(f"{value:.{digits}g}") + 0.0
