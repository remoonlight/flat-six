import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import hashlib
from copy import deepcopy
from unittest.mock import patch

from scripts.diagnostics.coding_input_source import (
    GUARDS, _pic_address, audit_coding_input_sources, audit_coding_record_storage,
    audit_variant_storage, describe_coding_input, main)
from scripts.diagnostics.offline_match import ANCHORS


def fixture(property_byte=1):
    prefix = b'\x22\x2e\0\x06\0\0\0'
    chain = b'0x80000001,0xF0000123\0'
    suffix = bytes([property_byte]) + struct.pack('<HBI', 15, 3, 0xf0000456)
    at = 5
    data = b'PADDING'[:at] + prefix + struct.pack('<H', len(chain)) + chain + suffix
    record = dict(at=at, request_prefix_hex=prefix.hex(), chain=chain[:-1].decode(), suffix_hex=suffix.hex(),
        readSID=0x22, companionSID=0x2e, pid=6, propertyByte=property_byte, byteOffset=15, bitOffset=3,
        formula={'id_hex': 'F0000456'}, source={'sha256': ANCHORS['data'].upper()})
    return data, record


class RecordStorageTests(unittest.TestCase):
    def test_source_bytes_match_but_property_never_grants_write(self):
        for value in (0, 1, 2, 255):
            data, record = fixture(value)
            result = audit_coding_record_storage(record, data)
            self.assertEqual(result['propertyByte'], value)
            self.assertTrue(result['sourceStorageMatched'])
            self.assertEqual({k:result[k] for k in GUARDS}, GUARDS)

    def test_each_metadata_field_is_checked_against_original_storage(self):
        data, record = fixture()
        mutations = dict(request_prefix_hex='222e0000070000', suffix_hex='010e0003560400f0',
            chain='0x80000002', readSID=0x21, companionSID=0x31, pid=7,
            propertyByte=2, byteOffset=14, bitOffset=4, formula={'id_hex':'F0000457'})
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'generated-record-source-mismatch'):
                audit_coding_record_storage(record | {key:value}, data)

    def test_boolean_integer_metadata_and_boolean_position_rejected(self):
        data, record = fixture()
        for key in ('at', 'propertyByte', 'readSID', 'pid', 'byteOffset', 'bitOffset'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit_coding_record_storage(record | {key:True}, data)

    def test_offsets_cannot_read_before_or_after_record(self):
        data, record = fixture()
        for at in (-1, len(data), len(data)-8, 99999999, '5', None):
            with self.subTest(at=at), self.assertRaises(ValueError):
                audit_coding_record_storage(record | {'at':at}, data)

    def test_truncated_prefix_chain_and_suffix_all_rejected(self):
        data, record = fixture()
        for length in (0, 12, 14, len(data)-9, len(data)-1):
            with self.subTest(length=length), self.assertRaises(ValueError):
                audit_coding_record_storage(record, data[:length])

    def test_size_is_bounded_before_following_suffix(self):
        data, record = fixture()
        for size in (0, 1, 257, 65535):
            mutated = bytearray(data); struct.pack_into('<H', mutated, record['at']+7, size)
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, 'record-body-invalid'):
                audit_coding_record_storage(record, bytes(mutated))

    def test_nonterminated_and_nonascii_chain_fail_without_lossy_decode(self):
        data, record = fixture()
        start = record['at']+9
        mutated = bytearray(data); mutated[-9] = ord('x')
        with self.assertRaisesRegex(ValueError, 'chain-unterminated'):
            audit_coding_record_storage(record, bytes(mutated))
        mutated = bytearray(data); mutated[start] = 255
        with self.assertRaisesRegex(ValueError, 'chain-invalid'):
            audit_coding_record_storage(record, bytes(mutated))

    def test_other_source_anchor_cannot_be_joined(self):
        data, record = fixture()
        with self.assertRaisesRegex(ValueError, 'record-source-anchor-mismatch'):
            audit_coding_record_storage(record | {'source': {'sha256': '0'*64}}, data)


class PicTests(unittest.TestCase):
    class Elf:
        def __init__(self, data): self.data=data
        def raw(self, address, count): return self.data[address:address+count]

    def test_thumb_literals_align_pc_for_narrow_and_wide_loads(self):
        # Both load a known relative word at 0x40 and add PC at 0x18 + 4.
        for load, instruction in ((0x12, struct.pack('<H', 0x490b)),
                                  (0x12, struct.pack('<HH', 0xf8df, 0x102c))):
            data=bytearray(100); data[load:load+len(instruction)]=instruction
            struct.pack_into('<H', data, 0x18, 0x4479)
            struct.pack_into('<I', data, 0x40, 0xfffffff0)
            self.assertEqual(_pic_address(self.Elf(data),load,0x18),0x0c)

    def test_nonliteral_load_and_wrong_register_do_not_resolve(self):
        data=bytearray(100); struct.pack_into('<H', data, 0x12, 0x490b)
        for addition in (0x4478, 0x4409, 0xbf00):
            struct.pack_into('<H', data, 0x18, addition)
            with self.assertRaisesRegex(ValueError, 'pic-add-unreviewed'):
                _pic_address(self.Elf(data),0x12,0x18)
        struct.pack_into('<H', data, 0x12, 0x6809)
        with self.assertRaisesRegex(ValueError, 'pic-load-unreviewed'):
            _pic_address(self.Elf(data),0x12,0x18)


class InputShapeTests(unittest.TestCase):
    @staticmethod
    def record(fields):
        return dict(formula={'text': 'IDENTICAL:' + fields})

    def test_input_type_is_not_substituted_for_encoded_type_or_character_encoding(self):
        result = describe_coding_input(self.record(
            'Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=88,Encoding=ISO-8859-2,'
            'HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'))
        self.assertEqual((result['codedType'], result['inputType'], result['sourceEncoding']),
                         ('A_ASCIISTRING', 'A_UNICODE2STRING', 'ISO-8859-2'))
        self.assertFalse(result['inputTypeMatchesCodedType'])
        self.assertEqual((result['category'], result['sourceFixedBits'], result['sourceFixedBytes']),
                         ('text', 88, 11))
        for key in ('inputCharacterEncodingQualified', 'legalInputDomainQualified', 'completeBlockLengthQualified'):
            self.assertFalse(result[key])
        self.assertEqual({k: result[k] for k in GUARDS}, GUARDS)

    def test_variable_length_source_is_not_a_fixed_extent_or_vehicle_legal_domain(self):
        result = describe_coding_input(self.record(
            'MinMax,MinLength=6,BaseDataType=A_ASCIISTRING,MaxLength=20,BitMask=0,'
            'Encoding=ISO-8859-2,Termination=END-OF-PDU,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'))
        self.assertEqual((result['sourceLengthMode'], result['sourceMinimumLength'], result['sourceMaximumLength']),
                         ('MinMax', 6, 20))
        self.assertEqual(result['sourceTermination'], 'END-OF-PDU')
        self.assertIsNone(result['sourceFixedBits'])
        self.assertIsNone(result['sourceFixedBytes'])
        self.assertFalse(result['legalInputDomainQualified'])

    def test_bytefield_is_distinct_from_named_text_input(self):
        result = describe_coding_input(self.record(
            'Standar,BitMask=0,BaseDataType=A_BYTEFIELD,BitLength=224,Encoding=Undefined,'
            'HighLow=1,Radix=10,DataType=A_BYTEFIELD,Precision=null;'))
        self.assertEqual(result['category'], 'bytefield')
        self.assertTrue(result['inputTypeMatchesCodedType'])
        self.assertEqual(result['sourceFixedBytes'], 28)
        self.assertFalse(result['executionEnabled'])
        self.assertIsNone(result['writePayload'])

    def test_packed_bcd_and_signed_numeric_input_stay_distinct(self):
        for coded, encoding, radix in (('A_UINT32', 'BCD-P', 10), ('A_INT32', '2C', 16)):
            result = describe_coding_input(self.record(
                f'Standar,BitMask=0,BaseDataType={coded},BitLength=16,Encoding={encoding},'
                f'HighLow=1,Radix={radix},DataType={coded},Precision=null;'))
            self.assertEqual((result['category'], result['sourceEncoding'], result['sourceRadix']),
                             ('numeric', encoding, radix))
            self.assertFalse(result['inferredBoundsEnabled'])

    def test_nonbyte_width_does_not_get_an_invented_byte_extent(self):
        result = describe_coding_input(self.record(
            'Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=5,Encoding=Undefined,'
            'HighLow=1,Radix=16,DataType=A_UINT32,Precision=null;'))
        self.assertEqual(result['sourceFixedBits'], 5)
        self.assertIsNone(result['sourceFixedBytes'])
        self.assertIsNone(result['sourceNumericLower'])
        self.assertIsNone(result['sourceNumericUpper'])

    def test_malformed_formula_does_not_get_a_default_input_type(self):
        for formula in ({}, {'text': 'unknown'}, {'text': 'IDENTICAL:BitLength=wrong;'},
                        {'text': 'IDENTICAL:BaseDataType=A_UINT32,BitLength=16;'}):
            result = describe_coding_input({'formula': formula})
            self.assertFalse(result['resolved'])
            self.assertNotIn('inputType', result)
            self.assertEqual({k: result[k] for k in GUARDS}, GUARDS)

    def test_pinned_whole_census_keeps_every_source_shape_and_unresolved_record(self):
        data, base = fixture()
        forms = [
            'IDENTICAL:Standar,BaseDataType=A_ASCIISTRING,DataType=A_UNICODE2STRING,BitLength=88,Encoding=ISO-8859-2;',
            'IDENTICAL:MinMax,BaseDataType=A_BYTEFIELD,DataType=A_BYTEFIELD,MinLength=1,MaxLength=30,Termination=END-OF-PDU;',
            'unresolved',
        ]
        data_sha = hashlib.sha256(data).hexdigest()
        records = []
        for text in forms:
            record = deepcopy(base); record['formula']['text'] = text
            record['source']['sha256'] = data_sha; records.append(record)
        variants = json.dumps({'pool_records': {'coding': {'records': records}}}) + '\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'variants.jsonl'; path.write_text(variants, encoding='utf8', newline='\n')
            anchors = {'data': data_sha, 'variants': hashlib.sha256(path.read_bytes()).hexdigest()}
            with patch.dict(ANCHORS, anchors):
                result = audit_variant_storage(path, data)
        self.assertEqual((result['recordCount'], result['unresolvedInputRecords']), (3, 1))
        self.assertEqual(sum(result['inputShapes'].values()), 2)
        self.assertEqual(result['differingInputAndCodedTypeRecords'], 1)
        self.assertEqual(result['identicalTextLengths'], {'fixed-bits': 1})
        self.assertEqual(result['identicalBytefieldLengths'], {'MinMax': 1})
        self.assertEqual(result['storageMismatches'], 0)
        self.assertFalse(result['propertyValueIsWritePermission'])
        self.assertFalse(result['legalInputDomainQualified'])


class SourceAndCliTests(unittest.TestCase):
    def test_unreviewed_elf_rejected_before_parse_or_execution(self):
        with patch('scripts.diagnostics.coding_input_source._Elf') as elf:
            for data in (b'ELF', bytearray(b'ELF'), None):
                with self.assertRaisesRegex(ValueError, 'calc-source-unreviewed'):
                    audit_coding_input_sources(data, b'ELF')
            elf.assert_not_called()

    def test_unreviewed_record_source_rejected_before_variants_are_opened(self):
        with self.assertRaisesRegex(ValueError, 'data-source-unreviewed'):
            audit_variant_storage(Path('/missing/variants.jsonl'), b'unknown')

    def test_cli_requires_both_census_inputs_and_reports_source_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'libPORSCHE_CALC.so').write_bytes(b'unknown')
            (path/'libPORSCHE_READWRITE.so').write_bytes(b'unknown')
            with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                main(['--source-dir',folder,'--variants',str(path/'variants')])
            self.assertEqual(error.exception.code,2)
            with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                main(['--source-dir',folder])
            self.assertEqual(error.exception.code,2)


if __name__ == '__main__':
    unittest.main()
