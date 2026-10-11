import contextlib
import io
import json
import random
import sys
import unittest
from unittest.mock import patch

from scripts.diagnostics.x431_access_math import (
    GUARDS, SOURCE_SHA256, audit_source, main, transform_1c940, transform_1cd20,
    transform_mode2, transform_mode6, transform_bde_cod_w06, transform_gateway, transform_psm)


def apply_columns(columns, word):
    result = 0
    for bit, column in enumerate(columns):
        if word & (1 << bit):
            result ^= column
    return result


def powered_columns(columns, power):
    """Independent GF(2) matrix exponentiation, rather than the model's scalar loop."""
    result = [1 << bit for bit in range(len(columns))]
    while power:
        if power & 1:
            result = [apply_columns(columns, column) for column in result]
        columns = [apply_columns(columns, column) for column in columns]
        power >>= 1
    return result


LEFT_TAPS = (0, 4, 7, 12, 14, 18, 20, 25, 30)
RIGHT_TAPS = (0, 1, 2, 5, 6, 7, 11, 12, 13, 15)
LEFT = powered_columns([1 << (i + 1) if i < 31 else sum(1 << j for j in LEFT_TAPS)
                        for i in range(32)], 36)
RIGHT = powered_columns([((1 << (i - 1)) if i else 0) | (0x8000 if i in RIGHT_TAPS else 0)
                         for i in range(16)], 44)


class AccessIntegerMathTests(unittest.TestCase):
    def test_named_original_vectors_and_widths(self):
        for fn, vectors in [
            (transform_bde_cod_w06, [(0,0),(1,1),(0x12345678,0xedcba987),(0xdeadbeef,0x21524110),(0xffffffff,0)]),
            (transform_gateway, [(0,0xc07ae41b),(1,0x1726e6cd),(0x12345678,0xb66cb38b),(0xffffffff,0x73e4f5d9)]),
            (transform_psm, [(0,0),(1,1),(0x400,0x400),(0x1042,0x2104),(0x1234,4),(0xffff,0x8000)])]:
            for word, expected in vectors:
                with self.subTest(fn=fn.__name__,word=hex(word)):
                    self.assertEqual(fn(word),expected)

    def test_named_words_reject_wrong_width_and_implicit_types(self):
        for fn,bits in [(transform_bde_cod_w06,32),(transform_gateway,32),(transform_psm,16)]:
            for word in (None,True,False,-1,1<<bits,0.0,'0',b'\0',bytearray(4)):
                with self.subTest(fn=fn.__name__,word=word),self.assertRaises(ValueError):fn(word)

    def test_named_cli_reports_explicit_source_bytes_without_vehicle_qualification(self):
        for entry, value, expected in [('bde-cod-w06','12345678','edcba987'),
                                       ('gateway','12345678','b66cb38b'),('psm','ffff','8000')]:
            with patch('scripts.diagnostics.x431_access_math.audit_source',return_value=dict(**GUARDS)), \
                    patch('pathlib.Path.read_bytes',return_value=b'controlled source fixture'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--source','fixture','--entry',entry,'--window-hex',value]),0)
            result=json.loads(output.getvalue())
            self.assertEqual(result['outputBytesHex'],expected)
            self.assertEqual(result['inputWindowInterpretation'],'source-algorithm-big-endian-bytes')
            self.assertIsNone(result['localClockWords'])
            self.assertFalse(result['securityAccessEnabled'])
            self.assertFalse(result['vehicleVerified'])
            self.assertIsNone(result['writePayload'])

    def test_named_cli_rejects_wrong_byte_width_and_unused_clock(self):
        for args in [('--entry','psm','--window-hex','0000ffff'),
                     ('--entry','gateway','--window-hex','ffff'),
                     ('--entry','bde-cod-w06','--window-hex','12345678','--second','0')]:
            with patch('scripts.diagnostics.x431_access_math.audit_source',return_value=dict(**GUARDS)), \
                    patch('pathlib.Path.read_bytes',return_value=b'controlled source fixture'), \
                    contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                main(['--source','fixture',*args])
            self.assertEqual(error.exception.code,2)

    def test_scalar_signed_extension_and_zero_iteration_regressions(self):
        # Fixed original-register observations: zero iterations must keep Mode 2
        # upper bits; a nonzero loop sign-extends only that mode's rotated word.
        for word, a, b in [(0,0,0),(0xa0,0xffffa0a0,0xa0a0),
                           (0x10000,0x10000,0),(0xffff0000,0xffff0000,0)]:
            with self.subTest(word=hex(word)):
                self.assertEqual(transform_mode2(word),a)
                self.assertEqual(transform_mode6(word),b)

    def test_scalar_inputs_require_explicit_unsigned_register_word(self):
        for fn in (transform_mode2,transform_mode6):
            for word in (None,True,False,-1,0x100000000,0.0,'0',b'\0',bytearray(4)):
                with self.subTest(fn=fn.__name__,word=word),self.assertRaises(ValueError):
                    fn(word)

    def test_scalar_cli_reports_register_representation_and_keeps_execution_disabled(self):
        for entry, expected in [('mode2','ffffa0a0'),('mode6','0000a0a0')]:
            with patch('scripts.diagnostics.x431_access_math.audit_source',return_value=dict(**GUARDS)), \
                    patch('pathlib.Path.read_bytes',return_value=b'controlled source fixture'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--source','fixture','--entry',entry,'--window-hex','000000a0']),0)
            result=json.loads(output.getvalue())
            self.assertEqual(result['outputBytesHex'],expected)
            self.assertEqual(result['inputWindowInterpretation'],'unsigned-32-bit-register-representation')
            self.assertIsNone(result['localClockWords'])
            self.assertFalse(result['executionEnabled'])
            self.assertFalse(result['securityAccessEnabled'])
            self.assertIsNone(result['writePayload'])

    def test_left_matrix_basis_and_deterministic_windows(self):
        rng = random.Random(981982)
        values = [0, 0xffffffff, 0x80000000, *[1 << i for i in range(32)],
                  *[rng.getrandbits(32) for _ in range(256)]]
        for word in values:
            for parity in (0, 1):
                window = bytearray(rng.getrandbits(8) for _ in range(8))
                if parity:
                    word |= 0x01000000
                    window[::2] = word.to_bytes(4, 'big')
                else:
                    window[0] &= 0xfe
                    window[1::2] = word.to_bytes(4, 'big')
                self.assertEqual(transform_1c940(bytes(window)), apply_columns(LEFT, word).to_bytes(4, 'big'))

    def test_left_unselected_positions_do_not_enter_the_word(self):
        for a, b in [(bytes.fromhex('0011223344556677'), bytes.fromhex('fe11aa33bb55cc77')),
                     (bytes.fromhex('0111223344556677'), bytes.fromhex('01aa22bb44cc66dd'))]:
            self.assertEqual(transform_1c940(a), transform_1c940(b))

    def test_right_complete_16_bit_domain_against_matrix(self):
        for word in range(65536):
            expected = apply_columns(RIGHT, word)
            self.assertEqual(transform_1cd20(word.to_bytes(2, 'big'), second=0, minute=0, hour=0),
                             bytes((expected >> 8, expected & 0xe0)))

    def test_explicit_clock_residues_and_leap_second(self):
        for word in (0, 1, 0x8000, 0xffff, 0x9810):
            state = apply_columns(RIGHT, word)
            for second, minute, hour in [*( (v, 0, 0) for v in range(32)), (60, 59, 23), (59, 59, 23), (1, 17, 9)]:
                low = (hour * 3600 + minute * 60 + second) % 32
                self.assertEqual(transform_1cd20(word.to_bytes(2, 'big'), second=second, minute=minute, hour=hour),
                                 bytes((state >> 8, (state & 0xe0) + low)))

    def test_window_shape_and_mutability_rejected(self):
        for fn, size, kwargs in [(transform_1c940, 8, {}), (transform_1cd20, 2, dict(second=0, minute=0, hour=0))]:
            for value in (None, '', bytearray(size), memoryview(bytes(size)), bytes(size - 1), bytes(size + 1)):
                with self.subTest(fn=fn.__name__, value=type(value).__name__), self.assertRaises(ValueError):
                    fn(value, **kwargs)

    def test_missing_invalid_or_boolean_clock_words_rejected(self):
        for name, values in [('second', (None, True, -1, 61, 1.5)),
                             ('minute', (None, False, -1, 60, '0')), ('hour', (None, True, -1, 24, 0.0))]:
            for value in values:
                clock = dict(second=0, minute=0, hour=0); clock[name] = value
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    transform_1cd20(b'\x00\x00', **clock)

    def test_source_mismatch_never_gets_provenance(self):
        for source in (None, bytearray(), b'', b'\x7fELF', bytes(591520)):
            with self.assertRaises(ValueError):
                audit_source(source)

    def test_guard_metadata_never_promotes_reference_to_execution(self):
        self.assertTrue(GUARDS['integerModelOnly'])
        self.assertFalse(GUARDS['executionEnabled'])
        self.assertFalse(GUARDS['securityAccessEnabled'])
        self.assertFalse(GUARDS['vehicleVerified'])
        self.assertIsNone(GUARDS['writePayload'])
        self.assertEqual(len(SOURCE_SHA256), 64)

    def test_cli_reports_offline_results_after_source_gate(self):
        with patch('scripts.diagnostics.x431_access_math.audit_source', return_value=dict(**GUARDS)), \
             patch('pathlib.Path.read_bytes', return_value=b'controlled source fixture'), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--source', 'fixture', '--entry', '1cd20', '--window-hex', '1234',
                                   '--second', '7', '--minute', '8', '--hour', '9']), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['localClockWords'], dict(second=7, minute=8, hour=9))
        self.assertEqual(report['outputBytesHex'], transform_1cd20(b'\x12\x34', second=7, minute=8, hour=9).hex())
        self.assertFalse(report['executionEnabled'])

    def test_cli_rejects_ambiguous_hex_missing_clock_and_unused_clock(self):
        cases = [('--entry', '1c940', '--window-hex', '00 11223344556677'),
                 ('--entry', '1cd20', '--window-hex', '1234'),
                 ('--entry', '1c940', '--window-hex', '0011223344556677', '--second', '0'),
                 ('--entry', 'mode2', '--window-hex', 'a0'),
                 ('--entry', 'mode6', '--window-hex', '000000a0', '--hour', '0')]
        for args in cases:
            with patch('scripts.diagnostics.x431_access_math.audit_source', return_value=dict(**GUARDS)), \
                 patch('pathlib.Path.read_bytes', return_value=b'controlled source fixture'), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(['--source', 'fixture', *args])
            self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
