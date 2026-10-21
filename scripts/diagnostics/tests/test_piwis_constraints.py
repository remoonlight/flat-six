"""Synthetic serialization edge cases; private original/native checks run separately."""
import hashlib
import struct
import unittest

from scripts.diagnostics.piwis_constraints import read_uint32_constraint


def interval(lower, upper):
    return b"\x01\xfb\x00\x0b" + struct.pack("<I", lower) + b"\x0b" + struct.pack("<I", upper) + b"\x11"


def fixture(lower=10, upper=1000, excluded=((25, 40), (800, 900))):
    data = b"\x03\x02" + interval(lower, upper) + b"\x01\x01\x02" + struct.pack("<I", len(excluded))
    for start, end in excluded:
        data += b"\x01\x02\x02" + interval(start, end) + b"\x03" + struct.pack("<4I", 1, 2, 3, 4) + b"\x00"
    return data + b"\x00#>\x00"


def read(data):
    return read_uint32_constraint(data, expected_sha256=hashlib.sha256(data).hexdigest())


class PiwisConstraintsTest(unittest.TestCase):
    def test_closed_limits_and_excluded_ranges(self):
        source = fixture()
        constraint = read(source)
        for value in range(1010):
            self.assertEqual(constraint.accepts(value), 10 <= value <= 1000 and not (
                25 <= value <= 40 or 800 <= value <= 900))
        self.assertEqual(constraint.excluded[0].description_id, 1)
        self.assertEqual(constraint.excluded[0].description_key_id, 2)
        self.assertEqual(constraint.excluded[0].short_label_id, 3)
        self.assertEqual(constraint.excluded[0].short_label_key_id, 4)
        self.assertEqual(constraint.source_sha256, hashlib.sha256(source).hexdigest())

    def test_full_uint32_domain_and_type_rejection(self):
        constraint = read(fixture(0, 0xffffffff, ()))
        for value in (0, 255, 65535, 0xffffffff):
            self.assertTrue(constraint.accepts(value))
        for value in (-1, 0x100000000, True, False, 25.0, "25", None):
            self.assertFalse(constraint.accepts(value))

    def test_overlap_and_outside_exclusions_do_not_expand_domain(self):
        constraint = read(fixture(10, 50, ((0, 20), (15, 30), (45, 100))))
        self.assertEqual([n for n in range(101) if constraint.accepts(n)], list(range(31, 45)))

    def test_every_truncated_prefix_and_trailing_bytes_rejected(self):
        data = fixture()
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                read(data[:length])
        with self.assertRaisesRegex(ValueError, "trailing-data"):
            read(data + b"\x00")

    def test_unknown_format_never_becomes_a_legal_range(self):
        data = fixture(excluded=((25, 40),))
        # Top class, interval presence/class/type, limits, collection class,
        # range object class, exclusion meaning, computed flags, stream end.
        for offset in (0, 2, 3, 5, 15, 16, 17, 23, 24, 26, 29, 39, 40, 57, 58, 59):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                read(data[:offset] + bytes([data[offset] ^ 0xff]) + data[offset+1:])
        with self.assertRaisesRegex(ValueError, "range-count-invalid"):
            read(data[:19] + struct.pack("<I", 257) + data[23:])
        with self.assertRaisesRegex(ValueError, "interval-invalid"):
            read(fixture(10, 9))
        with self.assertRaisesRegex(ValueError, "interval-invalid"):
            read(fixture(excluded=((40, 25),)))

    def test_fingerprint_and_input_extent_required(self):
        data = fixture()
        for expected in (None, "", "0" * 64, hashlib.sha256(data).hexdigest().upper()):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                read_uint32_constraint(data, expected_sha256=expected)
        for invalid in (None, bytearray(data), "data", b"", b"x" * 16385):
            with self.subTest(input_type=type(invalid)), self.assertRaises(ValueError):
                read_uint32_constraint(invalid, expected_sha256="0" * 64)


if __name__ == "__main__":
    unittest.main()
