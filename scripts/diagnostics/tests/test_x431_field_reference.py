import collections
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.diagnostics import x431_field_reference as reference


class FieldReferenceBoundaryTests(unittest.TestCase):
    def test_default_import_has_no_engine_dependency(self):
        code="import sys; from scripts.diagnostics import x431_field_reference; assert 'unicorn' not in sys.modules"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_wrong_original_rejected_before_engine_import(self):
        for blob in (None,b'',bytes(reference.base.SOURCE_BYTES)):
            with self.assertRaisesRegex(ValueError,'source-unreviewed'):
                reference.CpuFieldReference(blob)

    def test_actual_source_copy_requires_complete_512_byte_window(self):
        formula=reference.integer_formula('LINEAR',16,1)
        for data,byte in ((bytes(511),0),(bytes(512),1),(bytes(1024),513),
                          (bytes(1025),0),(bytearray(1024),0),(bytes(1024),True)):
            with self.subTest(size=len(data),byte=byte),self.assertRaisesRegex(ValueError,'source-window-required'):
                reference.validate_field(formula,'1',data,byte,0)
        self.assertEqual(reference.validate_field(formula,'1',bytes(1024),512,0)['bitLength'],16)

    def test_strings_do_not_admit_nul_non_ascii_or_unbounded_data(self):
        formula=reference.integer_formula('LINEAR',16,1)
        for text in ('','1\0','一','1'*129):
            with self.assertRaisesRegex(ValueError,'target-text-invalid'):
                reference.validate_field(formula,text,bytes(512),0,0)
        for value in (None,formula+'\0',formula+'度','X'*1001):
            with self.assertRaisesRegex(ValueError,'formula-invalid'):
                reference.validate_field(value,'1',bytes(512),0,0)

    def test_other_editor_and_encoding_paths_are_not_silently_simulated(self):
        formula=reference.integer_formula('IDENTICAL',16,1)
        for value,message in ((formula.replace('A_UINT32','A_ASCIISTRING'),'integer-path-required'),
                              (formula.replace('Undefined','BCD'),'integer-encoding-unreviewed'),
                              ('TEXTTABLE:BitLength=8;','formula-family-unreviewed')):
            with self.assertRaisesRegex(ValueError,message):
                reference.validate_field(value,'1',bytes(512),0,0)

    def test_layout_rejects_boolean_offsets_and_unreviewed_width(self):
        formula=reference.integer_formula('LINEAR',16,1)
        for bit in (True,-1,8,'0'):
            with self.assertRaisesRegex(ValueError,'layout-invalid'):
                reference.validate_field(formula,'1',bytes(512),0,bit)
        with self.assertRaisesRegex(ValueError,'layout-invalid'):
            reference.validate_field(formula.replace('BitLength=16','BitLength=33'),'1',bytes(512),0,0)

    def test_signed_and_unsigned_libc_words_are_exact_and_bounded(self):
        for text,radix,signed,expected in (('-2',10,True,0xfffffffe),('-2',10,False,0xfffffffe),
                                          ('0x81234567',16,False,0x81234567),('111',2,False,7)):
            self.assertEqual(reference.libc_integer(text,radix,signed),expected)
        for text,radix,signed in (('2147483648',10,True),('4294967296',10,False),
                                 ('12rest',10,False),('0x10',10,False),('1',True,False),('1',10,1)):
            with self.assertRaises(ValueError):reference.libc_integer(text,radix,signed)

    def test_soft_float_conversion_preserves_both_words(self):
        for text in ('-2','102.4','1.0e-1','-0.0'):
            words=reference.libc_double(text)
            self.assertEqual(struct.pack('<II',*words),struct.pack('<d',float(text)))
        for text in ('nan','inf','1e999','1/10','1 ','0x1p0','1\0'):
            with self.assertRaises(ValueError):reference.libc_double(text)

    def test_original_field_code_enforces_wall_budget(self):
        probe=reference.CpuFieldReference.__new__(reference.CpuFieldReference)
        probe.last=collections.deque(maxlen=24);probe.trace=collections.Counter(originalInstructions=1023)
        probe.deadline=-1;probe.field_events=[]
        with self.assertRaisesRegex(ValueError,'wall-budget-exhausted'):
            probe.code(None,0x31cd0,2,None)

    def test_field_call_cannot_continue_after_unknown_outcome(self):
        probe=reference.CpuFieldReference.__new__(reference.CpuFieldReference);probe.poisoned=True
        with self.assertRaisesRegex(ValueError,'prior-outcome-unknown'):
            probe.field(reference.integer_formula('LINEAR',16,1),'1',bytes(512))

    def test_unreviewed_native_code_and_device_calls_stop(self):
        probe=reference.CpuFieldReference.__new__(reference.CpuFieldReference)
        probe.last=collections.deque(maxlen=24);probe.trace=collections.Counter();probe.field_events=[]
        probe.hooks={}
        with self.assertRaisesRegex(ValueError,'code-range-unreviewed'):
            probe.code(None,0x50000,2,None)
        probe.hooks={0x3000000:'socket'}
        class Registers:
            def reg_read(self,_):return 0
        with patch.multiple(reference.base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,UC_ARM_REG_R3=3,create=True):
            with self.assertRaisesRegex(ValueError,'import-call-unreviewed'):
                probe.code(Registers(),0x3000000,2,None)

    def test_calculation_never_grants_ui_or_vehicle_qualification(self):
        self.assertTrue(reference.GUARDS['fieldCalculationOnly'])
        for key in ('androidInputValidationExecuted','nativeEditingBehaviorVerified','legalInputDomainQualified',
                    'vehicleVerified','codingWriteQualified','codingRestoreQualified','executionEnabled','hostElfLoaded'):
            self.assertFalse(reference.GUARDS[key])
        self.assertIsNone(reference.GUARDS['writePayload'])


if __name__=='__main__':unittest.main()
