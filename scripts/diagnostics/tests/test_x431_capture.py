import unittest
from functools import reduce
from operator import xor

from scripts.diagnostics.x431_capture import extract_return, read_request, matches_display


def fix(b):
    b[4:6] = (len(b) - 7).to_bytes(2, "big")
    b[-1] = reduce(xor, b[2:-1], 0)
    return bytes(b)


def long_return(pdu, count=None):
    n = len(pdu)
    count = count if count is not None else 1 + (n - 6 + 6) // 7
    b = bytearray.fromhex("55aaf8f000000167010000") + bytes([count])
    b += bytes.fromhex("55aa0b08ef00") + bytes([0x10 | (n >> 8), n & 255]) + pdu + b"\x12\x34\x00"
    return fix(b)


class CaptureTest(unittest.TestCase):
    def test_numeric_comparison_requires_exact_value_and_declared_unit(self):
        value = {"ok": True, "numeric": True, "phys": "12.5", "unit": "W/m2"}
        self.assertTrue(matches_display(value, "12.50 W/m2"))
        self.assertFalse(matches_display(value, "25 W/m2"))
        self.assertFalse(matches_display(value, "12.5 W"))
        self.assertFalse(matches_display(value, "12.5（单位原文：克）"))
        self.assertTrue(matches_display({"ok": True, "numeric": True, "phys": "1", "unit": "克"}, "1（单位原文：克）"))
        identical = {"ok": True, "numeric": True, "formulaKind": "IDENTICAL", "value": 10}
        self.assertFalse(matches_display(identical, "10 s"))
        self.assertTrue(matches_display(identical, "10 s", "s"))
    def test_observed_single_and_checksum(self):
        b = bytes.fromhex("55aaf8f00016ac670100000155aa0b08fd000461012e84aaaaaa363bbd")
        self.assertEqual(extract_return(b)["pduHex"], "61012E84")
        self.assertEqual(extract_return(b)["uninterpretedTrailerHex"], "363b")
        self.assertIsNone(extract_return(b[:-1] + b"\x00"))

    def test_long_exact_length_and_count_not_physical_frames(self):
        for n in (8, 13, 16, 31, 52, 255):
            pdu = b"\x62\x06\x00" + bytes(n - 3)
            b = long_return(pdu)
            out = extract_return(b)
            self.assertEqual(out["pduHex"], pdu.hex().upper())
            self.assertEqual(out["shape"], "vci-reassembled-long")
            self.assertIsNone(extract_return(long_return(pdu, count=1)))
            damaged = bytearray(b); damaged[19] ^= 1
            self.assertIsNone(extract_return(fix(damaged)))
            self.assertIsNone(extract_return(fix(bytearray(b[:-2]))))

    def test_status_address_marker_and_unknown_pci_rejected(self):
        base = long_return(bytes.fromhex("620600") + bytes(28))
        for offset, value in ((9, 1), (17, 1), (12, 0), (18, 0x20)):
            b = bytearray(base); b[offset] = value
            self.assertIsNone(extract_return(fix(b)))

    def test_security_and_write_requests_never_extracted(self):
        b = bytearray.fromhex("55aaf0f80016012701640001ff020d610108fc00022101aaaaaaaa0000")
        self.assertEqual(len(b), 29)
        self.assertEqual(read_request(fix(b))["requestHex"], "2101")
        for sid in (0x27, 0x2E, 0x31, 0x10, 0x3E):
            b[21] = sid
            self.assertIsNone(read_request(fix(b)))


if __name__ == "__main__":
    unittest.main()
