"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params
from openpilot.sunnypilot.livedelay.helpers import get_lat_delay


class ModelStateBase:
  def __init__(self):
    self.lat_delay = get_lat_delay(Params())

  def update_lat_delay(self, params: Params, sm) -> None:
    sample = sm["liveDelay"].lateralDelay if sm.seen["liveDelay"] else None
    self.lat_delay = get_lat_delay(params, sample)
