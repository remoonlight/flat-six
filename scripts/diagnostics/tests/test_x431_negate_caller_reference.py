import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from scripts.diagnostics import x431_negate_caller_reference as caller

FORMULA = 'IDENTICAL:MinMax,MinLength=0,BaseDataType=A_BYTEFIELD,MaxLength=4092,BitMask=0,Encoding=Undefined,Termination=END-OF-PDU,Radix=10,DataType=A_BYTEFIELD,Precision=null;'


class NegateCallerTests(unittest.TestCase):
    def setUp(self):
        pc = patch.object(caller.base, 'UC_ARM_REG_PC', 15, create=True)
        pc.start(); self.addCleanup(pc.stop)

    def validate(self, **changes):
        args = dict(formula=FORMULA, replies=[dict(data=b'41', accepted=True)], original=bytes(4096))
        args.update(changes)
        return caller.validate_call(**args)

    def test_cold_import_has_no_engine_native_library_or_transport(self):
        script = "import sys; from scripts.diagnostics import x431_negate_caller_reference; assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules; assert 'serial' not in sys.modules"
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_five_file_source_audit_precedes_engine_construction(self):
        with patch.object(caller.CpuCalcInputInfoReference, '__init__') as construct:
            with self.assertRaisesRegex(ValueError, 'calc-unreviewed'):
                caller.CpuNegateCallerReference(b'calc', b'std', b'deviceid', b'language', b'readwrite')
        construct.assert_not_called()

    def test_hook_counterexample_blocks_complete_caller_before_seven_engines(self):
        with patch.object(caller, 'audit_sources', return_value={}), \
                patch.object(caller, 'audit_instrumentation', return_value={
                    'memoryWriteHookArchitecturallyQualified':False}), \
                patch.object(caller.CpuCalcInputInfoReference, '__init__') as construct:
            with self.assertRaisesRegex(ValueError, 'instrumentation-unqualified'):
                caller.CpuNegateCallerReference(b'calc', b'std', b'deviceid', b'language', b'readwrite')
        construct.assert_not_called()

    def test_instrumentation_probe_rejects_unreviewed_source_before_private_engine(self):
        with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
            caller.audit_instrumentation(b'calc')

    def test_only_retained_exact_formulas_are_admitted(self):
        self.validate()
        for formula in (FORMULA.replace('4092', '4091'), FORMULA.replace('BitMask=0', 'BitMask=1'), FORMULA + ' '):
            with self.assertRaisesRegex(ValueError, 'exact-source-formula'):
                self.validate(formula=formula)

    def test_window_requires_complete_512_bytes_beyond_offset_and_header_space(self):
        self.validate(original=bytes(514), byte=2)
        self.validate(original=bytes(4096), byte=3584)
        for original, byte in ((bytes(512), 0), (bytes(513), 2), (bytes(4097), 4),
                (bytes(4096), 3585), (bytes(4096), 1), (bytearray(4096), 4), (bytes(4096), True)):
            with self.subTest(size=len(original), byte=byte), self.assertRaisesRegex(ValueError, 'source-window-or-header'):
                self.validate(original=original, byte=byte)

    def test_bit_offset_cannot_silently_change_input_flag(self):
        for bit in (1, 7, -1, True, '0'):
            with self.assertRaises(ValueError):
                self.validate(bit=bit)

    def test_no_unbounded_replies_or_embedded_nul_or_implicit_acceptance(self):
        for replies in ([], [dict(data=b'41', accepted=True)] * 4, [dict(data=b'A\0B', accepted=True)],
                [dict(data=b'41', accepted=1)], [dict(data=b'0' * 4096, accepted=True)]):
            with self.assertRaises(ValueError):
                self.validate(replies=replies)

    def test_canceled_nonempty_reply_is_explicit_and_not_automatically_accepted(self):
        self.validate(replies=[dict(data=b'41', accepted=False)])
        self.assertFalse(caller.GUARDS['cancellationIsVehicleSuccess'])
        self.assertFalse(caller.GUARDS['codingWriteQualified'])
        self.assertIsNone(caller.GUARDS['writePayload'])

    def test_buffers_have_distinct_bounds_immutable_inputs_and_no_global_slot_writes(self):
        p = object.__new__(caller.CpuNegateCallerReference)
        p.active_bytes = 4096; p.allocations = {}; p.u = Mock(); p.u.reg_read.return_value = 0xc5aca
        for address, size in ((caller.base.DATA + 4095, 1), (caller.LOG + 4095, 1),
                (caller.LENGTH + 31, 1), (caller.base.STACK, 4)):
            p.write(None, None, address, size, 0, None)
        for address, size in ((caller.base.DATA + 4095, 2), (caller.LOG + 4095, 2),
                (caller.LENGTH + 31, 2), (caller.TITLE, 1), (caller.FORMULA, 1), (0x10b82c, 4)):
            with self.subTest(address=address), self.assertRaises(caller.RejectedVirtualWrite):
                p.write(None, None, address, size, 0, None)

    def test_shared_empty_string_write_is_recorded_and_stays_rejected(self):
        p = object.__new__(caller.CpuNegateCallerReference)
        p.active_bytes = 4096; p.allocations = {}; p.u = Mock(); p.u.reg_read.return_value = 0xc5aca
        with self.assertRaises(caller.RejectedVirtualWrite) as failure:
            p.write(None, None, 0x10ce1c, 1, 48, None)
        self.assertEqual(failure.exception.detail, dict(address=0x10ce1c, size=1, value=48, instructionVa=0xc5aca))

    def test_bad_write_size_cannot_wrap_or_alias_a_buffer(self):
        p = object.__new__(caller.CpuNegateCallerReference)
        for size in (-1, 65537, True, None):
            with self.assertRaisesRegex(ValueError, 'write-size'):
                p.write(None, None, caller.LOG, size, 0, None)

    def test_all_seven_engine_cleanup_attempts_survive_first_failure(self):
        p = object.__new__(caller.CpuNegateCallerReference)
        p.closed = False
        keys = ('hex', 'pair', 'string', 'integer', 'language', 'sprintf')
        for key in keys:
            setattr(p, key, Mock())
        p.hex.close.side_effect = ValueError('first engine failed')
        with patch.object(caller.cpu.CpuSprintfReference, 'close') as primary:
            with self.assertRaisesRegex(ValueError, 'engine-cleanup-failed'):
                p.close()
        primary.assert_called_once_with(p)
        for key in keys:
            getattr(p, key).close.assert_called_once_with()

    def test_negative_input_return_is_not_unsigned_length_or_original_buffer_size(self):
        self.assertEqual(caller.signed(0xffffffff), -1)
        self.assertEqual(caller.signed(16), 16)
        self.assertFalse(caller.GUARDS['actualInputInfoReturnQualified'])
        self.assertFalse(caller.GUARDS['completeCodingBlockQualified'])


if __name__ == '__main__':
    unittest.main()
