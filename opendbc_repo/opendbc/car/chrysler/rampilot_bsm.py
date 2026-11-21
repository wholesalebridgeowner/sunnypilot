"""RamPilot: catch a blind-spot module that starts sending after the startup fingerprint.

The Chrysler interface turns blind-spot support on only when BSM_1 (address 720, bus 0) is in the
startup fingerprint, which listens for about one second. On the Ram 1500 BSM_1 arrives every 0.5 s,
and after some starts its first frame lands just after that window closes (one recorded start missed
it by 0.31 s). Blind-spot support then stays off for the whole drive while the truck's own system
works.

When BSM_1 is missing from the fingerprint, get_car() calls wait_for_late_bsm() before CarParams are
built. It keeps reading CAN for up to LATE_BSM_WAIT_S and records BSM_1 if a frame arrives. Once
enabled, the stock parser checks apply unchanged: if BSM_1 later stops, CAN validity fails as it does
today. If no frame arrives, blind-spot support stays off as today and the outcome is logged.
"""
import time

BSM_ADDR = 720
BSM_BUS = 0
LATE_BSM_WAIT_S = 3.0   # six BSM_1 periods on RAM_1500_5TH_GEN


def wait_for_late_bsm(can_recv, fingerprints: dict, wait_s: float = LATE_BSM_WAIT_S, monotonic=time.monotonic) -> float | None:
  """Add BSM_1 to the bus-0 fingerprint if it appears within wait_s.

  Returns 0.0 when it was already present (nothing is read), the seconds waited when it was added,
  or None when no BSM_1 frame arrived in time (the fingerprint is left unchanged)."""
  if BSM_ADDR in fingerprints.get(BSM_BUS, {}):
    return 0.0
  start = monotonic()
  while monotonic() - start < wait_s:
    for packet in can_recv(wait_for_one=True):
      for msg in packet:
        if msg.src == BSM_BUS and msg.address == BSM_ADDR:
          fingerprints.setdefault(BSM_BUS, {})[BSM_ADDR] = len(msg.dat)
          return monotonic() - start
  return None
