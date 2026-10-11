import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.diagnostics import x431_bcd_input_reference as bcd

FORMULA = 'IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=8,Encoding=BCD-P,HighLow=1,Radix=10,DataType=A_UINT32,Precision=null;'


class BcdInputReferenceTests(unittest.TestCase):
    def test_cold_import_has_no_optional_engine_native_loader_or_head(self):
        script = "import sys; from scripts.diagnostics import x431_bcd_input_reference; assert not {'unicorn','ctypes','serial'} & set(sys.modules)"
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_original_blocks_helper_and_complete_caller_before_engine(self):
        for cls, parent in ((bcd.CpuBcdHelperReference, bcd.CpuInputReference),
                (bcd.CpuBcdCallerReference, bcd.CpuNegateCallerReference)):
            with patch.object(parent, '__init__') as construct:
                with self.assertRaisesRegex(ValueError, 'calc-unreviewed'):
                    cls(b'calc', b'std', b'deviceid', b'language', b'readwrite')
            construct.assert_not_called()

    def test_catalog_source_hash_prevents_partial_or_edited_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'variants.jsonl'; path.write_text('{}\n')
            with self.assertRaisesRegex(ValueError, 'catalog-source-unreviewed'):
                bcd.collect_definitions(path)

    def test_exact_formula_not_family_wildcard(self):
        bcd.validate_helper(FORMULA, b'12')
        for formula in (FORMULA + ' ', FORMULA.replace('BitLength=8', 'BitLength=12'),
                FORMULA.replace('BitMask=0', 'BitMask=1'), FORMULA.replace('BCD-P', 'Unsigned')):
            with self.subTest(formula=formula), self.assertRaises(ValueError):
                bcd.validate_helper(formula, b'12')

    def test_synthetic_encoding_control_is_explicit_and_not_a_retained_formula(self):
        control = FORMULA.replace('Encoding=BCD-P,', 'Encoding=Unsigned,')
        bcd.validate_helper(control, b'12', True)
        for formula, flag in ((control, False), (FORMULA, True), (control, 1),
                (control.replace('HighLow=1', 'HighLow=0'), True)):
            with self.assertRaises(ValueError):
                bcd.validate_helper(formula, b'12', flag)

    def test_helper_raw_storage_is_bounded_not_unicode_or_embedded_nul(self):
        for data in (b'', b'1' * 64, bytes((1, 255)), b'\xc3\xa9'):
            bcd.validate_helper(FORMULA, data)
        for data in ('12', bytearray(b'12'), b'1' * 65, b'1\0', None):
            with self.assertRaises(ValueError):
                bcd.validate_helper(FORMULA, data)

    def test_zero_padding_is_local_to_exact_bcd_formatter_domain(self):
        p = object.__new__(bcd.CpuBcdIntegerReference)
        for value in (-799, -1, 0, 1, 3519):
            self.assertEqual(p.validate_format(b'%02d', struct.pack('<i', value)), value & 0xffffffff)
        for value in (-800, 3520):
            with self.assertRaises(ValueError):
                p.validate_format(b'%02d', struct.pack('<i', value))
        with self.assertRaisesRegex(ValueError, 'format-unreviewed'):
            bcd.validate_integer_format(b'%02d', bytes(4))
        with self.assertRaises(ValueError):
            p.validate_format(b'%02d', bytes(4), 0x00400000)
        with self.assertRaises(ValueError):
            p.validate_format(b'%02d', bytes(3))

    def test_caller_keeps_cancel_explicit_and_limits_reply_budget(self):
        bcd.validate_caller(FORMULA, [dict(data=b'12', accepted=False)], bytes(4096))
        for replies in ([], [dict(data=b'12', accepted=True)] * 4,
                [dict(data=b'12', accepted=1)], [dict(data=b'1\0', accepted=True)],
                [dict(data=b'1' * 65, accepted=True)], [dict(data=b'12')]):
            with self.assertRaises(ValueError):
                bcd.validate_caller(FORMULA, replies, bytes(4096))

    def test_caller_requires_distinct_header_safe_window_and_zero_bit_offset(self):
        replies = [dict(data=b'12', accepted=True)]
        bcd.validate_caller(FORMULA, replies, bytes(514), byte=2)
        bcd.validate_caller(FORMULA, replies, bytes(4096), byte=3584)
        for changes in (dict(byte=1), dict(byte=3585), dict(byte=True), dict(bit=1),
                dict(bit=True), dict(title=b'field\0'), dict(title=b'0x1234')):
            with self.assertRaises(ValueError):
                bcd.validate_caller(FORMULA, replies, bytes(4096), **changes)

    def test_helper_write_guard_never_includes_shared_empty_string_or_global_slots(self):
        p = object.__new__(bcd.CpuBcdHelperReference); p.allocations = {bcd.base.HEAP:32}
        for address, size in ((bcd.OBJECTS[0], 4), (bcd.OBJECTS[1], 4),
                (bcd.base.HEAP + 31, 1), (bcd.base.STACK, 4)):
            p.write(None, None, address, size, 0, None)
        for address, size in ((bcd.OBJECTS[1] + 3, 2), (bcd.base.HEAP + 31, 2),
                (0x10ce1c, 1), (0x10ba5c, 4), (bcd.base.DATA, 1), (bcd.base.HEAP, -1)):
            with self.assertRaises(ValueError):
                p.write(None, None, address, size, 0, None)

    def test_bcd_formatting_cannot_overrun_original_five_byte_stack_destination(self):
        p = object.__new__(bcd.CpuBcdHelperReference)
        p.hooks = {99:'sprintf'}; p.formats = []; p.integer = Mock(); p.write = Mock()
        p.integer.format_integer.return_value = {'outputBytesHex':b'12345'.hex()}
        u = Mock(); p.u = u
        names = ('R0', 'R1', 'R2', 'LR', 'SP', 'FPSCR')
        with patch.multiple(bcd.base, create=True, **{'UC_ARM_REG_' + name:i for i,name in enumerate(names)}):
            u.reg_read.side_effect = lambda reg: (0x2024, 0xf5f42, 0, 0x246df, 0x2000, 0)[reg]
            with self.assertRaisesRegex(ValueError, 'stack-format-capacity'):
                p.code(u, 99, 4, None)
        p.write.assert_not_called(); u.mem_write.assert_not_called()

    def test_helper_cleanup_attempts_main_even_if_child_fails(self):
        p = object.__new__(bcd.CpuBcdHelperReference); p.closed = False; p.integer = Mock()
        p.integer.close.side_effect = ValueError('child failed')
        with patch.object(bcd.cpu.CpuSprintfReference, 'close') as primary:
            with self.assertRaisesRegex(ValueError, 'engine-cleanup-failed'):
                p.close()
        primary.assert_called_once_with(p)

    def test_execution_flags_use_observed_helper_entry_not_return_or_vehicle_success(self):
        p = object.__new__(bcd.CpuBcdCallerReference)
        p.pending_bcd_prompt = None; p.bcd_events = []; p.bcd_formats = []; p.bcd_prompts = []
        with patch.object(bcd.CpuNegateCallerReference, 'call', return_value=dict(returnValue=-1)):
            result = p.call(FORMULA, [dict(data=b'', accepted=False)], bytes(4096))
        self.assertFalse(result['originalBcdHelperExecuted'])
        self.assertFalse(result['originalMinMaxCallerChainExecuted'])
        self.assertTrue(result['returnValueIsNotVehicleSuccess'])
        self.assertFalse(result['codingWriteQualified']); self.assertFalse(result['executionEnabled'])
        self.assertIsNone(result['writePayload'])


if __name__ == '__main__':
    unittest.main()
