"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params


def get_lat_delay(params: Params, stock_lat_delay: float) -> float:
  # RamPilot (sunnypilot #1906, 53e13a7b): live learning on uses what lagd publishes; off uses the fixed
  # steerActuatorDelay + software delay that LagdToggle caches. The branches were swapped, so the fixed
  # setting had no effect on controlsd.
  if params.get_bool("LagdToggle"):
    return stock_lat_delay

  return float(params.get("LagdValueCache", return_default=True))
