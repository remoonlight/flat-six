"""Pinned native compound-text behavior, without granting edit semantics."""
import unittest

from scripts.diagnostics.coding_fields import coding_field_readiness, coding_options
from scripts.diagnostics.definition_decoder_audit import audit_field
from scripts.diagnostics.response_values import decode_application_response
from scripts.diagnostics.x431_formula import parse_formula_text
from scripts.diagnostics.x431_values import decode_record, preview_coding


def compound(suffix='F0002A75', byte=3, bit=3):
    return {
        'byteOffset': byte, 'bitOffset': bit, 'readSID': 0x22, 'pid': 0x0600,
        'read_request_candidate_hex': '220600', 'name': 'retained source field',
        'formula': f'TEXTTABLE:DataType=A_UINT32,[0x00]->0x310007B4x{suffix};'
                   '[0x01]->0x310007B4;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;',
        'enumText': {'310007B4': '0', 'F0002A75': '启用', '8181003A': '已保留'},
    }


class CompoundTextTests(unittest.TestCase):
    def test_both_reviewed_formulas_retain_complete_target_and_warning(self):
        for suffix in ('F0002A75', '8181003A'):
            parsed = parse_formula_text(compound(suffix)['formula'])
            self.assertTrue(parsed['ok'], parsed)
            self.assertEqual(parsed['maps'], [(0, 0, '310007B4'), (1, 1, '310007B4')])
            self.assertEqual(parsed['compoundTextMapping']['sourceTarget'], '0x310007B4x' + suffix)
            self.assertEqual(parsed['compoundTextMapping']['ignoredSuffix'], suffix)
            self.assertFalse(parsed['compoundTextMapping']['namedOptionsQualified'])

    def test_native_zero_and_one_output_at_all_four_retained_positions(self):
        for suffix, byte, bit in (('F0002A75',3,3), ('F0002A75',3,7),
                                  ('F0002A75',7,0), ('8181003A',7,4)):
            record = compound(suffix, byte, bit)
            for fill in (0, 255):
                for value in (0, 1):
                    raw = bytearray([fill]*8)
                    raw[byte] = (raw[byte] & ~(1 << bit)) | (value << bit)
                    decoded = decode_record(record, bytes(raw))
                    self.assertTrue(decoded['ok'], decoded)
                    self.assertEqual((decoded['raw'],decoded['value']), (value,value))
                    self.assertEqual((decoded['textId'],decoded['text']), ('310007B4','0'))
                    self.assertEqual(decoded['textStatus'], 'resolved')
                    self.assertFalse(decoded['compoundTextMapping']['namedOptionsQualified'])

    def test_unknown_suffix_layout_and_partial_source_are_rejected(self):
        source = compound()['formula']
        for text in (source.replace('F0002A75','F0002A76'),
                     source.replace('BitLength=1','BitLength=2'),
                     source.replace('A_UINT32','A_UINT16'),
                     source.replace('0x310007B4x','0x310007B5x'),
                     source.replace('xF0002A75','xF0002A75x8181003A'),
                     'TEXTTABLE:DataType=A_UINT32,[0x00]->0x310007B4xF0002A75;BitLength=1;'):
            self.assertFalse(parse_formula_text(text)['ok'], text)

    def test_same_native_label_never_qualifies_named_editing(self):
        record = compound()
        for value in (0, 1):
            preview = preview_coding(record, bytes(8), value)
            self.assertFalse(preview['ok'])
            self.assertEqual(preview['reason'], 'preview_compound_enum_meaning_unqualified')
        with self.assertRaisesRegex(ValueError, 'coding_compound_enum_meaning_unqualified'):
            coding_options(record, bytes(8))
        ready = coding_field_readiness(record)
        self.assertFalse(ready['offlinePreviewReady'])
        self.assertFalse(ready['editorEnabled'])
        self.assertFalse(ready['codingWriteQualified'])
        self.assertEqual(ready['editorBlocker'], 'coding_compound_enum_meaning_unqualified')

    def test_response_boundaries_and_raw_scalar_remain_independent(self):
        record = compound('8181003A',7,4)
        raw = b'\0'*7 + b'\x10'
        result = decode_application_response(record, b'\x62\x06\x00' + raw, 'pdu')
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['raw'],result['text']), (1,'0'))
        for response in (b'\x62\x06\x01'+raw, b'\x62\x06\x00'+raw[:-1], b'\x7F\x22\x31'):
            self.assertFalse(decode_application_response(record,response,'pdu')['ok'])

    def test_read_audit_agreement_never_creates_missing_read_or_edit_options(self):
        for suffix in ('F0002A75','8181003A'):
            record = compound(suffix)
            result = audit_field(record)
            self.assertEqual(result['status'], 'matched', result)
            self.assertGreater(result['cases'], 0)
            self.assertEqual(result['responseStatus'], 'matched')
            record.pop('read_request_candidate_hex');record['readSID'] = 0x2E
            result = audit_field(record)
            self.assertEqual(result['fieldStatus'], 'matched')
            self.assertEqual(result['status'], 'unverified')
            self.assertEqual(result['readRequestStatus'], 'unresolved')

    def test_missing_translation_does_not_use_the_ignored_suffix_label(self):
        record = compound(); record['enumText'].pop('310007B4')
        result = decode_record(record,bytes(8))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['textId'], '310007B4')
        self.assertNotEqual(result['text'], '启用')
        self.assertNotEqual(result['textStatus'], 'resolved')


if __name__ == '__main__':
    unittest.main()
