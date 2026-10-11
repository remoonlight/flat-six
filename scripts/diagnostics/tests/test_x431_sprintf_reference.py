import collections
import struct
import subprocess
import sys
import unittest
import weakref
from unittest.mock import patch
from scripts.diagnostics import x431_sprintf_reference as reference
from scripts.diagnostics import x431_input_callbacks_reference as caller


class SprintfReferenceTests(unittest.TestCase):
    def test_default_import_has_no_cpu_or_host_library_dependency(self):
        r=subprocess.run([sys.executable,'-c',
            "import sys; from scripts.diagnostics import x431_input_callbacks_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_unreviewed_sources_stop_before_engine_construction(self):
        for cls in (reference.CpuSprintfReference,caller.CpuInputCallbacksReference):
            with self.assertRaisesRegex(ValueError,'callback-source-calc-unreviewed'):
                cls(b'calc',b'std',b'deviceid')

    def test_only_three_exact_formats_are_callable(self):
        for fmt in reference.FORMATS:
            self.assertEqual(reference.validate_format(fmt,struct.pack('<d',1.25)),1.25)
        for fmt in ('%.1f',b'%f',b'%.2f',b'%s',b'%n',b'%.3f\0',b'%9999999f'):
            with self.subTest(fmt=fmt),self.assertRaisesRegex(ValueError,'format-unreviewed'):
                reference.validate_format(fmt,struct.pack('<d',1.25))

    def test_special_and_unbounded_floats_never_reach_native_code(self):
        for value in (float('inf'),float('-inf'),float('nan'),1000000.01,-1000000.01):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'finite-domain-unreviewed'):
                reference.validate_format(b'%.3f',struct.pack('<d',value))
        for value in (None,b'',bytes(7),bytes(9),bytearray(8)):
            with self.assertRaisesRegex(ValueError,'double-bytes-invalid'):
                reference.validate_format(b'%.3f',value)

    def test_only_reviewed_rounding_mode_is_admitted(self):
        for value in (True,None,-1,1<<32,1<<22,1<<23,3<<22):
            with self.assertRaisesRegex(ValueError,'rounding-mode-unreviewed'):
                reference.validate_format(b'%.0f',bytes(8),value)
        self.assertEqual(reference.validate_format(b'%.0f',struct.pack('<d',-0.0),0),-0.0)

    def test_failed_or_closed_context_cannot_continue(self):
        p=reference.CpuSprintfReference.__new__(reference.CpuSprintfReference)
        for closed,poisoned in ((True,False),(False,True)):
            p.closed,p.poisoned=closed,poisoned
            with self.assertRaisesRegex(ValueError,'prior-outcome-unknown-or-closed'):
                p.format(b'%.0f',bytes(8))

    def test_original_writes_are_limited_to_stack_and_fifty_destination_bytes(self):
        p=reference.CpuSprintfReference.__new__(reference.CpuSprintfReference)
        for address,size in ((reference.DATA,50),(reference.DATA+49,1),(reference.STACK,4)):
            p.write(None,None,address,size,None,None)
        for address,size in ((reference.DATA+49,2),(reference.DATA-1,2),
                             (reference.STACK+0x100000-1,2),(reference.IMPORTS,1)):
            with self.assertRaisesRegex(ValueError,'write-range-unreviewed'):
                p.write(None,None,address,size,None,None)

    @staticmethod
    def fake_probe(name,values):
        p=reference.CpuSprintfReference.__new__(reference.CpuSprintfReference)
        p.hooks={100:name};p.trace=collections.Counter();p.sink=bytearray();p.imports={'__sF':200}
        class Registers:
            UC_ARM_REG_R0=0;UC_ARM_REG_R1=1;UC_ARM_REG_R2=2;UC_ARM_REG_R3=3
            UC_ARM_REG_PC=4;UC_ARM_REG_LR=5
        class Cpu:
            def reg_read(self,reg):return values[reg] if reg<4 else 400
            def reg_write(self,reg,value):pass
        p.r=Registers
        return p,Cpu()

    def test_putc_is_captured_and_never_forwarded_to_host_stdio(self):
        p,u=self.fake_probe('putc',[65,284,0,0]);p.code(u,100,2,None)
        self.assertEqual(p.sink,b'A')
        p,u=self.fake_probe('putc',[65,285,0,0])
        with self.assertRaisesRegex(ValueError,'stdio-unreviewed'):p.code(u,100,2,None)
        self.assertEqual(p.sink,b'')

    def test_unknown_import_cannot_reach_host_or_driver(self):
        for name in ('socket','fwrite','sprintf','__stack_chk_fail'):
            p,u=self.fake_probe(name,[0,0,0,0])
            with self.assertRaisesRegex(ValueError,'import-unreviewed'):p.code(u,100,2,None)

    def test_foundation_memory_call_cannot_write_source_or_destination(self):
        p,u=self.fake_probe('__aeabi_memclr8',[reference.DATA,32,0,0])
        with self.assertRaisesRegex(ValueError,'memory-unreviewed'):p.code(u,100,2,None)

    def test_original_execution_time_budget_is_enforced(self):
        p=reference.CpuSprintfReference.__new__(reference.CpuSprintfReference)
        p.r=None;p.hooks={};p.allowed=[(1,3)];p.trace=collections.Counter(originalInstructions=1023);p.deadline=0
        with self.assertRaisesRegex(ValueError,'wall-budget-exhausted'):p.code(None,1,2,None)

    def test_dispose_both_hooks_and_engine_exactly_once(self):
        disposed=[];deleted=[]
        class Token:pass
        token=Token()
        class Cpu:
            _Uc__finalizer=weakref.finalize(token,lambda:disposed.append(True))
            def hook_del(self,hook):deleted.append(hook)
        p=reference.CpuSprintfReference.__new__(reference.CpuSprintfReference)
        p.u=Cpu();p.closed=False;p.code_hook=11;p.write_hook=12
        retained=p.u;p.close();p.close()
        self.assertEqual(deleted,[11,12]);self.assertEqual(disposed,[True])
        self.assertFalse(retained._Uc__finalizer.alive);self.assertIsNone(retained._uch)

    def test_hook_cleanup_failure_still_disposes_engine_and_reports_failure(self):
        disposed=[];deleted=[]
        class Token:pass
        token=Token()
        class Cpu:
            _Uc__finalizer=weakref.finalize(token,lambda:disposed.append(True))
            def hook_del(self,hook):
                deleted.append(hook)
                if hook==11:raise RuntimeError('injected')
        p=reference.CpuSprintfReference.__new__(reference.CpuSprintfReference)
        p.u=Cpu();p.closed=False;p.code_hook=11;p.write_hook=12
        with self.assertRaisesRegex(ValueError,'hook-cleanup-failed'):p.close()
        self.assertEqual(deleted,[11,12]);self.assertEqual(disposed,[True])
        self.assertTrue(p.closed);self.assertTrue(p.poisoned)

    def test_input_callout_rejects_wrong_destination_before_child_execution(self):
        p=caller.CpuInputCallbacksReference.__new__(caller.CpuInputCallbacksReference)
        p.callback_sites={0x31946:{'name':'SPRINTF','objectVa':12}}
        p.uint=lambda _:0;p.cstr=lambda _:b'%.0f'
        class Cpu:
            def reg_read(self,reg):return [999,100,0,0,1000][reg]
        base=caller.original.base
        with patch.multiple(base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,
                            UC_ARM_REG_R3=3,UC_ARM_REG_SP=4,create=True):
            with self.assertRaisesRegex(ValueError,'destination-unreviewed'):
                p.code(Cpu(),0x31946,2,None)

    def test_input_callout_requires_observed_float_conversion_instructions(self):
        p=caller.CpuInputCallbacksReference.__new__(caller.CpuInputCallbacksReference)
        p.callback_sites={0x31946:{'name':'SPRINTF','objectVa':12}}
        p.uint=lambda _:0;p.cstr=lambda _:b'%.0f';p.float_roundtrip_seen=[]
        sp=reference.STACK+0x80000
        class Cpu:
            def reg_read(self,reg):return [sp+0xb8,100,0,0,sp][reg]
        base=caller.original.base
        with patch.multiple(base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,
                            UC_ARM_REG_R3=3,UC_ARM_REG_SP=4,create=True):
            with self.assertRaisesRegex(ValueError,'float-conversions-not-observed'):
                p.code(Cpu(),0x31946,2,None)


if __name__=='__main__':unittest.main()
