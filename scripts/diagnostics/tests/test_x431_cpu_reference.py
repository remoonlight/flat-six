import collections
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.diagnostics import x431_cpu_reference as reference


class CpuReferenceBoundaryTests(unittest.TestCase):
    def test_wrong_original_rejected_before_optional_engine_import(self):
        for blob in (None,bytearray(3),b'',bytes(reference.SOURCE_BYTES)):
            with self.subTest(type=type(blob)),self.assertRaisesRegex(ValueError,'source-unreviewed'):
                reference.CpuReference(blob)

    def test_module_import_requires_no_cpu_dependency(self):
        # A fresh ordinary Python process must import the module without Unicorn.
        code = "import sys; from scripts.diagnostics import x431_cpu_reference; assert 'unicorn' not in sys.modules"
        result = subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_boolean_and_noninteger_layouts_are_not_source_integer_arguments(self):
        for bit,width in ((True,1),(0,False),(0.0,8),(0,'8'),(-1,8),(8,8),(0,0),(0,33)):
            with self.subTest(bit=bit,width=width),self.assertRaisesRegex(ValueError,'layout-invalid'):
                reference.validate_case(bytes(8),bit,width)

    def test_truncated_and_large_buffers_rejected(self):
        for data in (None,bytearray(8),b'',bytes(1025)):
            with self.assertRaisesRegex(ValueError,'buffer-invalid'):
                reference.validate_case(data,0,8)
        with self.assertRaisesRegex(ValueError,'truncated'):
            reference.validate_case(bytes(1),7,2)

    def test_unsigned_targets_and_byte_order_checked_without_coercion(self):
        for raw in (True,-1,256,1.0,'1'):
            with self.subTest(raw=raw),self.assertRaisesRegex(ValueError,'value-invalid'):
                reference.validate_case(bytes(8),0,8,raw)
        for hl in (True,None,2,'1'):
            with self.assertRaisesRegex(ValueError,'byte-order-invalid'):
                reference.validate_case(bytes(8),0,8,1,hl)

    def test_single_byte_helper_cannot_be_used_for_cross_byte_cases(self):
        reference.validate_case(bytes(1),5,3,7,ex=True)
        with self.assertRaisesRegex(ValueError,'not-single-byte'):
            reference.validate_case(bytes(8),7,2,1,ex=True)
        with self.assertRaisesRegex(ValueError,'byte-order-invalid'):
            reference.validate_case(bytes(8),0,8,1,ex=1)

    def test_unknown_original_function_range_stops_before_execution(self):
        probe = reference.CpuReference.__new__(reference.CpuReference)
        probe.last=collections.deque(maxlen=24);probe.trace=collections.Counter();probe.hooks={}
        for address in (0,0x2ef44,0x31cd0,0x10000000):
            with self.assertRaisesRegex(ValueError,'code-range-unreviewed'):
                probe.code(None,address,2,None)

    def test_unknown_callout_cannot_be_forwarded_to_host(self):
        probe = reference.CpuReference.__new__(reference.CpuReference)
        probe.last=collections.deque(maxlen=24);probe.trace=collections.Counter()
        probe.hooks={0x3000000:'socket'}
        class Registers:
            def reg_read(self,_):return 0
        with patch.multiple(reference,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,UC_ARM_REG_R3=3,create=True):
            with self.assertRaisesRegex(ValueError,'import-call-unreviewed'):
                probe.code(Registers(),0x3000000,2,None)

    def test_original_instruction_hook_enforces_wall_budget(self):
        probe=reference.CpuReference.__new__(reference.CpuReference)
        probe.last=collections.deque(maxlen=24);probe.hooks={}
        probe.trace=collections.Counter(originalInstructions=1023);probe.deadline=-1
        with self.assertRaisesRegex(ValueError,'wall-budget-exhausted'):
            probe.code(None,0x26b1c,2,None)

    def test_collateral_check_covers_big_and_little_endian_physical_bits(self):
        # 12-bit target at offset 2: big-endian spare bits are byte0 top2/byte1 low2.
        before=bytes.fromhex('A5BCDD')
        self.assertTrue(reference.untouched_bits_preserved(before,bytes.fromhex('AAF0DD'),2,12,1))
        self.assertFalse(reference.untouched_bits_preserved(before,bytes.fromhex('6AF0DD'),2,12,1))
        self.assertFalse(reference.untouched_bits_preserved(before,bytes.fromhex('AAF1DD'),2,12,1))
        # Little-endian spare bits are byte0 low2/byte1 top2; trailing bytes stay.
        self.assertTrue(reference.untouched_bits_preserved(before,bytes.fromhex('F1AADD'),2,12,0))
        for after in ('F0AADD','F1EADD','F1AADC'):
            self.assertFalse(reference.untouched_bits_preserved(before,bytes.fromhex(after),2,12,0))

    def test_guards_never_grant_android_or_vehicle_workflow_qualification(self):
        for key in ('hostElfLoaded','actualDynamicBindingQualified','nativeEditingBehaviorVerified',
                    'vehicleVerified','codingWriteQualified','codingRestoreQualified','executionEnabled'):
            self.assertFalse(reference.GUARDS[key])
        self.assertTrue(reference.GUARDS['cpuEmulationOnly'])
        self.assertIsNone(reference.GUARDS['writePayload'])


if __name__=='__main__':unittest.main()
