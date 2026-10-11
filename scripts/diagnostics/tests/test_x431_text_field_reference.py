import subprocess
import sys
import unittest
from unittest.mock import patch
from scripts.diagnostics import x431_text_field_reference as text
from scripts.diagnostics import x431_hex_sprintf_reference as hexbyte

FIXED='IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=16,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'
VARIABLE='IDENTICAL:MinMax,MinLength=0,BaseDataType=A_ASCIISTRING,MaxLength=256,BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'


class TextFieldReferenceTests(unittest.TestCase):
    def test_default_import_has_no_engine_or_host_libc(self):
        r=subprocess.run([sys.executable,'-c',"import sys;from scripts.diagnostics import x431_text_field_reference;assert 'unicorn' not in sys.modules;assert 'ctypes' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_source_identity_precedes_engine(self):
        with self.assertRaisesRegex(ValueError,'callback-source-calc-unreviewed'):text.CpuTextFieldReference(b'calc',b'std',b'deviceid')

    def test_hex_format_is_exact_including_space(self):
        for fmt in (b'%02X',b' %02x',b' %X',b' %02X\0',' %02X',None):
            with self.assertRaisesRegex(ValueError,'format-unreviewed'):hexbyte.validate_byte(fmt,1)

    def test_hex_word_must_be_a_byte(self):
        for value in (True,-1,256,1.0,None):
            with self.assertRaisesRegex(ValueError,'byte-required'):hexbyte.validate_byte(b' %02X',value)
        for value in (0,1,255):hexbyte.validate_byte(b' %02X',value)

    def test_rounding_contract_remains_explicit(self):
        for fpscr in (True,-1,1<<32,1<<22,1<<23):
            with self.assertRaisesRegex(ValueError,'rounding-mode'):hexbyte.validate_byte(b' %02X',1,fpscr)

    def test_raw_text_storage_is_not_character_encoding_qualification(self):
        self.assertEqual(text.validate_field_bytes(FIXED,b'\xc3\xa9',b'\0'*512,0,0)['baseDataType'],'A_ASCIISTRING')
        text.validate_field_bytes(FIXED,b'\0A',b'\0'*512,0,0)
        self.assertFalse(text.GUARDS['inputCharacterEncodingQualified'])
        self.assertFalse(text.GUARDS['codingWriteQualified'])

    def test_bytefield_requires_complete_bounded_hex_pairs(self):
        formula=FIXED.replace('A_ASCIISTRING','A_BYTEFIELD').replace('A_UNICODE2STRING','A_BYTEFIELD')
        for data in (b'1',b'GG',b' 41',b'\xc3\xa9',b'00\0'):
            with self.assertRaisesRegex(ValueError,'hex-pairs-required'):text.validate_field_bytes(formula,data,b'\0'*512,0,0)
        text.validate_field_bytes(formula,b'4142',b'\0'*512,0,0)

    def test_fixed_layout_and_copy_window_must_fit(self):
        for original,byte,bit in ((b'\0'*511,0,0),(b'\0'*512,1,0),(b'\0'*512,0,8),(b'\0'*4097,0,0)):
            with self.assertRaisesRegex(ValueError,'source-window'):text.validate_field_bytes(FIXED,b'A',original,byte,bit)
        with self.assertRaisesRegex(ValueError,'fixed-layout'):text.validate_field_bytes(FIXED,b'ABC',b'\0'*512,0,0)

    def test_large_real_extent_is_supported_without_shrinking_catalog(self):
        formula=FIXED.replace('BitLength=16','BitLength=32736')
        text.validate_field_bytes(formula,b'A'*4092,b'\0'*4096,4,0)
        with self.assertRaisesRegex(ValueError,'four-kib'):text.validate_field_bytes(formula,b'A'*4093,b'\0'*4096,0,0)

    def test_other_formula_families_stop(self):
        for formula in ('TEXTTABLE:DataType=A_BYTEFIELD,[00]->0x12345678;BitLength=8,BitMask=0,HighLow=1;',FIXED.replace('A_ASCIISTRING','A_UNICODE2STRING')):
            with self.assertRaisesRegex(ValueError,'family-unreviewed'):text.validate_field_bytes(formula,b'A',b'\0'*512,0,0)

    def test_variable_path_cannot_treat_buffer_length_as_input_info_result(self):
        p=text.CpuTextFieldReference.__new__(text.CpuTextFieldReference)
        with self.assertRaisesRegex(ValueError,'explicit-unqualified-input-info-result-required'):p.field_bytes(VARIABLE,b'A',b'\0'*512)

    def test_write_guard_stops_unowned_or_past_buffer_writes(self):
        p=text.CpuTextFieldReference.__new__(text.CpuTextFieldReference);p.active_bytes=512;p.allocations={text.base.HEAP:32}
        for address,size in ((text.base.DATA,512),(text.base.HEAP,32),(text.base.STACK,100)):
            p.write(None,None,address,size,None,None)
        for address,size in ((0,4),(text.base.DATA+511,2),(text.base.HEAP+31,2),(text.base.DATA+0x4000,1)):
            with self.assertRaisesRegex(ValueError,'write-range'):p.write(None,None,address,size,None,None)

    def test_closed_or_poisoned_hex_reference_stops_before_engine_access(self):
        p=hexbyte.CpuHexSprintfReference.__new__(hexbyte.CpuHexSprintfReference);p.closed=True;p.poisoned=False
        with self.assertRaisesRegex(ValueError,'closed'):p.format_byte(b' %02X',1)

    def test_cleanup_attempts_parent_even_when_child_fails(self):
        p=text.CpuTextFieldReference.__new__(text.CpuTextFieldReference);p.closed=False;p.write_hook=None
        class Child:
            def close(self):raise RuntimeError('injected')
        p.hex=Child()
        with patch.object(text.previous.CpuInputReference,'close') as close:
            with self.assertRaisesRegex(ValueError,'cleanup-failed'):p.close()
            close.assert_called_once_with()


if __name__=='__main__':unittest.main()
