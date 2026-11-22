"""Count observed falling factory-cruise edges using the existing advisor streams."""
from collections import Counter, deque

from measure_api import Measure


class CruiseCutoffs(Measure):
    name = "cruiseCutoffs"
    owner_facing = True

    def __init__(self):
        self.history = deque()
        self.counts = Counter()
        self.observed = False

    def on_gap(self, ctx):
        self.history.clear()

    def on_sample(self, ctx, g):
        sample = g.sample
        edge = sample.cruise_observation
        if edge is None:
            self.history.clear()
            return
        enabled, available, gas, brake, host, button = edge
        if self.history and not 0 < sample.t - self.history[-1][0] <= .250000001:
            self.history.clear()
        previous = self.history[-1][1] if self.history else None
        self.observed |= previous is not None
        while self.history and sample.t - self.history[0][0] > 1.000000001:
            self.history.popleft()
        self.history.append((sample.t, enabled, gas, brake, host, button))
        if previous is not True or enabled:
            return
        gas = any(row[2] for row in self.history)
        brake = any(row[3] for row in self.history)
        if gas or brake:
            cause = "bothPedals" if gas and brake else "gas" if gas else "brake"
        elif any(row[4] is True for row in self.history):
            cause = "hostCancel"
        elif any(row[5] is True for row in self.history):
            cause = "button"
        elif available is False:
            cause = "unavailable"
        else:
            cause = "unresolved"
        self.counts[cause] += 1

    def window_fields(self):
        fields = {"cruiseCutoffs": dict(self.counts)} if self.observed else {}
        self.counts.clear()
        self.observed = False
        return fields
