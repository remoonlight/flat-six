from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.coding_fields import coding_options, coding_effects
from scripts.diagnostics.coding_audit import audit_named_patches, compile_audit
from scripts.diagnostics.decoder_reference import reference_patch
from scripts.diagnostics.tests.test_realtime_preparation import field


def enum(width=3, shift=1, highlow=1):
    record = field()
    record.update(at=1, bitOffset=shift, enumText={'F001': '关闭', 'F002': '开启'},
        formula={'text': f'TEXTTABLE:BaseDataType=A_UINT32,DataType=A_UINT32,[0,1]->0xF001;[2,3]->0xF002;BitLength={width},BitMask=0,HighLow={highlow};'})
    return record


class CodingFieldsTests(unittest.TestCase):
    def test_readable_literal_formula_does_not_qualify_coding_choices(self):
        record = enum()
        record['formula']['text'] = record['formula']['text'].replace('0xF001', 'nein')
        with self.assertRaisesRegex(ValueError, 'coding_options_not_defined'):
            coding_options(record, b'\0')

    def test_ranges_only_named_representable_options_and_other_bits_preserved(self):
        record = enum()
        self.assertEqual(coding_options(record, b'\xFF\xA5'), [
            {'rawValue': 0, 'label': '关闭'}, {'rawValue': 1, 'label': '关闭'},
            {'rawValue': 2, 'label': '开启'}, {'rawValue': 3, 'label': '开启'}])
        result = audit_named_patches(record)
        self.assertEqual((result['status'], result['cases']), ('matched', 12))
        self.assertEqual(reference_patch(record, b'\xFF\xA5', 2), b'\xF5\xA5')
        record['enumText']['F002'] = ''
        self.assertEqual(len(coding_options(record, b'\xFF')), 2)

    def test_sparse_32bit_and_signed_cross_byte_both_endian_orders(self):
        record = enum(32, 0)
        record['formula']['text'] = record['formula']['text'].replace('[0,1]', '[0xFFFFFFFF]').replace('[2,3]', '[0x80000000]')
        self.assertEqual([o['rawValue'] for o in coding_options(record, bytes(4))], [0x80000000, 0xFFFFFFFF])
        self.assertEqual(audit_named_patches(record)['status'], 'matched')
        for order in (0, 1):
            record = enum(12, 4, order)
            record['formula']['text'] = record['formula']['text'].replace('A_UINT32', 'A_INT32').replace('[0,1]', '[0x-1]').replace('[2,3]', '[0x-800]')
            self.assertEqual([o['rawValue'] for o in coding_options(record, bytes(2))], [2048, 4095])
            self.assertEqual(audit_named_patches(record)['status'], 'matched')

    def test_broad_ranges_and_unknown_values_do_not_open_raw_editor(self):
        record = enum(16, 0)
        record['formula']['text'] = record['formula']['text'].replace('[0,1]', '[0,65535]').replace('[2,3]->0xF002;', '')
        with self.assertRaisesRegex(ValueError, 'options_limit'):
            coding_options(record, bytes(2))
        with self.assertRaisesRegex(ValueError, 'not_defined'):
            coding_options(field(), bytes(1))
        unnamed = enum(); unnamed.pop('name')
        self.assertEqual(coding_options(unnamed, bytes(1)), [])

    def test_masked_field_preserves_unknown_bits(self):
        record = enum(8, 0)
        record['formula']['text'] = record['formula']['text'].replace('BitMask=0,', 'BitMask=0F,')
        self.assertEqual(reference_patch(record, b'\xFA\xFF', 2), b'\xF2\xFF')
        self.assertEqual(audit_named_patches(record)['status'], 'matched')

    def test_all_source_audit_cache_keeps_missing_names_and_bad_reads_separate(self):
        named = enum(); unnamed = deepcopy(named); unnamed.pop('name'); unnamed['at'] = 2
        bad_read = deepcopy(named); bad_read.update(at=3, readSID=0x2E, read_request_candidate_hex=None)
        variant = {'profile_id': 'synthetic', 'name': 'synthetic-only', 'module': 'DME', 'generation': '981',
            'pool_records': {'coding': {'records': [named, unnamed, bad_read]}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'variants.jsonl'; coverage = root / 'coverage.json'
            source.write_text(json.dumps(variant) + '\n', encoding='utf8')
            coverage.write_text(json.dumps({'menu_ecus': [{'ecu_id': 1, 'dsn_modules': ['DME']}]}), encoding='utf8')
            with patch('scripts.diagnostics.offline.DEFAULT_COVERAGE', coverage):
                out = compile_audit(source, root / 'output')
                with self.assertRaisesRegex(ValueError, 'output-not-empty'):
                    compile_audit(source, root / 'output')
            self.assertEqual(out['mappedUnitIds'], [1])
            self.assertEqual(out['counts'], {'fields': 3, 'decoder:matched': 2, 'patch:matched': 2,
                'patch:unverified': 1, 'decoder:unverified': 1})
            self.assertEqual(out['editorBlockers']['coding-read-request-unresolved'], 1)
            self.assertEqual(out['editorBlockers']['coding-options-label-or-value-unresolved'], 1)
            self.assertFalse(out['executionEnabled'])

    def test_related_fields_and_unresolved_overlap(self):
        selected = enum(1, 0)
        selected['formula']['text'] = selected['formula']['text'].replace('[0,1]', '[0]').replace('[2,3]', '[1]')
        alias = deepcopy(selected); alias['at'] = 2
        other_block = deepcopy(selected); other_block.update(at=3, pid=0xF101, read_request_candidate_hex='22F101')
        effects = coding_effects([selected, alias, other_block], selected, b'\xA4', b'\xA5')
        self.assertEqual([e['sourceOffset'] for e in effects], [1, 2])
        broken = deepcopy(alias); broken['formula'] = {'text': 'unresolved'}
        with self.assertRaisesRegex(ValueError, 'layout-unresolved'):
            coding_effects([selected, broken], selected, b'\xA4', b'\xA5')
        unnamed = deepcopy(alias); unnamed.pop('name')
        with self.assertRaisesRegex(ValueError, 'field-unresolved'):
            coding_effects([selected, unnamed], selected, b'\xA4', b'\xA5')
        unlabeled = deepcopy(alias); unlabeled['enumText']['F002'] = ''
        with self.assertRaisesRegex(ValueError, 'value-unresolved'):
            coding_effects([selected, unlabeled], selected, b'\xA4', b'\xA5')


if __name__ == '__main__': unittest.main()
