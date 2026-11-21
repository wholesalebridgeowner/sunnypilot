"""Compile the HUD patch's actual speed-limit update without a Qt or device dependency."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1] / "ui" / "0001-rampilot-speed-limit-sign.patch"


class SpeedLimitSignTests(unittest.TestCase):
  def test_transport_field_and_value_must_be_valid(self):
    patch = PATCH.read_text(encoding="utf-8")
    additions = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
    update = re.search(r"  const auto car_state_sp = .*?\n  speedLimit = [^;]+;", additions, re.S)
    self.assertIsNotNone(update, "HUD patch must update sign validity and display units")
    source = r"""
#include <cassert>
#include <cmath>
#include <limits>
#include <string>
struct State {
  bool field_valid;
  float value;
  bool getSpeedLimitValid() const { return field_valid; }
  float getSpeedLimit() const { return value; }
  State getCarStateSP() const { return *this; }
};
struct SM {
  bool online, envelope_valid;
  State state;
  bool alive(const std::string &name) const { assert(name == "carStateSP"); return online; }
  bool valid(const std::string &name) const { assert(name == "carStateSP"); return envelope_valid; }
  State operator[](const std::string &name) const { assert(name == "carStateSP"); return state; }
};
int main() {
  constexpr float MS_TO_MPH = 1.f / 0.44704f, MS_TO_KPH = 3.6f;
  constexpr float mph70 = 70.f * 0.44704f;
  bool speedLimitValid = false;
  float speedLimit = 0.f;
  auto update = [&](const SM &sm, bool is_metric = false) {
""" + update[0] + r"""
  };
  update({true, true, {true, mph70}});
  assert(speedLimitValid && std::round(speedLimit) == 70);
  update({true, true, {true, mph70}}, true);
  assert(speedLimitValid && std::round(speedLimit) == 113);
  update({true, false, {true, mph70}});
  assert(!speedLimitValid);
  update({false, true, {true, mph70}});
  assert(!speedLimitValid);
  update({true, true, {false, mph70}});
  assert(!speedLimitValid);
  for (float value : {0.f, -1.f, std::numeric_limits<float>::infinity(), std::numeric_limits<float>::quiet_NaN()}) {
    update({true, true, {true, value}});
    assert(!speedLimitValid);
  }
}
"""
    with tempfile.TemporaryDirectory() as temp:
      cpp = Path(temp) / "sign.cc"
      binary = Path(temp) / "check"
      cpp.write_text(source, encoding="utf-8")
      subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", str(cpp), "-o", str(binary)], check=True)
      subprocess.run([str(binary)], cwd=temp, check=True)


if __name__ == "__main__":
  unittest.main()
