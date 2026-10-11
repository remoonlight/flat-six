import collections
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
from scripts.diagnostics import x431_numeric_foundation as model
from scripts.diagnostics import x431_numeric_input_reference as caller


class NumericFoundationTests(unittest.TestCase):
    def test_default_import_has_no_engine_or_host_libc(self):
        r=subprocess.run([sys.executable,'-c',
            "import sys; from scripts.diagnostics import x431_numeric_input_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_wrong_originals_stop_before_engine_creation(self):
        with self.assertRaisesRegex(ValueError,'language-source-calc-unreviewed'):
            caller.CpuNumericInputReference(b'calc',b'std',b'deviceid',b'language')

    def test_integer_saturation_is_not_wrapping_overflow(self):
        cases=((b'2147483648',True,32,0x7fffffff),(b'-2147483649',True,32,0x80000000),
               (b'4294967296',False,32,0xffffffff),(b'-4294967296',False,32,0xffffffff),
               (b'9223372036854775808',True,64,0x7fffffffffffffff),
               (b'-9223372036854775809',True,64,0x8000000000000000),
               (b'18446744073709551616',False,64,0xffffffffffffffff))
        for data,signed,bits,value in cases:
            with self.subTest(data=data):
                r=model.integer_prefix(data,10,signed,bits);self.assertEqual(r['valueBits'],value)
                self.assertTrue(r['overflow']);self.assertEqual(r['sourceExpectedErrno'],'ERANGE')
                self.assertFalse(r['androidErrnoTlsModified'])

    def test_unsigned_minus_and_valid_limits_keep_source_semantics(self):
        cases=((b'-1',False,32,0xffffffff),(b'-4294967295',False,32,1),
               (b'-2147483648',True,32,0x80000000),(b'2147483647',True,32,0x7fffffff),
               (b'18446744073709551615',False,64,0xffffffffffffffff))
        for data,signed,bits,value in cases:
            r=model.integer_prefix(data,10,signed,bits);self.assertEqual(r['valueBits'],value)
            self.assertFalse(r['overflow']);self.assertIsNone(r['sourceExpectedErrno'])

    def test_explicit_long_integer_storage_consumes_full_source_mask(self):
        data=b'F'*8184
        result=model.integer_prefix(data,16,False,32,maximum_bytes=8192)
        self.assertEqual(result['valueBits'],0xffffffff)
        self.assertEqual(result['consumedBytes'],8184)
        self.assertTrue(result['overflow'])
        self.assertEqual(result['sourceExpectedErrno'],'ERANGE')
        self.assertFalse(result['androidErrnoTlsModified'])
        with self.assertRaises(ValueError):model.integer_prefix(data,16,False,32)
        with self.assertRaises(ValueError):model.decimal_prefix(b'1'*1025)
        for maximum in (True,8193,0,None):
            with self.assertRaises(ValueError):model.integer_prefix(b'1',10,False,32,maximum_bytes=maximum)
        with self.assertRaises(ValueError):model.integer_prefix(b'F'*8193,16,False,32,maximum_bytes=8192)

    def test_integer_prefix_end_offsets_and_no_digits(self):
        cases=((b'  +12rest',10,12,5),(b'0x',16,0,1),(b'0x10z',16,16,4),
               (b'-0x20!',16,0xffffffe0,5),(b'1012',2,5,3),(b'1.5',10,1,1),
               (b'  -abc',10,0,0),(b'',10,0,0))
        for data,radix,value,consumed in cases:
            r=model.integer_prefix(data,radix,False,32)
            self.assertEqual((r['valueBits'],r['consumedBytes']),(value,consumed))

    def test_exact_double_known_bits_prefix_and_incomplete_exponent(self):
        cases=((b'0',0,1),(b'-0',0x8000000000000000,2),(b'-abc',0,0),
               (b'  +1tail',0x3ff0000000000000,4),(b'7FFFFFFF',0x401c000000000000,1),
               (b'1e+',0x3ff0000000000000,1),(b'.25',0x3fd0000000000000,3),
               (b'0.1',0x3fb999999999999a,3),(b'1,5',0x3ff0000000000000,1))
        for data,bits,consumed in cases:
            r=model.decimal_prefix(data);actual=int.from_bytes(bytes.fromhex(r['returnedDoubleHex']),'little')
            self.assertEqual((actual,r['consumedBytes']),(bits,consumed))

    def test_exact_round_even_at_binary64_halfway_values(self):
        cases=((b'9007199254740993',0x4340000000000000),
               (b'9007199254740995',0x4340000000000002),
               (b'1.00000000000000011102230246251565404236316680908203125',0x3ff0000000000000),
               (b'1.00000000000000011102230246251565404236316680908203126',0x3ff0000000000001))
        for data,bits in cases:
            self.assertEqual(int.from_bytes(bytes.fromhex(model.decimal_prefix(data)['returnedDoubleHex']),'little'),bits)

    def test_special_hex_underflow_overflow_and_large_exponents_still_stop(self):
        for data in (b'inf',b'-infinity',b'nan(1)',b'0x1p2',b'0x',b'1e-308',b'1e309',b'0e4097'):
            with self.subTest(data=data),self.assertRaises(ValueError):model.decimal_prefix(data)
        for fpscr in (True,None,-1,1<<32,1<<22,1<<23):
            with self.assertRaisesRegex(ValueError,'rounding-mode-unreviewed'):model.decimal_prefix(b'1',fpscr)

    def test_both_models_require_bounded_ascii_without_embedded_nul(self):
        for data in (None,'1',bytearray(b'1'),b'1\0rest',b'\xff',b'1'*1025):
            for fn in (lambda:model.decimal_prefix(data),lambda:model.integer_prefix(data,10,False,32)):
                with self.assertRaisesRegex(ValueError,'bounded-ascii-required'):fn()
        for args in ((b'1',0,False,32),(b'1',True,False,32),(b'1',10,0,32),(b'1',10,False,16)):
            with self.assertRaisesRegex(ValueError,'integer-contract-unreviewed'):model.integer_prefix(*args)

    @staticmethod
    def fake_caller(name,values,data):
        p=caller.CpuNumericInputReference.__new__(caller.CpuNumericInputReference)
        p.hooks={100:name};p.numeric_events=[];p.trace=collections.Counter();p.cstr=lambda _:data
        class Cpu:
            writes=[]
            def reg_read(self,r):return values[r]
            def reg_write(self,r,v):self.writes.append((r,v))
        return p,Cpu()

    def test_caller_rejects_actual_end_pointer_and_conversion_budget(self):
        base=caller.original.base
        with patch.multiple(base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,create=True):
            p,u=self.fake_caller('strtoul',[123,456,10],b'1')
            with self.assertRaisesRegex(ValueError,'endptr-unreviewed'):p.code(u,100,2,None)
            p,u=self.fake_caller('strtoul',[123,0,10],b'1');p.numeric_events=[{}]*256
            with self.assertRaisesRegex(ValueError,'conversion-budget-exhausted'):p.code(u,100,2,None)
            for budget in (True,0,8193):
                p,u=self.fake_caller('strtoul',[123,0,10],b'1');p.numeric_conversion_budget=lambda:budget
                with self.assertRaisesRegex(ValueError,'conversion-budget-unreviewed'):p.code(u,100,2,None)

    def test_caller_unsupported_conversion_retains_explicit_foundation_boundary(self):
        base=caller.original.base
        with patch.multiple(base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,UC_ARM_REG_FPSCR=3,create=True):
            p,u=self.fake_caller('strtod',[123,0,10,0],b'1e309')
            with self.assertRaises(caller.original.FoundationBoundary) as e:p.code(u,100,2,None)
            self.assertFalse(e.exception.args[0]['conversionExecuted']);self.assertEqual(p.numeric_events,[])


if __name__=='__main__':unittest.main()
