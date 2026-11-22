"""Compile the HUD patch's actual speed-limit update without a Qt or device dependency."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1] / "ui" / "0001-rampilot-speed-limit-sign.patch"


class SpeedLimitSignTests(unittest.TestCase):
  def test_transport_field_and_value_must_be_valid(self):
    patch = PATCH.read_text(encoding="utf-8")
    def added_block(prefix):
      lines = iter(patch.splitlines())
      first = next((line for line in lines if line.startswith("+" + prefix)), None)
      if first is None:
        return ""
      block = [first[1:]]
      for line in lines:
        if not line.startswith("+"):
          break
        block.append(line[1:])
      return "\n".join(block)

    helper = added_block("static bool freshCarStateSP(")
    update = added_block("  // RamPilot: speed limit the truck reports")
    self.assertTrue(update)
    source = r"""
#include <cassert>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
uint64_t tick = 1'000'000'000;
uint64_t nanos_since_boot() { return tick; }
namespace cereal { struct Event { enum Which { INIT_DATA, CAR_STATE_S_P }; }; }
struct State {
  bool field_valid;
  float value;
  bool getSpeedLimitValid() const { return field_valid; }
  float getSpeedLimit() const { return value; }
};
struct Envelope {
  cereal::Event::Which tag = cereal::Event::INIT_DATA;
  State state{true, 70.f * 0.44704f};
  cereal::Event::Which which() const { return tag; }
  State getCarStateSP() const {
    if (tag != cereal::Event::CAR_STATE_S_P) throw std::logic_error("wrong Event union member");
    return state;
  }
};
struct SubMaster {
  bool online = false, envelope_valid = false;
  uint64_t receivedAt = 0, receivedFrame = 0;
  Envelope event;
  bool alive(const std::string &name) const { assert(name == "carStateSP"); return online; }
  bool valid(const std::string &name) const { assert(name == "carStateSP"); return envelope_valid; }
  uint64_t rcv_frame(const std::string &name) const { assert(name == "carStateSP"); return receivedFrame; }
  uint64_t rcv_time(const std::string &name) const { assert(name == "carStateSP"); return receivedAt; }
  const Envelope &operator[](const std::string &name) const { assert(name == "carStateSP"); return event; }
};
struct UIState { struct { uint64_t started_frame; } scene; const SubMaster *sm; };
""" + helper + r"""
int main() {
  constexpr float MS_TO_MPH = 1.f / 0.44704f, MS_TO_KPH = 3.6f;
  SubMaster sm;
  UIState s{{10}, &sm};
  bool speedLimitValid = false;
  float speedLimit = 0.f;
  auto update = [&](bool is_metric = false) {
""" + update + r"""
  };

  // The default native Event is initData until a message is received.
  update(); assert(!speedLimitValid);
  sm.online = sm.envelope_valid = true; sm.receivedAt = tick; sm.receivedFrame = 10;
  update(); assert(!speedLimitValid);  // even a received, valid envelope can be the wrong type
  sm.event.tag = cereal::Event::CAR_STATE_S_P;
  update(); assert(speedLimitValid && std::round(speedLimit) == 70);
  update(true); assert(speedLimitValid && std::round(speedLimit) == 113);
  sm.receivedFrame = 9; update(); assert(!speedLimitValid);
  sm.receivedFrame = 10; sm.receivedAt = tick + 1; update(); assert(!speedLimitValid);
  sm.receivedAt = tick;
  tick += 99'999'999; update(); assert(speedLimitValid);
  tick++; update(); assert(!speedLimitValid);  // simulated SubMaster::alive stays true on a stalled stream
  tick += 1'000'000'000; update(); assert(!speedLimitValid);
  sm.receivedAt = tick; update(); assert(speedLimitValid);
  sm.envelope_valid = false; update(); assert(!speedLimitValid);
  sm.online = false; sm.envelope_valid = true; update(); assert(!speedLimitValid);
  sm.online = true; sm.event.state.field_valid = false; update(); assert(!speedLimitValid);
  sm.event.state.field_valid = true;
  for (float value : {0.f, -1.f, std::numeric_limits<float>::infinity(), std::numeric_limits<float>::quiet_NaN()}) {
    sm.event.state.value = value; update();
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
