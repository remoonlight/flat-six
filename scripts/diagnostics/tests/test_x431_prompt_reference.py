import collections
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
from scripts.diagnostics import x431_integer_sprintf_reference as integer
from scripts.diagnostics import x431_language_reference as language
from scripts.diagnostics import x431_prompt_input_reference as caller


class PromptReferenceTests(unittest.TestCase):
    def test_import_never_loads_engine_or_host_elf(self):
        r=subprocess.run([sys.executable,'-c',
            "import sys; from scripts.diagnostics import x431_prompt_input_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_unreviewed_source_identity_stops_before_engine(self):
        for cls,args in ((integer.CpuIntegerSprintfReference,(b'calc',b'std',b'deviceid')),
                        (language.CpuLanguageReference,(b'calc',b'std',b'language')),
                        (caller.CpuPromptInputReference,(b'calc',b'std',b'deviceid',b'language'))):
            with self.subTest(cls=cls),self.assertRaisesRegex(ValueError,'source-calc-unreviewed'):
                cls(*args)

    def test_integer_argument_is_exact_raw_word(self):
        for value in (0,1,0x7fffffff,0x80000000,0xffffffff):
            for fmt in (b'%d',b'%u'):
                self.assertEqual(integer.validate_integer_format(fmt,struct.pack('<I',value)),value)
        for data in (None,b'',bytes(3),bytes(8),bytearray(4)):
            with self.assertRaisesRegex(ValueError,'word-bytes-required'):
                integer.validate_integer_format(b'%d',data)

    def test_unknown_formats_and_rounding_modes_rejected(self):
        for fmt in ('%d',b'%x',b'%s',b'%n',b'%d%d',b'%10d',b'%d\0',b'label=%u'):
            with self.assertRaisesRegex(ValueError,'format-unreviewed'):
                integer.validate_integer_format(fmt,bytes(4))
        for fpscr in (True,None,-1,1<<32,1<<22,1<<23):
            with self.assertRaisesRegex(ValueError,'rounding-mode-unreviewed'):
                integer.validate_integer_format(b'%u',bytes(4),fpscr)

    def test_integer_closed_or_poisoned_context_rejected(self):
        p=integer.CpuIntegerSprintfReference.__new__(integer.CpuIntegerSprintfReference)
        for closed,poisoned in ((True,False),(False,True)):
            p.closed,p.poisoned=closed,poisoned
            with self.assertRaisesRegex(ValueError,'prior-outcome-unknown-or-closed'):
                p.format_integer(b'%u',bytes(4))

    def test_integer_original_division_rejects_zero_or_other_radix(self):
        p=integer.CpuIntegerSprintfReference.__new__(integer.CpuIntegerSprintfReference)
        class Registers:UC_ARM_REG_R1=1
        class Cpu:
            def reg_read(self,_):return divisor
        p.r=Registers
        for divisor in (0,1,2,16,0xffffffff):
            with self.assertRaisesRegex(ValueError,'divisor-unreviewed'):
                p.code(Cpu(),0xc6ec,4,None)

    def test_language_writes_cannot_touch_context_secret_file_or_unowned_heap(self):
        p=language.CpuLanguageReference.__new__(language.CpuLanguageReference);p.allocations={language.HEAP:4}
        for address,size in ((language.cpu.DATA,200),(language.cpu.DATA+199,1),
                             (language.cpu.STACK,1024),(language.HEAP,4)):
            self.assertTrue(p._writable(address,size))
        for address,size in ((language.cpu.DATA+199,2),(language.CTX,4),(language.SEC,1),
            (language.HEAP+3,2),(language.HEAP+16,1),(language.cpu.STACK+0x100000-1,2),
            (0,4),(language.cpu.DATA,1025),(language.cpu.DATA,-1)):
            self.assertFalse(p._writable(address,size))
            with self.assertRaisesRegex(ValueError,'write-range-unreviewed'):
                p.write(None,None,address,size,None,None)

    @staticmethod
    def fake_language(name,values):
        p=language.CpuLanguageReference.__new__(language.CpuLanguageReference)
        p.hooks={100:name};p.trace=collections.Counter();p.allocations={};p.opened=False
        p.section={'offset':100,'bytes':5};p.cursor=100;p.file_bytes=b'0'*100+b'abcde';p.reads=[]
        class Registers:
            UC_ARM_REG_R0=0;UC_ARM_REG_R1=1;UC_ARM_REG_R2=2;UC_ARM_REG_R3=3
            UC_ARM_REG_PC=4;UC_ARM_REG_LR=5
        class Cpu:
            writes=[]
            def reg_read(self,r):return values[r] if r<4 else 400
            def reg_write(self,r,v):self.writes.append((r,v))
        p.r=Registers
        return p,Cpu()

    def test_language_open_and_close_require_exact_owned_state(self):
        for pointer in (0,language.CTX,974347):
            p,u=self.fake_language('retained-language-open-model',[pointer,0,0,0])
            with self.assertRaisesRegex(ValueError,'open-state-unreviewed'):p.code(u,100,4,None)
        p,u=self.fake_language('retained-language-open-model',[974346,0,0,0])
        p.code(u,100,4,None);self.assertTrue(p.opened)
        with self.assertRaisesRegex(ValueError,'open-state-unreviewed'):p.code(u,100,4,None)
        p,u=self.fake_language('retained-language-close-model',[language.CTX,0,0,0])
        with self.assertRaisesRegex(ValueError,'close-state-unreviewed'):p.code(u,100,4,None)
        p.opened=True;p.allocations={language.HEAP:4}
        with self.assertRaisesRegex(ValueError,'close-state-unreviewed'):p.code(u,100,4,None)
        p.allocations.clear();p.code(u,100,4,None);self.assertFalse(p.opened)

    def test_virtual_file_read_is_bounded_and_never_uses_host_file_api(self):
        p,u=self.fake_language('fread',[language.cpu.DATA,1,3,language.FILE_TOKEN]);p.opened=True
        with patch.object(p,'_put') as put:
            p.code(u,100,4,None);put.assert_called_once_with(language.cpu.DATA,b'abc')
        self.assertEqual(p.cursor,103);self.assertEqual(p.reads,[{'offset':100,'bytes':3}])
        p.cursor=99
        with self.assertRaisesRegex(ValueError,'read-unreviewed'):p.code(u,100,4,None)
        for values in ([0,1,6,language.FILE_TOKEN],[0,201,1,language.FILE_TOKEN],
                       [0,1,1,0],[0,1,0,language.FILE_TOKEN]):
            p,u=self.fake_language('fread',values);p.opened=True
            with self.assertRaisesRegex(ValueError,'read-unreviewed'):p.code(u,100,4,None)

    def test_virtual_seek_rejects_wrong_handle_mode_and_outside_text_section(self):
        for values in ([0,100,0,0],[language.FILE_TOKEN,99,0,0],
                       [language.FILE_TOKEN,106,0,0],[language.FILE_TOKEN,100,1,0]):
            p,u=self.fake_language('fseek',values);p.opened=True
            with self.assertRaisesRegex(ValueError,'seek-unreviewed'):p.code(u,100,4,None)

    def test_language_unknown_imports_stop(self):
        for name in ('fopen','socket','strtok','__android_log_print','sprintf'):
            p,u=self.fake_language(name,[0,0,0,0])
            with self.assertRaisesRegex(ValueError,'import-unreviewed'):p.code(u,100,4,None)

    def test_language_unknown_id_capacity_closed_and_poisoned_rejected(self):
        p=language.CpuLanguageReference.__new__(language.CpuLanguageReference)
        p.prompts={1:{}}
        for id_u32,capacity in ((True,200),(2,200),(1,199),(1,True)):
            with self.assertRaisesRegex(ValueError,'prompt-or-capacity-unreviewed'):p.lookup(id_u32,capacity)
        for closed,poisoned in ((True,False),(False,True)):
            p.closed,p.poisoned=closed,poisoned
            with self.assertRaisesRegex(ValueError,'prior-outcome-unknown-or-closed'):p.lookup(1)

    @staticmethod
    def fake_caller(address,values,name):
        p=caller.CpuPromptInputReference.__new__(caller.CpuPromptInputReference)
        p.callback_sites={address:{'objectVa':123,'name':name}};p.uint=lambda _:0
        p.pending_prompt=None
        class Cpu:
            def reg_read(self,reg):return values[reg]
        return p,Cpu()

    def test_caller_language_requires_matching_id_capacity_and_stack_destination(self):
        sp=language.cpu.STACK+0x80000;at=0x342c8;base=caller.previous.original.base
        for values in ([0,sp+0x108,200,0,sp],[caller.IDS[at],sp+0x108,199,0,sp],
                       [caller.IDS[at],sp+0x109,200,0,sp]):
            p,u=self.fake_caller(at,values,'GetTextFromTextLib')
            with patch.multiple(base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,
                               UC_ARM_REG_R3=3,UC_ARM_REG_SP=4,create=True), \
                    self.assertRaisesRegex(ValueError,'language-arguments-unreviewed'):
                p.code(u,at,2,None)

    def test_caller_format_requires_preceding_matching_language_call(self):
        sp=language.cpu.STACK+0x80000;at=0x342da;base=caller.previous.original.base
        p,u=self.fake_caller(at,[sp+0xa0,sp+0x108,1,0,sp],'SPRINTF')
        with patch.multiple(base,UC_ARM_REG_R0=0,UC_ARM_REG_R1=1,UC_ARM_REG_R2=2,
                           UC_ARM_REG_R3=3,UC_ARM_REG_SP=4,create=True):
            with self.assertRaisesRegex(ValueError,'format-pair-or-destination-unreviewed'):p.code(u,at,2,None)
            p.pending_prompt=(at,sp+0x108,b'original');p.cstr=lambda _:b'changed'
            with self.assertRaisesRegex(ValueError,'language-format-changed'):p.code(u,at,2,None)

    def test_caller_cleanup_attempts_every_owned_engine_on_failure(self):
        p=caller.CpuPromptInputReference.__new__(caller.CpuPromptInputReference);closed=[]
        class Engine:
            def __init__(self,name):self.name=name
            def close(self):
                closed.append(self.name)
                if self.name=='integer':raise ValueError('injected')
        p.integer=Engine('integer');p.language=Engine('language')
        with patch.object(caller.previous.CpuInputCallbacksReference,'close',side_effect=lambda:closed.append('parent')), \
                self.assertRaisesRegex(ValueError,'engine-cleanup-failed'):p.close()
        self.assertEqual(closed,['integer','language','parent'])


if __name__=='__main__':unittest.main()
