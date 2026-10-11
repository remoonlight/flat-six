"""Synthetic memory/API rejection tests; real image checks run separately."""
import ctypes as c
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import pt3g_binding as binding
from scripts.diagnostics.elm import ElmError


def image_fixture():
    raw=bytearray(0x2400);raw[:2]=b'MZ';struct.pack_into('<I',raw,0x3c,0x40);raw[0x40:0x44]=b'PE\0\0'
    struct.pack_into('<HH',raw,0x44,0x8664,1);struct.pack_into('<H',raw,0x54,120)
    struct.pack_into('<H',raw,0x58,0x20b)
    struct.pack_into('<II',raw,0x58+112,0x120100,0x100)
    struct.pack_into('<IIII',raw,0xd0+8,0x2000,0x120000,0x2000,0x200)
    struct.pack_into('<IIHHIIIIIII',raw,0x300,0,0,0,0,0,1,1,1,0x120220,0x120230,0x120240)
    struct.pack_into('<I',raw,0x420,0x121000);struct.pack_into('<I',raw,0x430,0x120300)
    struct.pack_into('<H',raw,0x440,0);raw[0x500:0x50e]=b'PDUGetVersion\0'
    return raw


def memory_info(**updates):
    info=binding._MemoryInfo()
    for name,value in ({'base':0x1000,'allocation':0x1000,'size':0x1000,'state':0x1000,
                       'protect':0x20,'type':0x1000000}|updates).items():
        setattr(info,name,value)
    return info


class FakeMemory:
    wrapper_base=0x100000;owner=0x400000;context=0x800000;table=0x900000
    wrong_slot=False;changed=False;reads=0
    def __init__(self):
        self.source={'bindings':[dict(name=name,exportRva=0x1000+8*i,slot=8*i)
            for i,name in enumerate(binding.SLOT_NAMES,1)]}
        values=[self.owner]+[self.owner+r['exportRva'] for r in self.source['bindings']]
        self.raw=struct.pack('<30Q',*values)
    def verify_image(self,base,path):
        if base not in (self.wrapper_base,self.owner):raise ElmError('image-owner-invalid')
    def info(self,address):
        return memory_info(base=address,allocation=self.owner)
    def read(self,address,size,**_):
        if address==self.wrapper_base+binding.CONTEXT_RVA:return struct.pack('<Q',self.context)
        if address==self.context+binding.TABLE_OFFSET:return struct.pack('<Q',self.table)
        if address==self.context+binding.HANDLE_OFFSET:return struct.pack('<I',1)
        if address==self.table:
            self.reads+=1
            raw=bytearray(self.raw)
            if self.wrong_slot or self.changed and self.reads>1:raw[0x38]^=1
            return bytes(raw)
        return b'A'*size


class Pt3gBindingTests(unittest.TestCase):
    def test_memory_ABI_matches_windows_x64_layout(self):
        self.assertEqual(c.sizeof(binding._MemoryInfo),48)
        expected={'base':0,'allocation':8,'allocationProtect':16,'partition':20,
                  'size':24,'state':32,'protect':36,'type':40}
        for name,offset in expected.items():self.assertEqual(getattr(binding._MemoryInfo,name).offset,offset)

    def test_source_guard_precedes_PE_or_native_loading(self):
        with patch.object(binding,'_exports') as parse:
            with self.assertRaisesRegex(ValueError,'source-unreviewed'):
                binding.audit_binding_source(b'bad',b'bad')
        parse.assert_not_called()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'PDU_VCI.dll').write_bytes(b'bad');(root/'pdu2.dll').write_bytes(b'bad')
            with patch.object(binding,'_MetadataProbe') as native:
                with self.assertRaisesRegex(ValueError,'source-unreviewed'):binding.probe_driver_binding(root)
            native.assert_not_called()

    def test_PE_export_names_resolve_RVAs_and_reject_forwarders_bad_ordinals(self):
        raw=image_fixture();self.assertEqual(binding._exports(bytes(raw)),{'PDUGetVersion':0x121000})
        for offset,format,value in ((0x420,'<I',0x120110),(0x420,'<I',0),(0x440,'<H',1),
                                    (0x318,'<I',129),(0x500,'<B',255)):
            bad=raw.copy();struct.pack_into(format,bad,offset,value)
            with self.assertRaises((ValueError,UnicodeError)):binding._exports(bytes(bad))

    def test_uncommitted_guard_execute_only_cross_region_and_foreign_image_rejected(self):
        binding._readable_region(0x1000,8,memory_info(),image=0x1000,executable=True)
        for info in (memory_info(state=0x2000),memory_info(protect=0x120),memory_info(protect=0x10),
                     memory_info(protect=1),memory_info(allocation=0x2000),memory_info(type=0x20000)):
            with self.assertRaises(ElmError):binding._readable_region(0x1000,8,info,image=0x1000)
        for address,size in ((0,1),(True,1),(0x1fff,2),(0x1000,4097),(2**64-1,2)):
            with self.assertRaises(ElmError):binding._readable_region(address,size,memory_info())
        with self.assertRaises(ElmError):binding._readable_region(0x1000,8,memory_info(protect=4),executable=True)

    def test_owned_read_rejects_OS_failure_and_partial_read(self):
        class Kernel:
            count=8;success=True
            def ReadProcessMemory(self,handle,address,buffer,size,count):
                self.handle=handle;c.cast(count,c.POINTER(c.c_size_t)).contents.value=self.count
                return self.success
        reader=binding._OwnedMemory.__new__(binding._OwnedMemory);reader.process=-1;reader.kernel=Kernel()
        reader.info=lambda _:memory_info()
        self.assertEqual(reader.read(0x1000,8),bytes(8));self.assertEqual(reader.kernel.handle,-1)
        for success,count in ((False,8),(True,7)):
            reader.kernel.success=success;reader.kernel.count=count
            with self.assertRaisesRegex(ElmError,'memory-read-failed'):reader.read(0x1000,8)

    def test_callback_mismatch_and_changing_table_stop_before_any_delegation(self):
        native=type('Native',(),{'dll':type('Dll',(),{'_handle':FakeMemory.wrapper_base})()})()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'PDU_VCI.dll').write_bytes(b'fixture');(root/'pdu2.dll').write_bytes(b'fixture')
            with patch.object(binding,'_pe_raw',side_effect=lambda _,rva,size:b'A'*size):
                memory=FakeMemory();report,target=binding._observe(native,memory,root,memory.source)
                self.assertEqual(target,memory.owner+memory.source['bindings'][6]['exportRva'])
                self.assertEqual(len(report['bindings']),29)
                for flag,reason in (('wrong_slot','callback-mismatch'),('changed','state-changed')):
                    memory=FakeMemory();setattr(memory,flag,True)
                    with self.assertRaisesRegex(ElmError,reason):binding._observe(native,memory,root,memory.source)

    def test_binding_failure_never_calls_version_and_always_closes(self):
        class Native:
            configuration={};calls=[];closed=False
            def call(self,*args):self.calls.append(args[0])
            def close(self):self.closed=True;return {'errors':[]}
        native=Native()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'PDU_VCI.dll').write_bytes(b'x');(root/'pdu2.dll').write_bytes(b'x')
            with patch.object(binding,'audit_binding_source',return_value={}),patch.object(binding,'_MetadataProbe',return_value=native),\
                 patch.object(binding,'_OwnedMemory'),patch.object(binding,'_observe',side_effect=ElmError('binding-failed')):
                report=binding.probe_driver_binding(root)
        self.assertFalse(report['ok']);self.assertEqual(report['error'],'binding-failed')
        self.assertEqual(native.calls,[]);self.assertTrue(native.closed)

    def test_sequential_metadata_error_and_cleanup_failure_never_qualify_head(self):
        class Native:
            configuration={};cleanup=[];closed=0
            def call(self,name,handle,pointer):
                self.asserted=(name,handle)
            def close(self):self.closed+=1;return {'errors':self.cleanup}
        native=Native();delegate_calls=[]
        def factory(*types):
            def bind(target):
                def invoke(handle,data):delegate_calls.append((target,handle));return 0x60
                return invoke
            return bind
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'PDU_VCI.dll').write_bytes(b'x');(root/'pdu2.dll').write_bytes(b'x')
            with patch.object(binding,'audit_binding_source',return_value={}),patch.object(binding,'_MetadataProbe',return_value=native),\
                 patch.object(binding,'_OwnedMemory'),patch.object(binding,'_observe',return_value=({'bound':True},0x1234)),\
                 patch.object(binding.c,'WINFUNCTYPE',factory,create=True):
                for cleanup in ([],['destruct-failed']):
                    native.cleanup=cleanup;report=binding.probe_driver_binding(root)
                    self.assertEqual(report['ok'],not cleanup)
                    self.assertEqual(native.asserted,('PDUGetVersion',binding.UNDEF))
                    self.assertEqual(report['delegateVersion']['status'],0x60)
                    self.assertTrue(report['sequentialVersionStatusesDiffer'])
                    self.assertFalse(report['delegateReturnInsideWrapperObserved'])
                    self.assertFalse(report['versionMetadataQualified'])
                    self.assertEqual({k:report[k] for k in binding.GUARDS},binding.GUARDS)
        self.assertEqual(delegate_calls,[(0x1234,binding.UNDEF)]*2);self.assertEqual(native.closed,2)


if __name__=='__main__':unittest.main()
