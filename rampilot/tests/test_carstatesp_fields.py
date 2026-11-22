"""RamPilot: carStateSP fields match between the Python struct and the message schema.

card converts structs.CarStateSP to the carStateSP message by field name, and the rebuilt screen reads the
message, so a name or type mismatch would silently drop the value."""
import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def struct_fields():
  tree = ast.parse((ROOT / "opendbc_repo" / "opendbc" / "car" / "structs.py").read_text(encoding="utf-8"))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CarStateSP")
  return {n.target.id: ast.unparse(n.annotation) for n in cls.body if isinstance(n, ast.AnnAssign)}


def schema_fields():
  text = (ROOT / "cereal" / "custom.capnp").read_text(encoding="utf-8")
  body = re.search(r"struct CarStateSP @0x[0-9a-f]+ \{(.*?)\n\}", text, re.S).group(1)
  return {m.group(1): (int(m.group(2)), m.group(3)) for m in re.finditer(r"(\w+) @(\d+) :(\w+);", body)}


class CarStateSPFieldTests(unittest.TestCase):
  def test_same_names_and_types(self):
    py, capnp = struct_fields(), schema_fields()
    self.assertEqual(set(py), set(capnp))
    kinds = {"float": "Float32", "bool": "Bool"}
    for name, typ in py.items():
      self.assertEqual(kinds[typ], capnp[name][1], name)

  def test_schema_ordinals_and_types(self):
    self.assertEqual(schema_fields(), {
      "speedLimit": (0, "Float32"), "speedLimitValid": (1, "Bool"), "dieselSupported": (2, "Bool"),
      "dpfRegenActive": (3, "Bool"), "dpfRegenValid": (4, "Bool"),
      "fuelRemainingUsGal": (5, "Float32"), "fuelRemainingUsGalValid": (6, "Bool"),
      "avgFuelEconomyUsMpg": (7, "Float32"), "avgFuelEconomyUsMpgValid": (8, "Bool"),
      "instantFuelEconomyUsMpg": (9, "Float32"), "instantFuelEconomyUsMpgValid": (10, "Bool"),
      "idleFuelUsGalPerHour": (11, "Float32"), "idleFuelUsGalPerHourValid": (12, "Bool"),
      "rangeMiles": (13, "Float32"), "rangeMilesValid": (14, "Bool"),
      "defPercent": (15, "Float32"), "defPercentValid": (16, "Bool"),
    })


if __name__ == "__main__":
  unittest.main()
