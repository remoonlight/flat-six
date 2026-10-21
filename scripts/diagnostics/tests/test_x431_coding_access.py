import struct
import unittest

from scripts.diagnostics.x431_coding_access import CodingAccessSource, _family_records, _match


def fixture(rows, *, declared=100, sentinel=True):
    blob = bytearray(40)
    struct.pack_into('<I', blob, 0x15, 25)
    blob[25] = 1
    struct.pack_into('<BI', blob, 26, 13, 39)
    blob[39] = declared
    for kind, flag, mode, selector, parameter in rows:
        blob += struct.pack('<BBI', kind, flag, mode)
        for value in (selector, parameter):
            raw = value.encode() + b'\0'
            blob += struct.pack('<H', len(raw)) + raw
    if sentinel:
        blob += b'\xff'
    return bytes(blob)


class CodingAccessTests(unittest.TestCase):
    def test_key_selection_requires_exact_source_parameter_and_width(self):
        # Synthetic selector rows exercise API boundaries; actual immutable
        # source and complete native dispatch are covered by the local audit.
        source = CodingAccessSource.__new__(CodingAccessSource)
        source._rows = _family_records(fixture([
            (0, 4, 0x03040100, 'Gateway', 'ASAM_seedkey_Gateway'),
            (0, 2, 0x01020150, 'PDK', 'ASAM_seedkey_PDK'),
            (0, 4, 0x03040100, 'WrongWidth', 'ASAM_seedkey_PDK'),
            (0, 4, 0x03040100, 'Unknown', 'Unknown_Algorithm'),
        ]), 13)
        self.assertEqual(source.key_for_seed('9x1:Gateway:V', bytes.fromhex('12345678')).hex(), 'b66cb38b')
        self.assertEqual(source.key_for_seed('9x1:PDK:V', bytes.fromhex('1234')).hex(), '3375')
        for seed in (None, 0x1234, b'\x12', bytes(4), bytearray(2)):
            with self.assertRaisesRegex(ValueError, 'seed-width-invalid'):
                source.key_for_seed('9x1:PDK:V', seed)
        for module in ('WrongWidth', 'Unknown'):
            with self.assertRaisesRegex(ValueError, 'algorithm-unqualified'):
                source.key_for_seed('9x1:' + module + ':V', bytes(4))
        with self.assertRaisesRegex(ValueError, 'definition-missing'):
            source.key_for_seed('9x1:Missing:V', bytes(4))
        self.assertFalse(source.resolve('9x1:Gateway:V')['executionEnabled'])

    def test_first_exact_match_wins_without_reordering_by_specificity(self):
        rows = _family_records(fixture([(0, 2, 3, 'M', 'module'),
            (1, 4, 5, 'V', 'variant')]), 13)
        self.assertEqual(_match(rows, 'M', 'V')['parameter'], 'module')
        rows = _family_records(fixture([(1, 4, 5, 'V', 'variant'),
            (0, 2, 3, 'M', 'module')]), 13)
        self.assertEqual(_match(rows, 'M', 'V')['parameter'], 'variant')

    def test_wrong_kind_case_and_partial_names_do_not_match(self):
        rows = _family_records(fixture([(2, 4, 9, 'V', 'ignored'),
            (0, 4, 1, 'M', 'one'), (1, 2, 2, 'V', 'two')]), 13)
        self.assertIsNone(_match(rows, 'm', 'v'))
        self.assertIsNone(_match(rows, 'MM', 'VV'))
        self.assertEqual(_match(rows, 'other', 'V')['modeWord'], 2)

    def test_declared_byte_is_not_loop_bound_and_empty_parameter_is_not_no_access(self):
        rows = _family_records(fixture([(0, 4, 0x03040100, 'M', ''),
            (1, 2, 0x01020150, 'V', 'named')], declared=0), 13)
        self.assertEqual(len(rows), 2)
        self.assertEqual(_match(rows, 'M', 'V')['modeWord'], 0x03040100)
        self.assertIsNone(_match(rows, 'unmatched', 'unmatched'))

    def test_missing_sentinel_or_truncated_strings_are_rejected(self):
        blob = fixture([(0, 2, 3, 'M', 'A')], sentinel=False)
        for candidate in (blob, blob[:-1], blob[:-5]):
            with self.assertRaises(ValueError):
                _family_records(candidate, 13)

    def test_family_pointer_and_duplicates_rejected(self):
        for offset in (0, 100000):
            blob = bytearray(fixture([]))
            struct.pack_into('<I', blob, 27, offset)
            with self.assertRaises(ValueError):
                _family_records(bytes(blob), 13)
        with self.assertRaises(ValueError):
            _family_records(fixture([]), 14)
        blob = bytearray(fixture([]))
        blob[25] = 2
        struct.pack_into('<BI', blob, 31, 13, 39)
        with self.assertRaisesRegex(ValueError, 'missing-or-ambiguous'):
            _family_records(bytes(blob), 13)

    def test_strings_cannot_be_unterminated_or_embed_other_names(self):
        blob = fixture([(0, 2, 3, 'MOD', 'KEY')])
        for bad in (b'MODX', b'M\0D\0', b'\xffOD\0'):
            with self.assertRaisesRegex(ValueError, 'string-invalid'):
                _family_records(blob.replace(b'MOD\0', bad), 13)

    def test_unqualified_file_never_creates_source(self):
        for blob in (fixture([]), b'\0' * 186584, None, bytearray(186584)):
            with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
                CodingAccessSource(blob)


if __name__ == '__main__':
    unittest.main()
