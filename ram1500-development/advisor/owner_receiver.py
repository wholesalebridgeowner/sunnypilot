"""Receive owner approvals and require fresh parked or ignition-off evidence for every write."""
from __future__ import annotations

import math

from approval import ApprovalError
from samples import get


class OwnerReceiver:
    def __init__(self, writer, control_clock_ns, board_clock_ns, ignition_socket):
        self.writer = writer
        self.control_clock_ns = control_clock_ns
        self.board_clock_ns = board_clock_ns
        self.ignition_socket = ignition_socket
        self.socket = None
        self.ignition_off_ns = None
        self.pending = {}
        self.next_check = 0.0

    @staticmethod
    def _fresh(stamp, clock):
        return isinstance(stamp, int) and 0 <= clock() - stamp <= 1_000_000_000

    def _close(self):
        if self.socket is not None:
            close = getattr(self.socket, "close", None)
            if close is not None:
                close()
        self.socket = None
        self.ignition_off_ns = None

    def _parked(self, agent):
        if not agent.listening or not agent.socks:
            return False
        rows = []
        for stream in ("carState", "carControl"):
            row = agent.latest_state.get(stream)
            if row is None or row[3] is not True or not self._fresh(row[2], self.control_clock_ns):
                return False
            rows.append(row[1])
        car, control = rows
        speed = get(car, "vEgo")
        return (get(car, "canValid") is True and str(get(car, "gearShifter")) == "park"
                and type(speed) in (float, int) and math.isfinite(speed) and abs(speed) < 0.1
                and all(get(control, flag) is False for flag in ("enabled", "latActive", "longActive")))

    def _off(self, agent):
        # Manager's offroad flag alone can also mean the driving software failed to start.
        if self.socket is None:
            self.socket = self.ignition_socket()
        if self.socket is None:
            return False
        raw = self.socket.receive(non_blocking=True)
        while raw is not None:
            self.ignition_off_ns = None
            stamp, boards, valid = agent.decode("pandaStates", raw)
            if (valid is True and self._fresh(stamp, self.board_clock_ns) and len(boards)
                    and all(str(get(board, "pandaType")) not in ("None", "unknown")
                            and get(board, "ignitionLine") is False and get(board, "ignitionCan") is False
                            for board in boards)):
                self.ignition_off_ns = stamp
            raw = self.socket.receive(non_blocking=True)
        return self._fresh(self.ignition_off_ns, self.board_clock_ns)

    def safe(self, agent):
        onroad = agent.params.stored("IsOnroad")
        offroad = agent.params.stored("IsOffroad")
        if onroad is True and offroad is False:
            return agent.onroad and self._parked(agent)
        if onroad is False and offroad is True:
            return not agent.onroad and self._off(agent)
        return False

    def tick(self, agent, now):
        if now < self.next_check:
            return
        self.next_check = now + 1.0
        if agent.onroad:
            self._close()
        agent.uplink.set_owner_poll(not agent.onroad or self._parked(agent))
        for approval in agent.uplink.owner_approvals():
            try:
                fields = self.writer.authenticate(approval)
            except ApprovalError:
                continue
            self.pending[fields["nonce"]] = approval
        if self.writer.pending.exists():
            self.writer.recover(lambda: self.safe(agent))
        for nonce, approval in list(self.pending.items()):
            result = self.writer.apply(approval, lambda: self.safe(agent), now=agent.wall_clock(),
                                       delay_probe=(agent.probe or {}).get("result"))
            if result != "refused:not_parked":
                del self.pending[nonce]
                agent._event("apply_result", {"suggestionId": approval.get("suggestion_id"), "status": result})
        if not self.pending and not self.writer.pending.exists():
            self._close()
