import copy
import collections
import subprocess
import sys
import unittest
from unittest.mock import patch
from scripts.diagnostics import x431_fixed_input_reference as fixed
from scripts.diagnostics.x431_formula import parse_formula_text

FORMULA='IDENTICAL:Standar,BitMask=0,BaseDataType=A_BYTEFIELD,BitLength=16,Encoding=Undefined,HighLow=1,Radix=10,DataType=A_BYTEFIELD,Precision=null;'


def definition(formula=FORMULA):
    return dict(formula=formula,parsed=parse_formula_text(formula))


class FixedInputTests(unittest.TestCase):
    def test_cold_import_does_not_load_engine_or_vehicle_io(self):
        result=subprocess.run([sys.executable,'-B','-c',
            "import sys;from scripts.diagnostics import x431_fixed_input_reference;assert not {'unicorn','ctypes','serial'}&set(sys.modules)"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_edited_census_rejected_before_any_engine_construction(self):
        with patch.object(fixed.CpuNegateCallerReference,'__init__') as init:
            with self.assertRaisesRegex(ValueError,'census-unreviewed'):
                fixed.CpuFixedCallerReference(b'',b'',b'',b'',b'',{})
        init.assert_not_called()

    def test_invalid_originals_rejected_before_engine(self):
        with patch.object(fixed,'fixed_definitions',return_value=[definition()]),patch.object(fixed.CpuNegateCallerReference,'__init__') as init:
            with self.assertRaises(ValueError):
                fixed.CpuFixedCallerReference(b'',b'',b'',b'',b'',{})
        init.assert_not_called()

    def validate(self,formula=FORMULA,replies=None,original=bytes(4096),byte=4,bit=0,title=b'field'):
        with patch.object(fixed,'fixed_definitions',return_value=[definition()]):
            return fixed.validate_fixed(formula,replies if replies is not None else [dict(data=b'1234',accepted=True)],original,byte,bit,title,{})

    def test_unknown_or_modified_formula_is_not_accepted_as_a_family(self):
        self.validate()
        for formula in (FORMULA+' ',FORMULA.replace('BitLength=16','BitLength=8'),FORMULA.replace('BitMask=0','BitMask=1')):
            with self.subTest(formula=formula),self.assertRaisesRegex(ValueError,'exact-retained-formula'):
                self.validate(formula=formula)

    def test_reply_storage_includes_nul_and_explicit_cancel(self):
        self.validate(replies=[dict(data=b'',accepted=False)])
        self.validate(replies=[dict(data=b'A',accepted=True),dict(data=b'1234',accepted=True)])
        for replies in ([],[dict(data=b'1'*4096,accepted=True)],[dict(data=b'12\0',accepted=True)],
                [dict(data='12',accepted=True)],[dict(data=b'1234',accepted=1)],
                [dict(data=b'1234',accepted=True)]*4):
            with self.subTest(replies=str(replies)[:60]),self.assertRaises(ValueError):self.validate(replies=replies)

    def test_header_bit_window_and_title_stay_guarded(self):
        for kwargs in (dict(byte=1),dict(byte=True),dict(bit=1),dict(bit=True),dict(byte=4095),
                dict(original=bytes(511)),dict(title=b'field\0'),dict(title=b'0x1234')):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.validate(**kwargs)

    def test_unknown_byte_text_does_not_reach_inverse_path(self):
        for data in (b'GH',b'12 ',b'\xff'):
            with self.assertRaisesRegex(ValueError,'hex-character'):self.validate(replies=[dict(data=data,accepted=True)])

    def test_oversized_catalog_target_is_retained_as_cancel_only(self):
        large=definition(FORMULA.replace('BitLength=16','BitLength=32736'))
        with patch.object(fixed,'fixed_definitions',return_value=[definition(),large]):rows=list(fixed.matrix_cases({}))
        self.assertEqual(sum(row[1]=='full-input' for row in rows),1)
        large_rows=[row for row in rows if row[0]==large]
        self.assertEqual(len(large_rows),1)
        self.assertEqual(large_rows[0][1],'cancel')
        self.assertFalse(large_rows[0][4][0]['accepted'])

    def result(self,cancel=False):
        original=bytes(4096);after=bytearray(original)
        if not cancel:after[4:6]=bytes.fromhex('1234')
        return original,dict(completed=True,afterHex=after.hex().upper(),repliesConsumed=1,
            returnValue=0,chainEvents=[dict(va=0x247e6,inputInfoReturn=-1 if cancel else 4),
            dict(va=0x2483e,inverseInputInfoResultR3=-1 if cancel else 4),dict(va=0x24842,inverseReturn=1 if cancel else 2)])

    def test_comparison_detects_changes_outside_target_and_return_mismatch(self):
        original,result=self.result();replies=[dict(data=b'1234',accepted=True)]
        self.assertTrue(fixed.compare_case(definition(),original,4,replies,result)['fullReplacementCompared'])
        wrong=copy.deepcopy(result);wrong['afterHex']='FF'+wrong['afterHex'][2:]
        with self.assertRaises(ValueError):fixed.compare_case(definition(),original,4,replies,wrong)
        wrong=copy.deepcopy(result);wrong['chainEvents'][1]['inverseInputInfoResultR3']=4096
        with self.assertRaises(ValueError):fixed.compare_case(definition(),original,4,replies,wrong)

    def test_cancel_does_not_use_outer_zero_as_acceptance(self):
        original,result=self.result(True)
        self.assertTrue(fixed.compare_case(definition(),original,4,[dict(data=b'',accepted=False)],result)['canceled'])
        for key in ('executionEnabled','codingWriteQualified','codingRestoreQualified','vehicleVerified','fullFixedCatalogInputCoverage'):
            self.assertFalse(fixed.GUARDS[key])
        self.assertIsNone(fixed.GUARDS['writePayload'])

    def test_accepted_empty_is_distinct_from_explicit_cancel(self):
        original,result=self.result(True)
        result['chainEvents'][0]['inputInfoReturn']=0
        result['chainEvents'][1]['inverseInputInfoResultR3']=0
        comparison=fixed.compare_case(definition(),original,4,[dict(data=b'',accepted=True)],result)
        self.assertTrue(comparison['emptyAcceptedWithoutModification'])
        self.assertFalse(comparison['canceled'])
        self.assertFalse(comparison['fullReplacementCompared'])

    def test_long_mask_call_is_owned_exact_signed_conversion(self):
        class Cpu:
            def __init__(self):self.writes={};self.data=b'F'*8184+b'\0';self.lr=0x32eff
            def reg_read(self,r):return [1000,0,16,self.lr][r]
            def reg_write(self,r,v):self.writes[r]=v
            def mem_read(self,a,n):return self.data[:n]
        with patch.multiple(fixed.base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,
                UC_ARM_REG_LR=3,UC_ARM_REG_PC=4,create=True):
            p=object.__new__(fixed.CpuFixedCallerReference);p.hooks={100:'strtol'}
            p.fixed_width=4092;p.uint=lambda _:8184;p.allocations={988:8197}
            p.numeric_events=[];p.trace=collections.Counter();cpu=Cpu()
            p.code(cpu,100,2,None)
            self.assertEqual(cpu.writes,{0:0x7fffffff,4:0x32eff})
            self.assertEqual(p.numeric_events[0]['result']['consumedBytes'],8184)
            self.assertTrue(p.numeric_events[0]['result']['overflow'])
            p.allocations={}
            with self.assertRaisesRegex(ValueError,'mask-storage'):p.code(Cpu(),100,2,None)
            p.allocations={988:8197};cpu=Cpu();cpu.data=b'F'*8183+b'0\0'
            with self.assertRaisesRegex(ValueError,'mask-bytes'):p.code(cpu,100,2,None)
            p.hooks={100:'strtoul'};cpu=Cpu();cpu.lr=0x32f73;cpu.data=b'7'+b'F'*8183+b'\0'
            p.code(cpu,100,2,None)
            self.assertEqual(cpu.writes,{0:0xffffffff,4:0x32f73})
            self.assertFalse(p.numeric_events[-1]['result']['signed'])

    def test_default_and_fixed_execution_limits_are_separately_bounded(self):
        self.assertEqual(object.__new__(fixed.CpuNegateCallerReference).execution_limits(),(20,8_000_000))
        self.assertEqual(object.__new__(fixed.CpuFixedCallerReference).execution_limits(),(60,8_000_000))

    def test_invalid_execution_budget_stops_before_virtual_memory_access(self):
        p=object.__new__(fixed.CpuNegateCallerReference);p.validate_arguments=lambda *args:None
        p.closed=False;p.poisoned=False
        for limits in ((True,8_000_000),(61,8_000_000),(20,True),(20,8_000_001),(0,8_000_000)):
            p.execution_limits=lambda:limits
            with self.assertRaisesRegex(ValueError,'execution-budget-unreviewed'):
                p.call(FORMULA,[dict(data=b'1234',accepted=True)],bytes(4096))
