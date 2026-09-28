#!/usr/bin/env python3
"""Tests for measurement_fields only. Optional decoded 9X1 via env / repo-relative path."""
from __future__ import annotations

import hashlib
import os
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
if not __package__:
    sys.path.insert(0, str(HERE))

if __package__:
    from .measurement_fields import parse_measurement_fields
else:
    from measurement_fields import parse_measurement_fields

EU5_A = bytes.fromhex("21100000004d5d00103f00009c4400f0")
EU5_OFF_A = 4036608
EU5_OFF_B = 4036624


def _x9_path() -> Path:
    env = os.environ.get("X431_9X1_DECODED")
    if env:
        return Path(env)
    return ROOT / ".local" / "x431-re" / "2026-09-27-decode" / "file-loader" / "decoded" / "9X1_ALLDATA.BIN.dec"


def _pack16(sid: int, pid: int, name: int, byte_off: int, bit_off: int, formula: int) -> bytes:
    return (
        bytes([sid])
        + struct.pack("<I", pid)
        + struct.pack("<I", name)
        + struct.pack("<H", byte_off)
        + bytes([bit_off])
        + struct.pack("<I", formula)
    )


def _pack19(sid: int, pid: int, name: int, byte_off: int, bit_off: int, formula: int) -> bytes:
    return (
        struct.pack("<I", sid)
        + struct.pack("<I", pid)
        + struct.pack("<I", name)
        + struct.pack("<H", byte_off)
        + bytes([bit_off])
        + struct.pack("<I", formula)
    )


class SyntheticSid21(unittest.TestCase):
    def test_sid21_pid16_eu5_hex(self):
        r = parse_measurement_fields(EU5_A, 0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["rawSID"], 0x21)
        self.assertEqual(r["wireSID"], 0x21)
        self.assertEqual(r["PID"], 16)
        self.assertEqual(r["byteOffset"], 63)
        self.assertEqual(r["bitOffset"], 0)
        self.assertEqual(r["nameID"], "10005D4D")
        self.assertEqual(r["formulaID"], "F000449C")
        cand = r["disabledreadrequestcandidate"]
        self.assertFalse(cand["executionEnabled"])
        self.assertFalse(cand["liveApproved"])
        self.assertEqual(cand["payload_hex"], "2110")

    def test_sid21_zero_pid(self):
        raw = _pack16(0x21, 0, 1, 0, 0, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["PID"], 0)
        self.assertEqual(r["disabledreadrequestcandidate"]["payload_hex"], "2100")

    def test_sid21_wide_pid_refused(self):
        raw = _pack16(0x21, 0x100, 1, 0, 0, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "sid21_wide_pid_unsupported")
        self.assertIsNone(r["disabledreadrequestcandidate"]["payload_hex"])
        self.assertEqual(r["PID"], 0x100)


class SyntheticSid22(unittest.TestCase):
    def test_sid22_two_byte_pid(self):
        raw = _pack16(0x22, 0xF197, 1, 4, 3, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["wireSID"], 0x22)
        self.assertEqual(r["byteOffset"], 4)
        self.assertEqual(r["bitOffset"], 3)
        self.assertEqual(r["disabledreadrequestcandidate"]["payload_hex"], "22F197")

    def test_sid22_overflow(self):
        raw = _pack16(0x22, 0x10000, 1, 0, 0, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "pid_overflow_u16")
        self.assertIsNone(r["disabledreadrequestcandidate"]["payload_hex"])


class SyntheticSid31Alias(unittest.TestCase):
    def test_alias_to_22_not_routine_31(self):
        raw = _pack16(0x31, 0x0010, 0x10005D4D, 0, 0, 0xF000449C)
        r = parse_measurement_fields(raw, 0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["rawSID"], 0x31)
        self.assertEqual(r["rawSID"], 49)
        self.assertEqual(r["wireSID"], 0x22)
        self.assertEqual(r["wireSID"], 34)
        cand = r["disabledreadrequestcandidate"]
        self.assertEqual(cand["payload_hex"], "220010")
        self.assertNotEqual(cand["payload_hex"][:2], "31")
        self.assertIn("not RoutineControl 31", cand["explanation"])

    def test_alias_overflow(self):
        raw = _pack16(0x31, 0x10000, 1, 0, 0, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertEqual(r["rawSID"], 0x31)
        self.assertEqual(r["wireSID"], 0x22)
        self.assertEqual(r["status"], "pid_overflow_u16")


class MalformedAndWidth(unittest.TestCase):
    def test_malformed_short(self):
        r = parse_measurement_fields(b"\x21", 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "format_mismatch")

    def test_format0_rejects_19(self):
        raw = _pack19(0x21, 16, 1, 63, 0, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "format_mismatch")
        self.assertIsNone(r["PID"])

    def test_format1_rejects_16(self):
        r = parse_measurement_fields(EU5_A, 1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "format_mismatch")
        self.assertIsNone(r["nameID"])

    def test_format1_19byte_native_layout(self):
        raw = _pack19(0x21, 16, 0x10005D4D, 63, 0, 0xF000449C)
        self.assertEqual(len(raw), 19)
        r = parse_measurement_fields(raw, 1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["sid_width"], 4)
        self.assertEqual(r["rawSID"], 0x21)
        self.assertEqual(r["PID"], 16)
        self.assertEqual(r["byteOffset"], 63)
        self.assertEqual(r["nameID"], "10005D4D")
        self.assertEqual(r["formulaID"], "F000449C")
        self.assertEqual(r["disabledreadrequestcandidate"]["payload_hex"], "2110")

    def test_unknown_format_flag(self):
        r = parse_measurement_fields(EU5_A, 9)
        self.assertEqual(r["status"], "unsupported_header_format")
        self.assertIsNone(r["PID"])

    def test_other_sid_unsupported(self):
        raw = _pack16(0x1A, 0x9F, 1, 0, 0, 2)
        r = parse_measurement_fields(raw, 0)
        self.assertEqual(r["status"], "unsupported_sid")
        self.assertEqual(r["rawSID"], 0x1A)
        self.assertIsNone(r["disabledreadrequestcandidate"]["payload_hex"])


@unittest.skipUnless(_x9_path().is_file(), "decoded 9X1 absent")
class RealEu5Rows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = _x9_path()
        cls.blob = cls.path.read_bytes()
        cls.sha256 = hashlib.sha256(cls.blob).hexdigest()

    def test_row_4036608(self):
        raw = self.blob[EU5_OFF_A : EU5_OFF_A + 16]
        self.assertEqual(raw, EU5_A)
        r = parse_measurement_fields(raw, 0)
        self.assertEqual(r["disabledreadrequestcandidate"]["payload_hex"], "2110")
        self.assertEqual(r["PID"], 16)
        self.assertEqual(r["byteOffset"], 63)
        self.assertEqual(r["bitOffset"], 0)
        self.assertEqual(r["nameID"], "10005D4D")
        self.assertEqual(r["formulaID"], "F000449C")

    def test_row_4036624(self):
        raw = self.blob[EU5_OFF_B : EU5_OFF_B + 16]
        r = parse_measurement_fields(raw, 0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["rawSID"], 0x21)
        self.assertEqual(r["PID"], 16)
        self.assertEqual(r["disabledreadrequestcandidate"]["payload_hex"], "2110")
        self.assertEqual(r["byteOffset"], 0)
        self.assertEqual(r["bitOffset"], 0)


if __name__ == "__main__":
    unittest.main()
