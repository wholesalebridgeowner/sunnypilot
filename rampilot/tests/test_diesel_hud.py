"""Run the diesel HUD update from the native patch with Qt-free message and text fixtures."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "ui" / "0002-rampilot-diesel-panel.patch"
FIELDS = ["FuelRemainingUsGal", "AvgFuelEconomyUsMpg", "InstantFuelEconomyUsMpg",
          "IdleFuelUsGalPerHour", "RangeMiles", "DefPercent"]


def additions():
  return "\n".join(line[1:] for line in PATCH.read_text(encoding="utf-8").splitlines()
                   if line.startswith("+") and not line.startswith("+++"))


class DieselHudTests(unittest.TestCase):
  def test_selection_formatting_stale_and_new_drive(self):
    source = additions()
    method = source[source.index("void HudRendererSP::updateDiesel("):source.index("void HudRendererSP::drawDiesel(")]
    getters = "\n".join(f"bool get{f}Valid() const {{return valid[{i}];}} "
                        f"float get{f}() const {{return value[{i}];}}" for i, f in enumerate(FIELDS))
    first = (ROOT / "ui" / "0001-rampilot-speed-limit-sign.patch").read_text(encoding="utf-8")
    shared = "\n".join(line[1:] for line in first.splitlines() if line.startswith("+") and not line.startswith("+++"))
    guard = re.search(r"static bool freshCarStateSP\(const UIState &s\) \{.*?\n}", shared, re.S)[0]
    cpp = r"""
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <string>
#include <utility>
#include <vector>
struct QString : std::string {
  using std::string::string;
  QString(const std::string &s) : std::string(s) {}
  static QString number(double v, char, int decimals) {
    char buf[64]; std::snprintf(buf, sizeof(buf), "%.*f", decimals, v); return buf;
  }
};
static uint64_t nowNs = 1000000000;
static uint64_t nanos_since_boot() {return nowNs;}
namespace cereal {struct Event {enum Which {INIT_DATA,CAR_STATE_S_P};};}
struct State {
  bool supported = true, regenValid = false, active = false;
  bool valid[6] = {};
  float value[6] = {12.5f, 24.2f, 26.1f, 0.31f, 300.f, 72.f};
  bool getDieselSupported() const {return supported;}
  bool getDpfRegenValid() const {return regenValid;}
  bool getDpfRegenActive() const {return active;}
  bool typed = true;
  cereal::Event::Which which() const {return typed ? cereal::Event::CAR_STATE_S_P : cereal::Event::INIT_DATA;}
  State getCarStateSP() const {assert(typed); return *this;}
""" + getters + r"""
};
struct SubMaster {
  State state;
  bool ok = true, online = true;
  uint64_t received = 10, receivedAt = nowNs;
  uint64_t rcv_time(const std::string &s) const {assert(s == "carStateSP"); return receivedAt;}
  bool valid(const std::string &s) const {assert(s == "carStateSP"); return ok;}
  bool alive(const std::string &s) const {assert(s == "carStateSP"); return online;}
  uint64_t rcv_frame(const std::string &s) const {assert(s == "carStateSP"); return received;}
  State operator[](const std::string &s) const {assert(s == "carStateSP"); return state;}
};
struct UIState { struct {uint64_t started_frame;} scene; SubMaster *sm; };
struct HudRendererSP {
  std::vector<std::pair<QString,QString>> dieselRows;
  bool is_metric = false;
  QString tr(const char *s) {return s;}
  void updateDiesel(const UIState &s, bool current);
};
""" + guard + "\n" + method + r"""
int main() {
  SubMaster sm;
  UIState s{{1}, &sm};
  HudRendererSP hud;
  auto &rows = hud.dieselRows;
  auto update = [&] {hud.updateDiesel(s, freshCarStateSP(s));};
  auto hidden = [&] {assert(rows.empty());};
  sm.state.typed=false; update(); assert(rows.empty());
  sm.state.typed=true; update(); hidden();
  sm.state.active=true; update(); hidden();
  sm.state.regenValid=true; update(); assert(rows[0].second=="Active");
  sm.state.active=false; update(); assert(rows[0].second=="Inactive");
  sm.state.regenValid=false;
  sm.state.valid[0]=true; update();
  assert(rows.size()==2 && rows[0].second=="Unknown" && rows[1].second=="12.5 US gal");
  sm.state.valid[0]=false; update(); hidden();
  for (bool &v : sm.state.valid) v=true;
  update();
  assert(rows.size()==7 && rows[1].second=="12.5 US gal" && rows[2].second=="24.2 MPG" &&
         rows[3].second=="26.1 MPG" && rows[4].second=="0.31 gal/h" &&
         rows[5].second=="300 mi" && rows[6].second=="72 %");
  hud.is_metric=true; update();
  assert(rows.size()==7 && rows[0].second=="Unknown" && rows[1].second=="47.3 L" &&
         rows[2].second=="9.7 L/100 km" && rows[3].second=="9.0 L/100 km" &&
         rows[4].second=="1.17 L/h" && rows[5].second=="483 km" && rows[6].second=="72 %");
  auto original = sm.state;
  sm.state = State{}; sm.state.valid[1]=true; sm.state.value[1]=0.f; update(); hidden();
  sm.state.value[1]=std::numeric_limits<float>::infinity(); update(); hidden();
  hud.is_metric=false; sm.state.value[1]=0.f; update();
  assert(rows.size()==2 && rows[1].second=="0.0 MPG");
  sm.state=original; update();
  for (int i=0; i<6; ++i) {
    sm.state.valid[i]=false; update(); assert(rows.size()==6); sm.state.valid[i]=true;
  }
  sm.state.value[0]=std::numeric_limits<float>::quiet_NaN();
  sm.state.value[1]=std::numeric_limits<float>::infinity();
  sm.state.value[2]=-1.f; update(); assert(rows.size()==4);
  // Simulation keeps alive true after the first sample even if publication stops.
  nowNs += 100000000; update(); hidden();
  sm.receivedAt=nowNs; update(); assert(rows.size()==4);
  sm.state.typed=false; update(); hidden();
  sm.state.typed=true; sm.ok=false; update(); hidden();
  sm.ok=true; sm.online=false; update(); hidden();
  s.scene.started_frame=11; update(); assert(rows.empty());
  sm.online=true; update(); assert(rows.empty());
  sm.received=11; sm.state.supported=false; update(); assert(rows.empty());
}
"""
    with tempfile.TemporaryDirectory() as tmp:
      file = Path(tmp) / "diesel.cc"
      binary = file.with_name("check")
      file.write_text(cpp, encoding="utf-8")
      subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", str(file), "-o", str(binary)], check=True)
      subprocess.run([str(binary)], cwd=tmp, check=True)

  def test_panel_bounds_and_paint_hooks(self):
    patch = PATCH.read_text(encoding="utf-8")
    source = additions()
    self.assertIn("  updateDiesel(s, currentCarStateSP);", source)
    self.assertIn("    drawDiesel(p, surface_rect);", source)
    sign_patch = (ROOT / "ui" / "0001-rampilot-speed-limit-sign.patch").read_text(encoding="utf-8")
    self.assertIn("if (!reversing)", sign_patch)
    self.assertIn("45 + 204 + 24, 172, 204", sign_patch)
    top = int(re.search(r"constexpr int top = (\d+)", source)[1])
    self.assertGreater(top, 45 + 204 + 24 + 204)
    self.assertIn(".contains(box)", source)
    self.assertIn("if (dieselRows.isEmpty()) return;", patch)


if __name__ == "__main__":
  unittest.main()
