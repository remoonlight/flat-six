import collections
import struct
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from scripts.diagnostics import x431_calc_input_info_reference as caller
from scripts.diagnostics import x431_minmax_prompt_reference as prompt

FORMULA = 'IDENTICAL:MinMax,MinLength=0,BaseDataType=A_BYTEFIELD,MaxLength=4092,BitMask=0,Encoding=Undefined,Termination=END-OF-PDU,Radix=10,DataType=A_BYTEFIELD,Precision=null;'
FMT = bytes.fromhex('c7ebcae4c8ebd7eeb6cc25642cd7eeb3a42564b5c4d7d6b7fb')


class CalcInputInfoTests(unittest.TestCase):
    def test_import_does_not_load_private_engine_or_native_libraries(self):
        r = subprocess.run([sys.executable, '-c',
            "import sys; from scripts.diagnostics import x431_calc_input_info_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_new_engines_check_originals_before_optional_engine(self):
        for cls, args in ((caller.CpuCalcInputInfoReference, (b'calc', b'std', b'deviceid', b'language')),
                (prompt.CpuMinMaxLanguageReference, (b'calc', b'std', b'language')),
                (prompt.CpuMinMaxSprintfReference, (b'calc', b'std', b'deviceid'))):
            with self.subTest(cls=cls), self.assertRaisesRegex(ValueError, 'calc-unreviewed'):
                cls(*args)

    def test_two_argument_format_is_exact_and_not_arbitrary_percent_input(self):
        self.assertEqual(prompt.validate_pair(FMT, struct.pack('<II', 6, 20)), (6, 20))
        for fmt in (FMT + b' ', FMT + b'\0', FMT.replace(b'%d', b'%n'), b'%d%d', b'%d', FMT.decode('latin1')):
            with self.subTest(fmt=fmt), self.assertRaisesRegex(ValueError, 'format-unreviewed'):
                prompt.validate_pair(fmt, bytes(8))

    def test_second_word_cannot_be_omitted_or_silently_read_from_stack(self):
        for words in (b'', bytes(4), bytes(12), bytearray(8), None):
            with self.assertRaisesRegex(ValueError, 'two-words-required'):
                prompt.validate_pair(FMT, words)

    def test_source_doubled_length_domain_and_rounding_are_bounded(self):
        prompt.validate_pair(FMT, struct.pack('<II', 0, 131070))
        for values in ((0, 131071), (0xffffffff, 0)):
            with self.assertRaisesRegex(ValueError, 'integer-domain'):
                prompt.validate_pair(FMT, struct.pack('<II', *values))
        for mode in (True, None, -1, 1 << 32, 1 << 22, 1 << 23):
            with self.assertRaisesRegex(ValueError, 'rounding-mode'):
                prompt.validate_pair(FMT, bytes(8), mode)

    def validate(self, **changes):
        args = dict(formula=FORMULA, replies=[dict(data=b'41', accepted=True)], title=b'field', flag=0)
        args.update(changes)
        return caller.validate_info(**args)

    def test_reply_storage_never_uses_the_unqualified_capacity_plus_nul_edge(self):
        self.validate(replies=[dict(data=b'A' * 4095, accepted=True)])
        for data in (b'A' * 4096, b'A\0B', '41', bytearray(b'41')):
            with self.assertRaisesRegex(ValueError, 'reply-invalid-or-nul-capacity'):
                self.validate(replies=[dict(data=data, accepted=True)])

    def test_no_implicit_replies_unbounded_retry_or_truthy_acceptance(self):
        for replies in ([], tuple(), [dict(data=b'41', accepted=True)] * 4,
                [dict(data=b'41')], [dict(data=b'41', accepted=1)], [dict(data=b'41', accepted=True, retry=True)]):
            with self.subTest(replies=replies), self.assertRaises(ValueError):
                self.validate(replies=replies)

    def test_other_formula_families_and_flags_keep_a_stop(self):
        for formula in (FORMULA.replace('IDENTICAL', 'LINEAR'), FORMULA.replace('MinMax', 'Standar'),
                        FORMULA.replace('A_BYTEFIELD', 'A_UINT16')):
            with self.assertRaisesRegex(ValueError, 'family-unreviewed'):
                self.validate(formula=formula)
        for flag in (True, 1, -1):
            with self.assertRaisesRegex(ValueError, 'flag-unreviewed'):
                self.validate(flag=flag)

    def test_title_cannot_inject_an_unreviewed_language_id(self):
        for title in (b'0xFF000001', b'prefix0X', b'A\0B', b'A' * 65, 'field'):
            with self.assertRaisesRegex(ValueError, 'title-invalid'):
                self.validate(title=title)

    def test_unknown_definition_snapshot_stops_before_engine_or_output_creation(self):
        with patch.object(caller, 'CpuCalcInputInfoReference') as engine:
            for census in ({}, {'definitions':[]}, {'sourceSha256':'foreign'}):
                with self.assertRaisesRegex(ValueError, 'census-unreviewed'):
                    caller.audit_matrix(b'calc', b'std', b'deviceid', b'language', census, None)
            engine.assert_not_called()

    def test_language_writes_respect_actual_capacity_and_preserve_context(self):
        p = prompt.CpuMinMaxLanguageReference.__new__(prompt.CpuMinMaxLanguageReference)
        p.allocations = {}; p.capacity = 1024
        self.assertTrue(p._writable(prompt.cpu.DATA, 1024))
        self.assertFalse(p._writable(prompt.cpu.DATA, 1025))
        self.assertFalse(p._writable(prompt.language.CTX, 4))
        p.capacity = 200
        self.assertFalse(p._writable(prompt.cpu.DATA + 199, 2))

    def test_caller_write_guard_rejects_unowned_source_and_past_nul_storage(self):
        p = caller.CpuCalcInputInfoReference.__new__(caller.CpuCalcInputInfoReference)
        p.allocations = {caller.base.HEAP:4096}
        p.write(None, None, caller.base.HEAP + 4095, 1, None, None)
        for address, size in ((caller.base.HEAP + 4095, 2), (caller.base.DATA, 1), (0x10b82c, 4), (0, 1)):
            with self.assertRaisesRegex(ValueError, 'write-range'):
                p.write(None, None, address, size, None, None)

    def test_all_owned_engines_are_attempted_even_after_child_close_failure(self):
        p = caller.CpuCalcInputInfoReference.__new__(caller.CpuCalcInputInfoReference)
        p.closed = False; children = []
        for name in ('pair', 'string', 'integer', 'language', 'sprintf'):
            child = Mock(); setattr(p, name, child); children.append(child)
        children[0].close.side_effect = RuntimeError('injected')
        with patch.object(caller.cpu.CpuSprintfReference, 'close') as primary:
            with self.assertRaisesRegex(ValueError, 'engine-cleanup-failed'):
                p.close()
            for child in children:
                child.close.assert_called_once_with()
            primary.assert_called_once_with(p)

    def test_source_odd_hex_acceptance_does_not_open_project_hex_editor(self):
        self.validate(replies=[dict(data=b'A', accepted=True)])
        self.assertFalse(caller.GUARDS['legalInputDomainQualified'])
        self.assertFalse(caller.GUARDS['codingWriteQualified'])
        from scripts.diagnostics.x431_text_field_reference import validate_field_bytes
        with self.assertRaisesRegex(ValueError, 'complete-hex-pairs-required'):
            validate_field_bytes(FORMULA, b'A', bytes(512), 0, 0)


if __name__ == '__main__':
    unittest.main()
