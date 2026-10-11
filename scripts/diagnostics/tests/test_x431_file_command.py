import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics import x431_file_command as subject


class FileCommandTests(unittest.TestCase):
    def test_retained_family_commands_have_exact_internal_descriptors(self):
        # Literal source tables and independently specified internal layouts.
        cases = [
            ('100300', '01021003'), ('108900', '01021089'),
            ('19020108', '0103190208'), ('1902012C', '010319022C'),
            ('1902010C', '010319020C'), ('180002FF00', '01041800FF00'),
            ('180202FF00', '01041802FF00'), ('14FF02FFFF', '010414FFFFFF'),
            ('14FF0100', '010314FF00'), ('0400FF', '010104'),
        ]
        for table, expected in cases:
            with self.subTest(table=table):
                result = subject.convert_source_command(bytes.fromhex(table))
                self.assertEqual(result['nativeDescriptorHexCandidate'], expected)
                self.assertEqual(result['requestHexCandidate'], expected[4:])
                self.assertEqual(result['nativeLengthByte'], len(bytes.fromhex(expected)) - 2)
                self.assertFalse(result['lengthWrapped'])
                for key, value in subject.GUARDS.items():
                    self.assertEqual(result[key], value)

    def test_sentinel_ignores_second_byte_and_unobserved_tail(self):
        baseline = subject.convert_source_command(bytes.fromhex('0400FF'))
        for second in range(256):
            row = subject.convert_source_command(bytes((4, second, 255)) + b'\xa5' * 32)
            self.assertEqual(row['nativeDescriptorHexCandidate'], baseline['nativeDescriptorHexCandidate'])
            self.assertEqual(row['sourceBytesConsumed'], 3)
            self.assertEqual(row['trailingSourceBytesIgnored'], 32)

    def test_normal_zero_and_positive_lengths_ignore_only_declared_tail(self):
        for table in ['108900', '180002FF00', '19020108']:
            raw = bytes.fromhex(table)
            a = subject.convert_source_command(raw)
            b = subject.convert_source_command(raw + b'\xa5' * 32)
            self.assertEqual(a['nativeDescriptorHexCandidate'], b['nativeDescriptorHexCandidate'])
            self.assertEqual(b['sourceBytesConsumed'], len(raw))
            self.assertEqual(b['trailingSourceBytesIgnored'], 32)

    def test_uint8_length_wrap_is_preserved_without_a_wire_candidate(self):
        for length in (17, 253, 254):
            row = subject.convert_source_command(bytes((0x21, 0x42, length)) + bytes(range(length)))
            self.assertEqual(row['nativePayloadBytes'], length + 2)
            self.assertEqual(row['nativeLengthByte'], (length + 2) & 255)
            self.assertEqual(row['lengthWrapped'], length == 254)
            if length == 254:
                self.assertIsNone(row['requestHexCandidate'])
                self.assertEqual(row['status'], 'native-length-wrapped-unqualified')

    def test_truncated_window_never_fills_missing_bytes(self):
        for value in [None, bytearray(b'\x10\x89\0'), b'', b'\x10\x89', b'\0' * 4097]:
            with self.assertRaisesRegex(ValueError, 'window-invalid'):
                subject.convert_source_command(value)
        for length in (1, 2, 17, 253, 254):
            with self.assertRaisesRegex(ValueError, 'extent-incomplete'):
                subject.convert_source_command(bytes((0x18, 0, length)) + bytes(length - 1))

    def test_unknown_source_rejected_before_elf_parse(self):
        with patch('scripts.diagnostics.x431_native_binding._Elf') as parse:
            with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
                subject.audit_source(b'unknown')
        parse.assert_not_called()

    def test_cli_requires_source_and_emits_offline_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'file.so'
            p.write_bytes(b'explicit test source')
            with patch.object(subject, 'audit_source', return_value={'testOnly': True}), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(subject.main(['--source', str(p), '--table-hex', '0400FF']), 0)
            row = json.loads(out.getvalue())
            self.assertEqual(row['requestHexCandidate'], '04')
            self.assertEqual(row['provenance'], {'testOnly': True})
            self.assertFalse(row['executionEnabled'])
            self.assertIsNone(row['writePayload'])


if __name__ == '__main__':
    unittest.main()
