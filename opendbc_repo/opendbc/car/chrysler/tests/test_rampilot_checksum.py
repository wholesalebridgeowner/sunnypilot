"""RamPilot backport of opendbc #3782: the table-driven Chrysler checksum equals the former bit loop.

Loads chrysler_checksum from chryslercan.py and the CRC table from crc.py by path, so it needs no installed
opendbc. The reference below is the installed build's original bit-by-bit loop."""
import ast
import importlib.util
import pathlib
import random
import unittest

CAR = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("rp_crc", CAR / "crc.py")
crc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crc)
_fn = next(n for n in ast.parse((CAR / "chrysler" / "chryslercan.py").read_text(encoding="utf-8")).body
           if isinstance(n, ast.FunctionDef) and n.name == "chrysler_checksum")
_ns = {"CRC8J1850": crc.CRC8J1850}
exec(compile(ast.Module(body=[_fn], type_ignores=[]), "chryslercan.py", "exec"), _ns)
chrysler_checksum = _ns["chrysler_checksum"]


def reference(d: bytes) -> int:
  checksum = 0xFF
  for j in range(len(d) - 1):
    curr = d[j]
    shift = 0x80
    for _ in range(8):
      bit_sum = curr & shift
      temp_chk = checksum & 0x80
      if bit_sum:
        bit_sum = 0x1C
        if temp_chk:
          bit_sum = 1
        checksum = (checksum << 1) & 0xFF
        temp_chk = checksum | 1
        bit_sum ^= temp_chk
      else:
        if temp_chk:
          bit_sum = 0x1D
        checksum = (checksum << 1) & 0xFF
        bit_sum ^= checksum
      checksum = bit_sum & 0xFF
      shift >>= 1
  return (~checksum) & 0xFF


class ChryslerChecksumTests(unittest.TestCase):
  def test_every_one_and_two_byte_payload(self):
    for a in range(256):
      d = bytes([a, 0])
      self.assertEqual(chrysler_checksum(0, None, d), reference(d))
      for b in range(256):
        d = bytes([a, b, 0])
        self.assertEqual(chrysler_checksum(0, None, d), reference(d), d.hex())

  def test_random_frames_up_to_eight_bytes(self):
    rng = random.Random(20261121)
    for _ in range(20000):
      d = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 9)))
      self.assertEqual(chrysler_checksum(0, None, d), reference(d), d.hex())

  def test_last_byte_is_not_part_of_the_sum(self):
    self.assertEqual(chrysler_checksum(0, None, bytes([1, 2, 3, 0])), chrysler_checksum(0, None, bytes([1, 2, 3, 255])))


if __name__ == "__main__":
  unittest.main()
