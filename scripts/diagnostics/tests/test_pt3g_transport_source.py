"""Pinned sources and bounded synthetic own-process observations; no device I/O."""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import pt3g_transport_source as transport
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.tests.test_pt3g_binding import image_fixture, memory_info


class FakeMemory:
    base=0x100000;owner=0x400000;context=0x800000;table=0x900000;begin=0xa00000
    def __init__(self):
        self.source=dict(bindings=[dict(name=name,exportRva=0x1000+8*i,prologueBytes=32,
            prologueSha256=hashlib.sha256(b'A'*32).hexdigest()) for i,name in enumerate(transport.BINDING_NAMES)],
            routes=[dict(route=key,index=index,sourceId=i+1) for i,(key,index,_) in enumerate(transport.CAN_ROUTES)],
            canVtableTargets=[0x1000+i*8 for i in range(15)],regions=[])
        self.raw=struct.pack('<25Q',self.owner,*[self.owner+r['exportRva'] for r in self.source['bindings']])
        ids=[0xfffffffe]*55
        for row in self.source['routes']:ids[row['index']]=row['sourceId']
        self.protocols=struct.pack('<55I',*ids)
        self.header=struct.pack('<3Q',self.begin,self.begin+220,self.begin+256)
        self.vtable=struct.pack('<15Q',*[self.base+r for r in self.source['canVtableTargets']])
        self.reads={};self.change_at=None;self.bad_image=False;self.foreign_executable=False;self.code=b'A'*32
    def verify_image(self,base,path):
        if self.bad_image:raise ElmError('image-path-hash-mismatch')
        if base not in (self.base,self.owner):raise ElmError('wrong-owner')
    def info(self,address):
        return memory_info(base=address,allocation=self.owner if self.foreign_executable else self.base)
    def read(self,address,size,**_):
        self.reads[address]=self.reads.get(address,0)+1
        values={self.base+transport.CONTEXT_RVA:struct.pack('<Q',self.context),
                self.context+transport.PASSTHRU_OFFSET:struct.pack('<Q',self.table),
                self.table:self.raw,self.context+transport.PROTOCOL_OFFSET:self.header,
                self.begin:self.protocols,self.base+transport.CAN_VTABLE_RVA:self.vtable}
        raw=values.get(address,self.code)
        if address==self.change_at and self.reads[address]>1:raw=bytes([raw[0]^1])+raw[1:]
        return raw


class Pt3gTransportSourceTests(unittest.TestCase):
    def test_full_pinned_source_matches_three_CAN_routes_and_24_exports(self):
        default=Path(transport.__file__).resolve().parents[2]/'archives/migration-20261008/flat-six/.local/pt3g-support/vendor/PORSCHE-VCI/X64'
        root=Path(os.environ.get('PORSCHE981_PT3G_TEST_SOURCE') or default)
        source=transport.audit_transport_source(root)
        self.assertEqual(len(source['bindings']),24);self.assertEqual(len(source['regions']),20)
        self.assertEqual([r['index'] for r in source['routes']],[6,12,13])
        self.assertEqual([r['sourceId'] for r in source['routes']],[1,2,10])
        self.assertEqual([r['factoryClass'] for r in source['routes']],[5,5,5])
        self.assertFalse(source['canDisconnectLowerStatusPropagated'])
        self.assertFalse(source['cancelLowerStatusPropagated'])
        self.assertFalse(source['executionEnabled']);self.assertEqual(source['ecuRequestsSent'],0)

    def test_unreviewed_files_stop_before_driver_construction(self):
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);(root/'PDU_VCI.dll').write_bytes(b'bad')
            with patch.object(transport,'_TransportMetadataProbe') as native:
                with self.assertRaisesRegex(ValueError,'source-unreviewed'):transport.probe_transport_metadata(root)
            native.assert_not_called()

    def test_export_parser_rejects_forwarders_bad_indices_and_large_counts(self):
        raw=image_fixture();self.assertEqual(transport._exports(bytes(raw)),{'PDUGetVersion':0x121000})
        for offset,fmt,value in ((0x420,'<I',0x120110),(0x420,'<I',0),(0x440,'<H',1),
                                 (0x318,'<I',65),(0x500,'<B',255)):
            bad=raw.copy();struct.pack_into(fmt,bad,offset,value)
            with self.assertRaises((ValueError,UnicodeError)):transport._exports(bytes(bad))

    def test_independent_storage_passes_without_native_calls(self):
        memory=FakeMemory();report=transport._snapshot(memory,memory.base,Path('.'),memory.source)
        self.assertEqual(report['bindingsVerified'],24);self.assertEqual(report['protocolIdsVerified'],3)
        self.assertFalse(report['nativeProtocolObjectObserved'])

    def test_pointer_owner_instruction_and_vtable_drift_stop(self):
        for failure in ('pointer','owner','prologue','vtable','foreign'):
            memory=FakeMemory()
            if failure=='pointer':memory.raw=memory.raw[:8]+struct.pack('<Q',memory.owner+999)+memory.raw[16:]
            if failure=='owner':memory.bad_image=True
            if failure=='prologue':memory.code=b'B'*32
            if failure=='vtable':memory.vtable=memory.vtable[:8]+struct.pack('<Q',memory.base+999)+memory.vtable[16:]
            if failure=='foreign':memory.foreign_executable=True
            with self.subTest(failure=failure),self.assertRaises(ElmError):
                transport._snapshot(memory,memory.base,Path('.'),memory.source)

    def test_vector_extent_capacity_alignment_and_route_id_are_checked(self):
        for failure in ('short','capacity','alignment','id'):
            memory=FakeMemory()
            if failure=='short':memory.header=struct.pack('<3Q',memory.begin,memory.begin+216,memory.begin+256)
            if failure=='capacity':memory.header=struct.pack('<3Q',memory.begin,memory.begin+220,memory.begin+1024)
            if failure=='alignment':memory.header=struct.pack('<3Q',memory.begin+1,memory.begin+221,memory.begin+256)
            if failure=='id':memory.protocols=memory.protocols[:24]+struct.pack('<I',999)+memory.protocols[28:]
            with self.subTest(failure=failure),self.assertRaises(ElmError):
                transport._snapshot(memory,memory.base,Path('.'),memory.source)

    def test_every_snapshot_state_change_is_rejected(self):
        for target in ('context','pointer','table','header','ids','vtable'):
            memory=FakeMemory();memory.change_at={'context':memory.base+transport.CONTEXT_RVA,
                'pointer':memory.context+transport.PASSTHRU_OFFSET,'table':memory.table,
                'header':memory.context+transport.PROTOCOL_OFFSET,'ids':memory.begin,
                'vtable':memory.base+transport.CAN_VTABLE_RVA}[target]
            with self.subTest(target=target),self.assertRaisesRegex(ElmError,'state-changed'):
                transport._snapshot(memory,memory.base,Path('.'),memory.source)

    def test_observation_error_and_cleanup_error_never_qualify(self):
        class Native:
            dll=type('DLL',(),{'_handle':0x100000})()
            def __init__(self,_):self.closed=False
            def close(self):self.closed=True;return {'errors':['failed-cleanup']}
        for problem in (None,ElmError('pointer-drift')):
            with patch.object(transport,'audit_transport_source',return_value={}),patch.object(transport,'_TransportMetadataProbe',Native),patch.object(transport,'_OwnedMemory'),patch.object(transport,'_snapshot',side_effect=problem,return_value={}):
                report=transport.probe_transport_metadata(Path('.'))
            self.assertFalse(report['ok']);self.assertFalse(report['executionEnabled'])
            self.assertEqual(report['directPassthruCalls'],0)
            if problem:self.assertEqual(report['error'],'pointer-drift')

    def test_native_probe_rejects_module_link_and_primitive_apis(self):
        native=transport._TransportMetadataProbe.__new__(transport._TransportMetadataProbe)
        for name in ('PDUModuleConnect','PDUCreateComLogicalLink','PDUConnect','PDUStartComPrimitive',
                     'PDUCancelComPrimitive','PDUGetEventItem','PDUSetComParam','PDUGetVersion'):
            with self.assertRaisesRegex(ElmError,'metadata-only'):native.call(name)


if __name__=='__main__':unittest.main()
