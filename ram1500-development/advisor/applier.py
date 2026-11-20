"""The only Drive Advisor component that writes settings.

Order of checks: approval signature/expiry/replay, allowlist and range,
parked state, one change at a time, live-trial limit. The approval is marked
used and the journal is written BEFORE any setting changes, so a crash at any
point can be undone and no approval can be replayed.
"""
from __future__ import annotations

from dataclasses import dataclass

from approval import ApprovalError, verify_approval
from journal import Journal, NonceLedger
from settings_guard import GuardError, validate_change

MAX_LIVE_TRIALS_PER_DRIVE = 2
PARKED_SPEED_MS = 0.1


@dataclass(frozen=True)
class VehicleState:
    in_park: bool
    speed_ms: float
    engaged: bool

    def parked(self) -> bool:
        return self.in_park and abs(self.speed_ms) < PARKED_SPEED_MS and not self.engaged


class Applier:
    def __init__(self, params, journal: Journal, nonces: NonceLedger, key: bytes):
        self.params = params
        self.journal = journal
        self.nonces = nonces
        self.key = key
        self.live_trials_this_drive = 0

    def apply(self, approval: dict, state: VehicleState, *, now: float,
              learned_delay_s: float, base_actuator_delay_s: float) -> str:
        try:
            fields = verify_approval(self.key, approval, now=now, used_nonces=self.nonces.used())
        except ApprovalError as error:
            return f"refused:{error}"
        change, mode = fields["change"], fields["mode"]
        try:
            validate_change(change, mode=mode, learned_delay_s=learned_delay_s,
                            base_actuator_delay_s=base_actuator_delay_s)
        except GuardError as error:
            return f"refused:{error}"
        if not state.parked():
            return "refused:not_parked"
        if self.journal.is_open():
            return "refused:trial_already_active"
        if mode == "live" and self.live_trials_this_drive >= MAX_LIVE_TRIALS_PER_DRIVE:
            return "refused:live_trial_limit"
        self.nonces.consume(fields["nonce"])
        if mode == "live":
            previous = {key: self.params.get(key) for key in change}
            self.journal.open_trial(suggestion_id=fields["suggestion_id"], previous=previous, written=change)
            self.live_trials_this_drive += 1
        for key, value in change.items():
            self.params.put(key, value)
        return "applied"

    def _owner_changed(self, entry: dict) -> list:
        return sorted(key for key, value in entry["written"].items() if self.params.get(key) != value)

    def check_owner_override(self) -> str | None:
        """Call regularly during a trial; ends the trial if the owner changed a setting."""
        entry = self.journal.load()
        if entry is None or not self._owner_changed(entry):
            return None
        self.journal.close()
        return "trial_ended_owner_changed"

    def restore(self, *, onroad: bool) -> str:
        """At drive end, or at agent start while not driving: put back what a trial replaced."""
        entry = self.journal.load()
        if entry is None:
            return "nothing_to_restore"
        if onroad:
            return "deferred_until_drive_end"
        changed = self._owner_changed(entry)
        if changed:
            self.journal.close()
            return "owner_changed:" + ",".join(changed)
        for key, value in entry["previous"].items():
            self.params.put(key, value)
        self.journal.close()
        return "restored"

    def new_drive(self) -> None:
        self.live_trials_this_drive = 0
