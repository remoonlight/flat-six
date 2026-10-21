import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from scripts.diagnostics import firmware_container as module
from scripts.diagnostics.workbench import handle


ODX = '''<?xml version="1.0"?><ODX MODEL-VERSION="2.1.0"><FLASH><ECU-MEMS><ECU-MEM ID="MEM">
<MEM><SESSIONS><SESSION ID="SESSION"><SHORT-NAME>Fixture session</SHORT-NAME><DATABLOCK-REFS><DATABLOCK-REF ID-REF="BLOCK"/></DATABLOCK-REFS></SESSION></SESSIONS>
<DATABLOCKS><DATABLOCK ID="BLOCK" TYPE="DATA"><SHORT-NAME>Fixture block</SHORT-NAME><FLASHDATA-REF ID-REF="DATA"/>
<SEGMENTS><SEGMENT ID="SEG"><SOURCE-START-ADDRESS>20</SOURCE-START-ADDRESS><COMPRESSED-SIZE>3</COMPRESSED-SIZE></SEGMENT></SEGMENTS></DATABLOCK></DATABLOCKS>
<FLASHDATAS><FLASHDATA ID="DATA"><SHORT-NAME>Fixture payload</SHORT-NAME><DATAFORMAT SELECTION="BINARY"/>
<ENCRYPT-COMPRESS-METHOD TYPE="A_BYTEFIELD">2A</ENCRYPT-COMPRESS-METHOD>PAYLOAD</FLASHDATA></FLASHDATAS></MEM></ECU-MEM></ECU-MEMS></FLASH></ODX>'''

IDENTIFIERS = '''<EXPECTED-IDENTS><EXPECTED-IDENT ID="EXPECT"><SHORT-NAME>Part</SHORT-NAME>
<LONG-NAME>Declared part</LONG-NAME><IDENT-VALUES><IDENT-VALUE TYPE="A_ASCIISTRING">981 TEST  </IDENT-VALUE>
<IDENT-VALUE TYPE="A_ASCIISTRING"> \n </IDENT-VALUE></IDENT-VALUES></EXPECTED-IDENT></EXPECTED-IDENTS>'''
OWN = '''<OWN-IDENTS><OWN-IDENT ID="OWN"><SHORT-NAME>Version</SHORT-NAME><IDENT-VALUES>
<IDENT-VALUE TYPE="A_BYTEFIELD">303031</IDENT-VALUE></IDENT-VALUES></OWN-IDENT></OWN-IDENTS>'''
CONNECTOR = '''<ECU-MEM-CONNECTORS><ECU-MEM-CONNECTOR ID="CONNECTOR"><SHORT-NAME>Connector</SHORT-NAME>
<ECU-MEM-REF ID-REF="MEM"/><SESSION-DESCS><SESSION-DESC DIRECTION="DOWNLOAD"><SHORT-NAME>Description</SHORT-NAME>
<PARTNUMBER>981 TARGET </PARTNUMBER><SESSION-SNREF SHORT-NAME="Fixture session"/>
<DIAG-COMM-SNREF SHORT-NAME="ExternalFlashJob"/></SESSION-DESC></SESSION-DESCS><IDENT-DESCS>
<IDENT-DESC><IDENT-IF-SNREF SHORT-NAME="Part"/><DIAG-COMM-SNREF SHORT-NAME="ExternalPartRead"/>
<OUT-PARAM-IF-SNREF SHORT-NAME="Value"/></IDENT-DESC>
<IDENT-DESC><IDENT-IF-SNREF SHORT-NAME="Version"/><DIAG-COMM-SNREF SHORT-NAME="ExternalVersionRead"/></IDENT-DESC>
</IDENT-DESCS><LAYER-REFS><LAYER-REF ID-REF="ExternalLayer" DOCREF="OtherDoc"/></LAYER-REFS>
</ECU-MEM-CONNECTOR></ECU-MEM-CONNECTORS>'''


def identity_odx():
    return (ODX.replace('<DATABLOCK-REFS>', IDENTIFIERS+'<DATABLOCK-REFS>')
        .replace('<FLASHDATA-REF', OWN+'<FLASHDATA-REF')
        .replace('</ECU-MEMS>', '</ECU-MEMS>'+CONNECTOR).replace('PAYLOAD', '<DATA>0102FF</DATA>'))


class FirmwareContainerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def pdx(self, *, xml=None, payload=b'\x01\x02\xff', catalog=True, compression=zipfile.ZIP_DEFLATED):
        path = self.root/'原厂 fixture.pdx'
        text = xml or ODX.replace('PAYLOAD', '<DATAFILE LATEBOUND-DATAFILE="false">data.bin</DATAFILE>')
        with zipfile.ZipFile(path, 'w', compression) as package:
            package.writestr('ecu.odx-f', text)
            if payload is not None:
                package.writestr('data.bin', payload)
            if catalog:
                package.writestr('index.xml', '<PDX><FILE>ecu.odx-f</FILE><FILE>data.bin</FILE></PDX>')
        return path

    def odx(self, text=None):
        path = self.root/'inline.odx'
        path.write_text(text or ODX.replace('PAYLOAD', '<DATA>01 02\nFF</DATA>'), encoding='utf8')
        return path

    def test_pdx_hashes_actual_bytes_and_keeps_opaque_declarations(self):
        p = self.pdx()
        before = p.read_bytes()
        r = module.inspect_container(p)
        self.assertTrue(r['complete'])
        self.assertEqual(r['file']['sha256'], hashlib.sha256(before).hexdigest())
        f = r['documents'][0]['flashdata'][0]
        self.assertEqual(f['payload']['sha256'], hashlib.sha256(b'\x01\x02\xff').hexdigest())
        self.assertEqual(f['payload']['bytes'], 3)
        self.assertEqual(f['encryptCompressMethod']['text'], '2A')
        self.assertEqual(r['documents'][0]['blocks'][0]['segments'][0]['fields'][0]['text'], '20')
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual({k:r[k] for k in module.FLAGS}, module.FLAGS)
        self.assertNotIn('dataHex', json.dumps(r))

    def test_inline_payload_hashes_decoded_bytes_without_export(self):
        r = module.inspect_container(self.odx())
        self.assertTrue(r['complete'])
        self.assertEqual(r['counts']['inlinePayloads'], 1)
        self.assertEqual(r['documents'][0]['flashdata'][0]['payload']['sha256'], hashlib.sha256(b'\x01\x02\xff').hexdigest())
        self.assertFalse(r['members'])

    def test_duplicate_payload_declarations_never_choose_the_first(self):
        variants = {
            'inline': ODX.replace('PAYLOAD', '<DATA>0102FF</DATA><DATA>0304</DATA>'),
            'external': ODX.replace('PAYLOAD', '<DATAFILE>data.bin</DATAFILE><DATAFILE>missing.bin</DATAFILE>'),
        }
        for kind, xml in variants.items():
            with self.subTest(kind=kind):
                r = module.inspect_container(self.pdx(xml=xml))
                self.assertFalse(r['complete'])
                self.assertIsNone(r['documents'][0]['flashdata'][0]['payload'])
                self.assertIn('flashdata-payload-ambiguous-or-missing', [i['reason'] for i in r['issues']])
                self.assertEqual({k:r[k] for k in module.FLAGS}, module.FLAGS)

    def test_duplicate_format_method_and_block_reference_are_unresolved(self):
        base = ODX.replace('PAYLOAD', '<DATA>0102FF</DATA>')
        cases = [
            ('<DATAFORMAT SELECTION="BINARY"/>', '<DATAFORMAT SELECTION="TEXT"/>', 'flashdata-dataformat-ambiguous-or-missing'),
            ('<ENCRYPT-COMPRESS-METHOD TYPE="A_BYTEFIELD">2A</ENCRYPT-COMPRESS-METHOD>',
             '<ENCRYPT-COMPRESS-METHOD TYPE="A_BYTEFIELD">00</ENCRYPT-COMPRESS-METHOD>', 'flashdata-encrypt-compress-method-ambiguous'),
            ('<FLASHDATA-REF ID-REF="DATA"/>', '<FLASHDATA-REF ID-REF="OTHER"/>', 'odx-reference-ambiguous'),
        ]
        for original, extra, reason in cases:
            with self.subTest(reason=reason):
                r = module.inspect_container(self.odx(base.replace(original, original+extra)))
                self.assertFalse(r['complete'])
                self.assertIn(reason, [i['reason'] for i in r['issues']])
                d = r['documents'][0]
                if reason == 'odx-reference-ambiguous':
                    self.assertFalse(d['blocks'][0]['flashdata']['resolved'])
                elif reason.startswith('flashdata-dataformat'):
                    self.assertIsNone(d['flashdata'][0]['format'])
                else:
                    self.assertIsNone(d['flashdata'][0]['encryptCompressMethod'])
                self.assertEqual({k:r[k] for k in module.FLAGS}, module.FLAGS)

    def test_identity_declarations_preserve_padding_blank_types_and_source_owners(self):
        p = self.odx(identity_odx())
        before = p.read_bytes()
        r = module.inspect_container(p)
        self.assertTrue(r['complete'])
        d = r['documents'][0]
        s, b = d['sessions'][0], d['blocks'][0]
        self.assertEqual(s['memoryId'], 'MEM')
        self.assertEqual(b['memoryId'], 'MEM')
        expected = s['expectedIdentifiers'][0]
        self.assertEqual(expected['longName'], 'Declared part')
        self.assertEqual([v['text'] for v in expected['values']], ['981 TEST  ', ' \n '])
        self.assertEqual(expected['values'][0]['attributes'], {'TYPE':'A_ASCIISTRING'})
        self.assertEqual(b['ownIdentifiers'][0]['values'][0]['text'], '303031')
        c = d['memoryConnectors'][0]
        self.assertTrue(c['memoryReference']['resolved'])
        self.assertEqual(c['sessionDescriptions'][0]['sessionReference']['targetId'], 'SESSION')
        self.assertEqual(c['sessionDescriptions'][0]['partNumber'], '981 TARGET ')
        self.assertEqual(c['identityDescriptions'][0]['localCandidates'], [dict(id='EXPECT', kind='EXPECTED-IDENT', ownerId='SESSION', ownerKind='SESSION')])
        self.assertEqual(c['identityDescriptions'][1]['localCandidates'], [dict(id='OWN', kind='OWN-IDENT', ownerId='BLOCK', ownerKind='DATABLOCK')])
        self.assertEqual(c['layerReferences'], [{'ID-REF':'ExternalLayer', 'DOCREF':'OtherDoc'}])
        self.assertFalse(d['identityMatchQualified'])
        self.assertEqual({k:r[k] for k in module.FLAGS}, module.FLAGS)
        self.assertEqual(before, p.read_bytes())

    def test_connector_session_cannot_resolve_across_memories_or_external_documents(self):
        xml = identity_odx().replace('</ECU-MEMS>', '<ECU-MEM ID="OTHER"><MEM><SESSION ID="OTHER-S"><SHORT-NAME>Other session</SHORT-NAME></SESSION></MEM></ECU-MEM></ECU-MEMS>')
        for changed in [xml.replace('SHORT-NAME="Fixture session"', 'SHORT-NAME="Other session"'),
                        xml.replace('SHORT-NAME="Fixture session"', 'SHORT-NAME="Fixture session" DOCREF="External"'),
                        xml.replace('<ECU-MEM-REF ID-REF="MEM"/>', '<ECU-MEM-REF ID-REF="DATA"/>'),
                        xml.replace('<ECU-MEM-REF ID-REF="MEM"/>', ''),
                        xml.replace('<SESSION-SNREF SHORT-NAME="Fixture session"/>', '')]:
            with self.subTest(xml=changed[-180:]):
                r = module.inspect_container(self.odx(changed))
                self.assertFalse(r['complete'])
                ref = r['documents'][0]['memoryConnectors'][0]['sessionDescriptions'][0]['sessionReference']
                self.assertFalse(ref['resolved'])
                self.assertIsNone(ref['targetId'])

    def test_duplicate_session_names_are_unresolved_but_ident_interface_candidates_are_retained(self):
        xml = identity_odx().replace('</SESSIONS>', '<SESSION ID="DUP"><SHORT-NAME>Fixture session</SHORT-NAME>'+IDENTIFIERS.replace('ID="EXPECT"','ID="EXPECT2"')+'</SESSION></SESSIONS>')
        r = module.inspect_container(self.odx(xml))
        self.assertFalse(r['complete'])
        c = r['documents'][0]['memoryConnectors'][0]
        self.assertFalse(c['sessionDescriptions'][0]['sessionReference']['resolved'])
        self.assertEqual([t['id'] for t in c['identityDescriptions'][0]['localCandidates']], ['EXPECT', 'EXPECT2'])
        self.assertFalse(r['documents'][0]['identityMatchQualified'])

    def test_duplicate_connector_refs_are_not_silently_first_selected(self):
        for tag in ('<ECU-MEM-REF ID-REF="MEM"/>', '<SESSION-SNREF SHORT-NAME="Fixture session"/>'):
            with self.subTest(tag=tag):
                r = module.inspect_container(self.odx(identity_odx().replace(tag, tag+tag)))
                self.assertFalse(r['complete'])

    def test_unknown_identity_interface_is_retained_without_inventing_a_read_binding(self):
        r = module.inspect_container(self.odx(identity_odx().replace('SHORT-NAME="Part"', 'SHORT-NAME="Unknown"')))
        c = r['documents'][0]['memoryConnectors'][0]
        self.assertEqual(c['identityDescriptions'][0]['localCandidates'], [])
        self.assertEqual(c['identityDescriptions'][0]['diagnosticReferences'], [{'SHORT-NAME':'ExternalPartRead'}])
        self.assertFalse(r['documents'][0]['identityMatchQualified'])

    def test_nested_identifier_value_is_explicitly_unqualified(self):
        r = module.inspect_container(self.odx(identity_odx().replace('981 TEST  ', '<UNKNOWN>981 TEST</UNKNOWN>')))
        self.assertFalse(r['complete'])
        self.assertTrue(r['documents'][0]['sessions'][0]['expectedIdentifiers'][0]['values'][0]['hasChildren'])
        self.assertIn('odx-identifier-value-unqualified', [i['reason'] for i in r['issues']])

    def test_same_session_name_in_different_memory_resolves_only_selected_owner(self):
        xml = identity_odx().replace('</ECU-MEMS>', '<ECU-MEM ID="OTHER"><MEM><SESSION ID="OTHER-S"><SHORT-NAME>Fixture session</SHORT-NAME>'+IDENTIFIERS.replace('ID="EXPECT"','ID="OTHER-E"')+'</SESSION></MEM></ECU-MEM></ECU-MEMS>')
        d = module.inspect_container(self.odx(xml))['documents'][0]
        c = d['memoryConnectors'][0]
        self.assertEqual(c['sessionDescriptions'][0]['sessionReference']['targetId'], 'SESSION')
        self.assertEqual([t['id'] for t in c['identityDescriptions'][0]['localCandidates']], ['EXPECT'])

    def test_short_name_is_exact_and_no_filename_or_partnumber_fallback(self):
        for name in ('fixture session', 'Fixture session ', '981 TARGET '):
            with self.subTest(name=name):
                r = module.inspect_container(self.odx(identity_odx().replace('SHORT-NAME="Fixture session"', 'SHORT-NAME="'+name+'"')))
                self.assertFalse(r['complete'])
                self.assertFalse(r['documents'][0]['memoryConnectors'][0]['sessionDescriptions'][0]['sessionReference']['resolved'])

    def test_pdx_documents_do_not_supply_each_others_memory_or_session(self):
        p = self.root/'separate.pdx'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('index.xml', '<PDX><FILE>source.odx-f</FILE><FILE>other.odx-f</FILE></PDX>')
            z.writestr('source.odx-f', identity_odx().replace('ID-REF="MEM"', 'ID-REF="REMOTE"'))
            z.writestr('other.odx-f', identity_odx().replace('ID="MEM"', 'ID="REMOTE"').replace('ID-REF="MEM"', 'ID-REF="REMOTE"'))
        r = module.inspect_container(p)
        self.assertFalse(r['complete'])
        self.assertFalse(r['documents'][0]['memoryConnectors'][0]['memoryReference']['resolved'])
        self.assertTrue(r['documents'][1]['memoryConnectors'][0]['memoryReference']['resolved'])

    def test_standalone_odx_does_not_read_sibling_external_files(self):
        (self.root/'data.bin').write_bytes(b'private sibling')
        r = module.inspect_container(self.odx(ODX.replace('PAYLOAD', '<DATAFILE>data.bin</DATAFILE>')))
        self.assertFalse(r['complete'])
        self.assertEqual(r['issues'][0]['reason'], 'flashdata-member-unresolved')

    def test_missing_and_wrong_kind_references_are_not_complete(self):
        for ref in ('UNKNOWN', 'DATA'):
            with self.subTest(ref=ref):
                r = module.inspect_container(self.pdx(xml=ODX.replace('ID-REF="BLOCK"', f'ID-REF="{ref}"').replace('PAYLOAD', '<DATAFILE>data.bin</DATAFILE>')))
                self.assertFalse(r['complete'])
                self.assertEqual(r['issues'][0]['reason'], 'odx-reference-unresolved')
        self.assertFalse(module.inspect_container(self.pdx(payload=None))['complete'])

    def test_duplicate_ids_and_non_odx_roots_rejected(self):
        for text, error in [(ODX.replace('ID="SEG"', 'ID="DATA"').replace('PAYLOAD','<DATA>01</DATA>'), 'id-duplicate'),
                            ('<unrelated/>', 'odx-invalid')]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                module.inspect_container(self.odx(text))

    def test_dtd_entities_rejected_in_utf8_and_utf16(self):
        xml = '<!DOCTYPE ODX [<!ENTITY leak SYSTEM "file:///private">]><ODX MODEL-VERSION="2.1.0">&leak;</ODX>'
        for encoding in ('utf8', 'utf16'):
            p = self.root/(encoding+'.odx')
            p.write_bytes(xml.encode(encoding))
            with self.subTest(encoding=encoding), self.assertRaisesRegex(ValueError, 'declaration-forbidden'):
                module.inspect_container(p)

    def test_archive_member_ambiguity_and_traversal_rejected(self):
        for name in ('../data.bin', '/data.bin', 'C:/data.bin', 'dir\\data.bin', 'dir\0data.bin', 'data.bin', 'DATA.BIN'):
            p = self.pdx()
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(p, 'a') as z:
                    # ZipInfo normalizes backslashes on Windows; retain the literal
                    # archive name so this tests the same invalid bytes everywhere.
                    info = zipfile.ZipInfo('placeholder')
                    info.filename = info.orig_filename = name
                    z.writestr(info, b'unreviewed')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'member-(invalid|duplicate)'):
                module.inspect_container(p)

    def test_archive_symlinks_rejected(self):
        p = self.pdx()
        with zipfile.ZipFile(p, 'a') as z:
            info = zipfile.ZipInfo('link.bin')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, 'data.bin')
        with self.assertRaisesRegex(ValueError, 'member-invalid'):
            module.inspect_container(p)

    def test_crc_failure_is_not_accepted(self):
        p = self.pdx(compression=zipfile.ZIP_STORED)
        with zipfile.ZipFile(p) as z:
            offset = z.getinfo('data.bin').header_offset
        raw = bytearray(p.read_bytes())
        names, extra = struct.unpack_from('<HH', raw, offset+26)
        raw[offset+30+names+extra] ^= 1
        p.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'zip-invalid'):
            module.inspect_container(p)

    def test_limits_fail_instead_of_returning_truncated_success(self):
        p = self.pdx()
        for key, value, expected in [('MAX_FILE', 10, 'size-invalid'), ('MAX_XML', 10, 'xml-limit'),
                                     ('MAX_MEMBERS', 1, 'expanded-limit'), ('MAX_NODES', 2, 'xml-limit'),
                                     ('MAX_REPORT', 128, 'report-limit')]:
            with self.subTest(key=key), patch.object(module, key, value), self.assertRaisesRegex(ValueError, expected):
                module.inspect_container(p)

    def test_mutating_selected_file_rejected(self):
        p = self.odx()
        real = module._hash
        calls = 0
        def changing(stream):
            nonlocal calls
            result = real(stream)
            calls += 1
            if calls == 1:
                p.write_bytes(p.read_bytes().replace(b'Fixture session', b'Changed session'))
            return result
        with patch.object(module, '_hash', changing), self.assertRaisesRegex(ValueError, 'file-changed'):
            module.inspect_container(p)

    def test_unknown_format_version_and_invalid_inline_retained_as_incomplete(self):
        p = self.root/'raw.bin'
        p.write_bytes(b'opaque firmware bytes')
        r = module.inspect_container(p)
        self.assertFalse(r['recognized'])
        self.assertFalse(r['complete'])
        for text in [ODX.replace('2.1.0','9.0.0').replace('PAYLOAD','<DATA>01</DATA>'),
                     ODX.replace('PAYLOAD','<DATA>0G</DATA>'), ODX.replace('PAYLOAD','<DATA>0</DATA>'),
                     ODX.replace('PAYLOAD','<DATA>00</DATA><DATAFILE>file.bin</DATAFILE>')]:
            self.assertFalse(module.inspect_container(self.odx(text))['complete'])

    def test_missing_catalog_and_odx_are_reported(self):
        self.assertFalse(module.inspect_container(self.pdx(catalog=False))['complete'])
        p = self.root/'empty.pdx'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('index.xml','<PDX/>')
        self.assertEqual(module.inspect_container(p)['issues'][0]['reason'], 'pdx-odx-missing')

    def test_workbench_selection_is_private_and_request_cannot_supply_path(self):
        p = self.odx()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(handle({'action':'firmware-container'})['error'], 'firmware-container-selection-missing')
        with patch.dict(os.environ, {'PORSCHE981_FIRMWARE_FILE':str(p)}):
            self.assertTrue(handle({'action':'firmware-container'})['container']['complete'])
            for key in ('path', 'firmwareFile', 'inputFile', 'generation'):
                self.assertEqual(handle({'action':'firmware-container',key:str(p)})['error'], 'forbidden_field')


if __name__ == '__main__':
    unittest.main()
