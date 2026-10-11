"""ABI and provenance checks with synthetic buffers; no driver/device I/O."""
import ctypes as c
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from scripts.diagnostics import pt3g_version as version
from scripts.diagnostics.elm import ElmError


def data_buffer():
    data=version.VersionData()
    for name,text in (('hardwareName',b'VAS6154A'),('firmwareName',b'VAS6154A PFW'),
                      ('vendorName',b'VAS6154A'),('apiName',b'VOLKSWAGEN D-PDU-API')):
        target=getattr(data,name)
        for i,value in enumerate(text):target[i]=value
    data.part2Version=0x02020000;data.hardwareSerial=12345
    data.hardwareVersion=0x01000000;data.firmwareVersion=0x18010000;data.apiVersion=0x18010000
    return data


class Pt3gVersionTests(unittest.TestCase):
    def test_abi_matches_reference_and_pinned_wrapper_write_offsets(self):
        self.assertEqual(c.sizeof(version.VersionData),0x128)
        expected={'part1Version':0,'part2Version':4,'hardwareSerial':8,'hardwareName':12,
            'hardwareVersion':0x4c,'hardwareDate':0x50,'hardwareInterface':0x54,
            'firmwareName':0x58,'firmwareVersion':0x98,'firmwareDate':0x9c,
            'vendorName':0xa0,'apiName':0xe0,'apiVersion':0x120,'apiDate':0x124}
        for name,offset in expected.items():self.assertEqual(getattr(version.VersionData,name).offset,offset)

    def test_named_firmware_and_successful_buffer_never_qualify_head(self):
        data=data_buffer();raw=c.string_at(c.byref(data),c.sizeof(data));out=version.parse_api_version(raw)
        self.assertEqual(out['values']['firmwareVersion']['display'],'24.1.0.0')
        self.assertEqual(out['values']['hardwareSerial']['raw'],12345)
        self.assertTrue(out['apiIdentityPresent'])
        self.assertEqual(out['moduleHandle'],version.UNDEF)
        self.assertEqual({k:out[k] for k in version.GUARDS},version.GUARDS)
        self.assertEqual(out['rawHex'],raw.hex().upper())
        empty=version.parse_api_version(bytes(0x128));self.assertFalse(empty['apiIdentityPresent'])

    def test_size_text_encoding_and_termination_are_bounded(self):
        for raw in (bytes(0x127),bytes(0x129),'bad'):
            with self.assertRaisesRegex(ValueError,'size-invalid'):version.parse_api_version(raw)
        data=data_buffer();raw=bytearray(c.string_at(c.byref(data),c.sizeof(data)))
        raw[12:76]=b'A'*64
        with self.assertRaisesRegex(ValueError,'not-terminated'):version.parse_api_version(bytes(raw))
        for value in (10,255):
            raw=bytearray(c.string_at(c.byref(data),c.sizeof(data)));raw[12]=value
            with self.assertRaisesRegex(ValueError,'text-invalid'):version.parse_api_version(bytes(raw))

    def test_whole_source_guard_runs_before_PE_or_native_parsing(self):
        with patch.object(version,'_pe_raw') as parse:
            with self.assertRaisesRegex(ValueError,'source-unreviewed'):version.audit_version_source(b'MZunreviewed')
        parse.assert_not_called()

    def test_PE_mapping_does_not_read_unbacked_virtual_tail_or_overlapping_sections(self):
        raw=bytearray(0x400);raw[:2]=b'MZ';struct.pack_into('<I',raw,0x3c,0x40);raw[0x40:0x44]=b'PE\0\0'
        struct.pack_into('<HH',raw,0x44,0x8664,1);struct.pack_into('<H',raw,0x54,112)
        struct.pack_into('<H',raw,0x58,0x20b);struct.pack_into('<IIII',raw,0xc8+8,0x300,0x1000,0x200,0x200)
        raw[0x200:0x204]=b'DATA'
        self.assertEqual(version._pe_raw(bytes(raw),0x1000,4),b'DATA')
        for address,length in ((0x1200,1),(0x11ff,2),(0x1000,0)):
            with self.assertRaises(ValueError):version._pe_raw(bytes(raw),address,length)
        struct.pack_into('<H',raw,0x46,2);raw[0xc8+40:0xc8+80]=raw[0xc8:0xc8+40]
        with self.assertRaisesRegex(ValueError,'range-invalid'):version._pe_raw(bytes(raw),0x1000,4)

    def test_only_driver_scope_version_query_is_permitted(self):
        native=version._MetadataProbe.__new__(version._MetadataProbe)
        for name,args in [('PDUModuleConnect',(1,)),('PDUGetModuleIds',()),('PDUStartComPrimitive',()),
                          ('PDUCreateComLogicalLink',()),('PDUGetObjectId',()),('PDUIoCtl',()),
                          ('PDUGetVersion',(1,None)),('PDUGetVersion',(True,None)),('PDUGetVersion',())]:
            with self.assertRaises(ElmError):native.call(name,*args)

    def test_API_error_empty_metadata_and_cleanup_error_cannot_pass(self):
        class Fake:
            configuration={};problem=None;closed=0
            def call(self,name,handle,pointer):
                if self.problem=='api':raise ElmError('d-pdu:PDUGetVersion:0x00000011')
                if self.problem!='empty':c.memmove(pointer,c.byref(data_buffer()),c.sizeof(version.VersionData))
            def close(self):
                self.closed+=1;return {'errors':['release-failed'] if self.problem=='close' else []}
        native=Fake()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'PDU_VCI.dll').write_bytes(b'fixture')
            source={'fixedFallbackFields':{'firmwareVersion':0x18010000}}
            with patch.object(version,'audit_version_source',return_value=source),patch.object(version,'_MetadataProbe',return_value=native):
                for problem in (None,'api','empty','close'):
                    native.problem=problem;out=version.probe_driver_version(root)
                    self.assertEqual(out['ok'],problem is None)
                    self.assertEqual({k:out[k] for k in version.GUARDS},version.GUARDS)
                    if problem=='api':self.assertIsNone(out['metadata']);self.assertFalse(out['apiSucceeded'])
                    if problem=='empty':self.assertEqual(out['error'],'pt3g-api-version-metadata-incomplete')
        self.assertEqual(native.closed,4)


if __name__=='__main__':unittest.main()
