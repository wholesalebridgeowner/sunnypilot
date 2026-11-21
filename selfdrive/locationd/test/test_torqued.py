from unittest.mock import Mock

import pytest
from cereal import car, log
from openpilot.selfdrive.locationd.torqued import TorqueEstimator


def test_delay_before_and_after_first_live_event(monkeypatch):
  params = Mock()
  params.get.side_effect = lambda key, **kwargs: 0.31 if key == "LagdValueCache" else None
  params.get_bool.return_value = True
  monkeypatch.setattr("openpilot.selfdrive.locationd.torqued.Params", lambda: params)
  est = TorqueEstimator(car.CarParams())
  est.handle_log(10.0, "carControl", car.CarControl())
  assert est.raw_points["carControl_t"][-1] == pytest.approx(10.31)

  est.handle_log(11.0, "liveDelay", log.LiveDelayData.new_message(lateralDelay=0.46))
  est.handle_log(12.0, "carControl", car.CarControl())
  assert est.raw_points["carControl_t"][-1] == pytest.approx(12.46)

  params.get_bool.return_value = False
  est.handle_log(13.0, "liveDelay", log.LiveDelayData.new_message(lateralDelay=0.71))
  assert est.lag == pytest.approx(0.31)


def test_cal_percent():
  est = TorqueEstimator(car.CarParams())
  msg = est.get_msg()
  assert msg.liveTorqueParameters.calPerc == 0

  for (low, high), min_pts in zip(est.filtered_points.buckets.keys(),
                                  est.filtered_points.buckets_min_points.values(), strict=True):
    for _ in range(int(min_pts)):
      est.filtered_points.add_point((low + high) / 2.0, 0.0)

  # enough bucket points, but not enough total points
  msg = est.get_msg()
  assert msg.liveTorqueParameters.calPerc == (len(est.filtered_points) / est.min_points_total * 100 + 100) / 2

  # add enough points to bucket with most capacity
  key = list(est.filtered_points.buckets)[0]
  for _ in range(est.min_points_total - len(est.filtered_points)):
    est.filtered_points.add_point((key[0] + key[1]) / 2.0, 0.0)

  msg = est.get_msg()
  assert msg.liveTorqueParameters.calPerc == 100
