import collections
import subprocess
import sys
import unittest
import weakref
from unittest.mock import patch

from scripts.diagnostics import x431_input_reference as reference


class InputReferenceTests(unittest.TestCase):
    def test_default_import_has_no_optional_engine_dependency(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from scripts.diagnostics import x431_input_reference; assert 'unicorn' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_source_rejected_before_engine_import(self):
        for blob in (None, b'', bytes(reference.base.SOURCE_BYTES)):
            with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
                reference.CpuInputReference(blob)

    def test_complete_static_scope_retains_both_callbacks(self):
        self.assertEqual(len(reference.INPUT_REGIONS), 7)
        self.assertEqual(len(reference.CALLBACK_SITES), 30)
        self.assertEqual(collections.Counter(row[-1] for row in reference.CALLBACK_SITES),
                         {'SPRINTF': 16, 'GetTextFromTextLib': 14})

    def test_explicit_bytes_keep_unknown_character_encoding_separate(self):
        formula = reference.field.integer_formula('IDENTICAL', 16, 1)
        for data in (b'', b'AB', bytes.fromhex('C3A9'), bytes.fromhex('E4B880')):
            self.assertTrue(reference.validate_input(formula, data, 0)['ok'])
        for data in ('AB', bytearray(b'AB'), b'A\0B', bytes(129)):
            with self.assertRaisesRegex(ValueError, 'bytes-invalid'):
                reference.validate_input(formula, data, 0)

    def test_formula_and_flag_are_bounded_and_not_arbitrary_dispatch(self):
        formula = reference.field.integer_formula('IDENTICAL', 16, 1)
        for value in (None, '', 'X' * 1001, formula + '\0', formula + '中'):
            with self.assertRaisesRegex(ValueError, 'formula-invalid'):
                reference.validate_input(value, b'1', 0)
        for flag in (-1, 2, True, '0'):
            with self.assertRaisesRegex(ValueError, 'flag-unreviewed'):
                reference.validate_input(formula, b'1', flag)
        with self.assertRaisesRegex(ValueError, 'family-unreviewed'):
            reference.validate_input('TEXTTABLE:BitLength=8;', b'1', 0)

    def test_integer_prefix_matches_explicit_c_cases_not_full_python_parse(self):
        cases = [(b'  +12rest', 10, True, 32, 12), (b'-2', 10, False, 32, 0xfffffffe),
                 (b'1.5', 10, True, 64, 1), (b'abc', 10, True, 32, 0),
                 (b'', 10, False, 64, 0), (b'0x10', 10, False, 32, 0),
                 (b'0x10', 16, False, 32, 16), (b'0x', 16, False, 32, 0),
                 (b'1012', 2, False, 32, 5), (b'-80000000', 16, True, 32, 0x80000000),
                 (b'-9223372036854775808', 10, True, 64, 1 << 63),
                 (b'18446744073709551615', 10, False, 64, (1 << 64) - 1)]
        for data, radix, signed, bits, value in cases:
            with self.subTest(data=data):
                self.assertEqual(reference.integer_prefix(data, radix, signed, bits), value)

    def test_overflow_errno_and_unreviewed_integer_contracts_stop(self):
        for data, signed, bits in ((b'2147483648', True, 32), (b'-2147483649', True, 32),
                                 (b'4294967296', False, 32), (b'18446744073709551616', False, 64)):
            with self.assertRaisesRegex(ValueError, 'overflow-unreviewed'):
                reference.integer_prefix(data, 10, signed, bits)
        for args in ((b'1', 0, True, 32), (b'1', True, True, 32), (b'1', 10, 1, 32),
                     (b'1', 10, True, 16), (b'\xff', 10, True, 32), (b'1\0', 10, True, 32)):
            with self.assertRaisesRegex(ValueError, 'contract-unreviewed'):
                reference.integer_prefix(*args)

    def test_partial_or_closed_context_cannot_continue(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        formula = reference.field.integer_formula('IDENTICAL', 16, 1)
        for poisoned, closed in ((True, False), (False, True)):
            probe.poisoned, probe.closed = poisoned, closed
            with self.assertRaisesRegex(ValueError, 'prior-outcome-unknown-or-closed'):
                probe.validate(formula, b'1')

    def test_sprintf_boundary_records_original_format_pointer_and_never_calls_it(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.callback_sites = {0x31946: {'name': 'SPRINTF', 'objectVa': 12}}
        probe.uint = lambda _: 0
        probe.cstr = lambda _: b'%.3f'
        class Registers:
            def reg_read(self, reg): return [100, 200, 0, 0][reg]
        with patch.multiple(reference.base, UC_ARM_REG_R0=0, UC_ARM_REG_R1=1,
                            UC_ARM_REG_R2=2, UC_ARM_REG_R3=3, create=True):
            with self.assertRaises(reference.CallbackBoundary) as stopped:
                probe.code(Registers(), 0x31946, 2, None)
        self.assertEqual(stopped.exception.args[0]['formatBytesHex'], '252E3366')
        self.assertFalse(stopped.exception.args[0]['callbackExecuted'])

    def test_nonempty_runtime_slot_is_not_guessed_or_invoked(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.callback_sites = {0x342c8: {'name': 'GetTextFromTextLib', 'objectVa': 12}}
        probe.uint = lambda _: 123
        with self.assertRaisesRegex(ValueError, 'slot-not-unbound'):
            probe.code(None, 0x342c8, 2, None)

    def test_unreviewed_float_input_stops_without_guessing_empty_string_value(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.callback_sites = {}; probe.hooks = {100: 'strtod'}
        probe.cstr = lambda _: b''
        class Registers:
            def reg_read(self, reg): return [200, 0][reg]
        with patch.multiple(reference.base, UC_ARM_REG_R0=0, UC_ARM_REG_R1=1, create=True):
            with self.assertRaises(reference.FoundationBoundary) as stopped:
                probe.code(Registers(), 100, 2, None)
        self.assertEqual(stopped.exception.args[0]['name'], 'strtod')
        self.assertFalse(stopped.exception.args[0]['conversionExecuted'])

    def test_original_input_code_enforces_wall_budget(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.callback_sites = {}; probe.hooks = {}
        probe.last = collections.deque(maxlen=24)
        probe.trace = collections.Counter(originalInstructions=1023); probe.deadline = -1
        with self.assertRaisesRegex(ValueError, 'wall-budget-exhausted'):
            probe.code(None, 0x33fd8, 2, None)

    def test_unknown_native_imports_remain_rejected(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.callback_sites = {}; probe.hooks = {0x3000000: 'socket'}
        probe.last = collections.deque(maxlen=24); probe.trace = collections.Counter(); probe.field_events = []
        class Registers:
            def reg_read(self, _): return 0
        with patch.multiple(reference.base, UC_ARM_REG_R0=0, UC_ARM_REG_R1=1,
                            UC_ARM_REG_R2=2, UC_ARM_REG_R3=3, create=True):
            with self.assertRaisesRegex(ValueError, 'import-call-unreviewed'):
                probe.code(Registers(), 0x3000000, 2, None)

    def test_explicit_engine_disposal_runs_once_even_with_retained_python_reference(self):
        calls = []
        class Engine:
            def __init__(self):
                self._Uc__finalizer = weakref.finalize(self, calls.append, 'native-close')
                self._uch = 123
            def hook_del(self, handle): calls.append(('hook-del', handle))
        engine = Engine()
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.u = engine; probe.code_hook = 9; probe.closed = False
        probe.close(); probe.close()
        self.assertEqual(calls, [('hook-del', 9), 'native-close'])
        self.assertIsNone(engine._uch); self.assertIsNone(probe.u)
        self.assertTrue(probe.poisoned)

    def test_failed_or_unknown_engine_cleanup_is_not_reported_as_disposed(self):
        probe = reference.CpuInputReference.__new__(reference.CpuInputReference)
        probe.u = object(); probe.closed = False
        with self.assertRaisesRegex(ValueError, 'finalizer-unreviewed'):
            probe.close()
        self.assertFalse(probe.closed)

    def test_matrix_preserves_invalid_prefix_and_non_ascii_stored_byte_cases(self):
        cases = list(reference.matrix_cases())
        self.assertEqual(len(cases), 169)
        self.assertTrue(any(row['inputBytesHex'] == '312E35' for row in cases))
        self.assertTrue(any(row['inputBytesHex'] == 'E4B880' for row in cases))
        self.assertEqual(sum(row['kind'] == 'linear-runtime-callback' for row in cases), 9)

    def test_completed_instruction_subset_cannot_grant_android_or_vehicle_qualification(self):
        self.assertTrue(reference.GUARDS['inputInstructionsOnly'])
        for key in ('runtimeCallbacksBound', 'androidInputWorkflowVerified', 'legalInputDomainQualified',
                    'inputCharacterEncodingQualified', 'actualDynamicBindingQualified',
                    'nativeEditingBehaviorVerified', 'vehicleVerified', 'codingWriteQualified',
                    'codingRestoreQualified', 'executionEnabled', 'hostElfLoaded'):
            self.assertFalse(reference.GUARDS[key])
        self.assertIsNone(reference.GUARDS['writePayload'])


if __name__ == '__main__':
    unittest.main()
