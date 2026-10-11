"""Routine header names cannot be confused with body prompts or vehicle recipes."""
from copy import deepcopy
import hashlib
import struct
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.routine_definitions import routine_definition, LEAF_KIND
from scripts.diagnostics.coverage_readiness import function_rows
from scripts.diagnostics.workbench import slim_record, _search_hit, handle


def routine_record(body=b'\x31\x01\xFF\xFF'):
    token = b'0x40005A15\0'
    return {'at': 123, 'payload_kind': LEAF_KIND,
            'raw_hex': (b'\x02' + struct.pack('<H', len(token)) + token + body).hex(),
            'labels': [{'id_hex': '40005A15', 'text': '灯罩加热器', 'namespace': 'DSTREAM_CN.GAG'},
                       {'id_hex': 'F001A261', 'text': '保留创建的空结构', 'namespace': 'DSTREAM_CN.GAG'}],
            'source': {'file': '9X1_ALLDATA.BIN.dec', 'offset': 90, 'sha256': 'A' * 64}}


class RoutineDefinitionsTests(unittest.TestCase):
    def test_body_prompt_is_not_the_function_name_or_search_key(self):
        record = routine_record()
        item = slim_record(record, 'routine')
        self.assertEqual(item['displayName'], '灯罩加热器')
        self.assertTrue(_search_hit(record, 'routine', '加热器'))
        self.assertFalse(_search_hit(record, 'routine', '空结构'))
        self.assertIsNone(item['requestCandidate'])
        self.assertEqual(item['unsupportedReason'], 'routine-recipe-and-recovery-required')

    def test_header_and_body_have_separate_boundaries_and_hashes(self):
        raw = bytes.fromhex(routine_record()['raw_hex'])
        result = routine_definition(routine_record())
        self.assertEqual(result['headerBytes'], 14)
        self.assertEqual(result['bodyBytes'], 4)
        self.assertEqual(result['nativeEntryTag'], 2)
        self.assertEqual(result['nameId'], '40005A15')
        self.assertEqual(result['headerSha256'], hashlib.sha256(raw[:14]).hexdigest())
        self.assertEqual(result['bodySha256'], hashlib.sha256(raw[14:]).hexdigest())
        self.assertEqual(result['sourceOffset'], 123)
        self.assertEqual(result['source']['offset'], 90)

    def test_name_never_grants_command_or_recipe_qualification(self):
        for body in [b'', b'\x31\x01\xff\xff', b'\xff' * 20]:
            result = routine_definition(routine_record(body))
            self.assertEqual(result['status'], 'source-header-only')
            for flag in ['recipeQualified', 'executionEnabled', 'liveVerified', 'nativeHeaderSemanticsQualified']:
                self.assertFalse(result[flag])
            self.assertIsNone(result['requestHex']); self.assertIsNone(result['writePayload'])
            self.assertEqual(result['functionClass'], 'unclassified')

    def test_all_header_truncations_publish_no_name(self):
        record = routine_record()
        raw = bytes.fromhex(record['raw_hex'])
        for end in range(14):
            with self.subTest(end=end):
                result = routine_definition({**record, 'raw_hex': raw[:end].hex()})
                self.assertEqual(result['status'], 'unresolved')
                self.assertIsNone(result['name']); self.assertIsNone(result['headerBytes'])

    def test_empty_missing_conflicting_and_wrong_namespace_labels_remain_distinct(self):
        original = routine_record()
        for labels, status in [([], 'source-entry-missing'),
                ([{'id_hex': '40005A15', 'text': '', 'namespace': 'DSTREAM_CN.GAG'}], 'source-empty'),
                ([{'id_hex': '40005A15', 'text': 'another namespace', 'namespace': 'TEXT_CN.GAG'}], 'source-entry-missing'),
                ([*original['labels'], {**original['labels'][0], 'text': 'conflict'}], 'source-conflict'),
                ([{**original['labels'][0], 'text': {'malformed': True}}], 'source-conflict')]:
            with self.subTest(status=status):
                result = routine_definition({**original, 'labels': labels})
                self.assertEqual(result['nameStatus'], status)
                self.assertIsNone(result['name'])

    def test_no_fallback_to_other_body_id_or_untrusted_name(self):
        record = routine_record()
        record.update(name='guessed name', labels=record['labels'][1:], readSID=0x22, pid=0xF190)
        item = slim_record(record, 'routine')
        self.assertIsNone(item['displayName']); self.assertIsNone(item['name'])
        self.assertIsNone(item['requestCandidate'])

    def test_dynamic_label_is_not_shown_as_resolved(self):
        record = routine_record()
        record['labels'][0]['text'] = 'SubIndexNum=1#0xF0,0x00,0x00,0x01'
        result = routine_definition(record)
        self.assertEqual(result['nameStatus'], 'dynamic-name-unresolved')
        self.assertIsNone(result['name'])

    def test_unqualified_layout_chain_hex_and_terminator_rejected(self):
        original = routine_record()
        raw = bytearray.fromhex(original['raw_hex'])
        invalid = [dict(original, payload_kind='routine_16B_row'), dict(original, raw_hex='abc'),
                   dict(original, raw_hex='GG'), dict(original, raw_hex='00' * 65537)]
        for offset, value in [(1, 12), (3, ord('1')), (13, 1), (5, 0)]:
            changed = raw.copy(); changed[offset] = value
            invalid.append(dict(original, raw_hex=changed.hex()))
        for record in invalid:
            with self.subTest(record=record.get('raw_hex', '')[:20]):
                result = routine_definition(record)
                self.assertEqual(result['status'], 'unresolved'); self.assertIsNone(result['name'])

    def test_coverage_binds_function_header_without_completing_recipe(self):
        record = routine_record()
        variant = {'profile_id': '9x1:ACC3:ACC3_BaseSys', 'module': 'ACC3', 'name': 'ACC3_BaseSys',
                   'generation': '9x1', 'pool_records': {'routine': {'records': [record]}}}
        row = next(row for row in function_rows(variant, '981', 64) if row['category'] == 'routine')
        reference = row['references'][0]
        self.assertEqual(reference['name'], '灯罩加热器')
        self.assertEqual(reference['nameId'], '40005A15')
        self.assertEqual(reference['routineDefinition']['bodyBytes'], 4)
        self.assertEqual(row['definitionStatus'], 'unresolved')
        self.assertIn('routine-recipe-and-recovery-required', row['definitionGaps'])
        self.assertFalse(row['executionEnabled'])
        bad = deepcopy(variant); bad['pool_records']['routine']['records'][0]['raw_hex'] = '00'
        unresolved = next(row for row in function_rows(bad, '981', 64) if row['category'] == 'routine')
        self.assertIn('name-unresolved', unresolved['definitionGaps'])
        self.assertIsNone(unresolved['references'][0]['name'])

    def test_json_api_retains_exact_source_and_rejects_ecu_and_generation_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'variants.jsonl'
            profile = '9x1:ACC3:ACC3_981_Test'
            path.write_text(json.dumps({'profile_id': profile, 'module': 'ACC3', 'name': 'ACC3_981_Test',
                'generation': '981', 'pool_records': {'routine': {'count': 1, 'records': [routine_record()]}}}) + '\n', encoding='utf8')
            with patch.dict(os.environ, {'PORSCHE981_VARIANTS': str(path)}):
                request = {'action': 'records', 'category': 'routine', 'generation': '981', 'ecuId': 64, 'profileId': profile}
                result = handle(request)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['items'][0]['displayName'], '灯罩加热器')
                self.assertEqual(result['items'][0]['routineDefinition']['source'], routine_record()['source'])
                self.assertFalse(result['executionEnabled']); self.assertIsNone(result['writePayload'])
                self.assertEqual(handle({**request, 'search': '空结构'})['total'], 0)
                self.assertFalse(handle({**request, 'generation': '982'})['ok'])
                self.assertFalse(handle({**request, 'ecuId': 1})['ok'])


if __name__ == '__main__': unittest.main()
