"""Software boundaries for collection planning; no private logs or hardware."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import field_collection
from scripts.diagnostics.field_collection import decode_recorded_cycle, request_groups, capture_batches
from scripts.diagnostics.offline_match import FLAGS, sha
from scripts.diagnostics.all_unit_collection import build_all_unit_pack, read_groups


def parameter(key, request='2110', offset=0, historical=0):
    return {'id': key, 'name': key, 'unit': 'source-unit', 'requestHex': request,
        'decoderReady': True, 'dataSpan': {'kind': 'exact', 'dataMin': offset + 1},
        'byteOffset': offset, 'bitOffset': 0, 'decodedSampleCount': historical,
        'sourceOffsets': [100 + offset], 'nameResolved': True,
        'record': {'readSID': int(request[:2], 16), 'pid': int(request[2:], 16),
            'read_request_candidate_hex': request, 'byteOffset': offset, 'bitOffset': 0,
            'formula': {'text': 'IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1'}}}


class TestFieldCollection(unittest.TestCase):
    def test_short_session_prefers_missing_response_yield_without_truncation(self):
        params = [parameter('old', '2100', historical=1), parameter('small', '2101')]
        params += [parameter('large-' + str(i), '2120', offset=i) for i in range(4)]
        params += [parameter('extra-' + str(i), f'21{0x30+i:02X}') for i in range(13)]
        groups = request_groups({'parameters': params})
        self.assertEqual(groups[0]['requestHex'], '2120')
        self.assertEqual(groups[-1]['requestHex'], '2100')
        batches = capture_batches(groups)
        selections = [item for batch in batches for item in batch['selections']]
        self.assertTrue(all(len(batch['selections']) <= 12 for batch in batches))
        self.assertEqual(len(selections), 16)
        self.assertEqual(len({item['requestHex'] for item in selections}), 16)
        self.assertEqual(sum(batch['potentialOfflineFields'] for batch in batches), len(params))
        self.assertEqual(sum(batch['missingHistoricalFields'] for batch in batches), len(params)-1)
        self.assertFalse(any(item['executionEnabled'] for item in selections))
        self.assertEqual(selections[0]['fieldsInResponse'], 4)

    def test_explicit_language_source_is_used_and_drift_cannot_fall_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);language=root/'explicit-language';language.write_bytes(b'explicit source fixture')
            (root/'index.json').write_text(json.dumps({'profiles':[],'counts':{}}))
            (root/'manifest.json').write_text(json.dumps({'inputs':[],'code':[],
                'outputs':[{'path':str(root/'index.json'),'sha256':sha(root/'index.json')}]}))
            with patch.object(field_collection,'GGP_PATH',root/'missing-default'), \
                 patch.object(field_collection,'REVIEWED_LANGUAGE_SHA256',sha(language)), \
                 patch.object(field_collection,'GgpLanguage') as loader:
                result=field_collection.verified_profiles(root,language_ggp=language)
                loader.assert_called_once_with(language)
                self.assertEqual(result[2][-1]['path'],str(language.resolve()))
                language.write_bytes(b'drift')
                with self.assertRaisesRegex(ValueError,'hash-mismatch'):
                    field_collection.verified_profiles(root,language_ggp=language)
                self.assertEqual(loader.call_count,1)

    def test_exported_all_unit_definitions_decode_the_same_complete_response(self):
        from scripts.diagnostics.response_values import decode_application_response
        records = [parameter('first')['record'], parameter('second', offset=1)['record']]
        records[1]['unit'] = 'source-unit'
        groups, missing = read_groups(records)
        self.assertEqual(missing, [])
        exported = [field['decodingDefinition'] for field in groups[0]['fields']]
        values = [decode_application_response(record, bytes.fromhex('61100509'), mode='pdu') for record in exported]
        self.assertTrue(all(value['ok'] for value in values))
        self.assertEqual([value['value'] for value in values], [5, 9])
        self.assertEqual(exported[1]['unit'], 'source-unit')
        self.assertEqual(exported[0]['formula'], records[0]['formula'])
        self.assertTrue(all(not record['executionEnabled'] and record['writePayload'] is None for record in exported))

    def test_shared_response_decodes_multiple_fields_once(self):
        profile = {'parameters': [parameter('a', historical=1), parameter('b', offset=1), parameter('c', '2111')]}
        groups = request_groups(profile)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['requestHex'], '2111')  # Missing-response group first.
        self.assertEqual(groups[1]['minimumDataBytes'], 2)
        result = decode_recorded_cycle(profile, ['a', 'b'], {'2110': bytes.fromhex('61100102')}, source_kind='simulation')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['consumedResponses'], ['2110'])
        self.assertEqual([r['value']['value'] for r in result['decoded']], [1, 2])
        self.assertTrue(all(r['synthetic'] for r in result['decoded']))
        self.assertFalse(result['liveApproved'])
        self.assertFalse(result['independentLiveVerified'])

    def test_invalid_pdus_stop_before_later_groups(self):
        profile = {'parameters': [parameter('a'), parameter('b', '2111')]}
        for payload in ('6111FF', '621000', '6110', '7F2111', '7F2178'):
            result = decode_recorded_cycle(profile, ['a', 'b'], {'2110': bytes.fromhex(payload), '2111': b'\x61\x11\x00'}, source_kind='historical-capture')
            self.assertFalse(result['ok'], payload)
            self.assertEqual(result['consumedResponses'], ['2110'])
            self.assertEqual(result['decoded'], [])

    def test_missing_short_response_keeps_partial_results(self):
        profile = {'parameters': [parameter('a'), parameter('b', offset=1)]}
        result = decode_recorded_cycle(profile, ['a', 'b'], {'2110': b'\x61\x10\x07'}, source_kind='simulation')
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['decoded']), 1)
        result = decode_recorded_cycle(profile, ['a'], {}, source_kind='simulation')
        self.assertEqual(result['error'], 'missing-response')

    def test_read_request_only_and_consistent_source(self):
        for request in ('2E0010', '310010', '1410'):
            with self.assertRaises(ValueError):
                request_groups({'parameters': [parameter('a', request)]})
        p = parameter('a'); p['requestHex'] = '2111'
        with self.assertRaisesRegex(ValueError, 'parameter-request-mismatch'):
            request_groups({'parameters': [p]})

    def test_no_live_source_or_ambiguous_selection(self):
        profile = {'parameters': [parameter('a')]}
        with self.assertRaisesRegex(ValueError, 'offline-source-required'):
            decode_recorded_cycle(profile, ['a'], {}, source_kind='live')
        for selected in ([], ['a', 'a'], ['unknown']):
            with self.assertRaises(ValueError):
                decode_recorded_cycle(profile, selected, {}, source_kind='simulation')

    def test_length_is_lower_bound_and_does_not_claim_full_pdu(self):
        p = parameter('a'); p['dataSpan']['kind'] = 'lowerBound'
        group = request_groups({'parameters': [p]})[0]
        self.assertEqual(group['spanKind'], 'lowerBound')
        self.assertEqual(group['lengthClaim'], 'minimum-for-selected-fields-only')

    def test_no_device_or_database_imports(self):
        tree = ast.parse(Path(field_collection.__file__).read_text(encoding='utf-8'))
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertFalse(any(s and s.split('.')[0] in ('serial', 'sqlite3', 'subprocess', 'socket') for s in imports))

    def test_tampered_index_and_profile_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = {'profileId': 'candidate', 'status': 'identity-matched', 'parameters': [], **FLAGS}
            (root / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
            index = {'profiles': [{'profileId': 'candidate', 'status': 'identity-matched',
                'file': 'profile.json', 'sha256': sha(root / 'profile.json')}]}

            def write_index():
                (root / 'index.json').write_text(json.dumps(index), encoding='utf-8')
                (root / 'manifest.json').write_text(json.dumps({'inputs': [], 'code': [],
                    'outputs': [{'path': str(root / 'index.json'), 'sha256': sha(root / 'index.json')}]}), encoding='utf-8')

            write_index()
            (root / 'index.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'source-hash-mismatch'):
                field_collection.verified_profiles(root)
            index['profiles'][0]['file'] = '../profile.json'
            write_index()
            with self.assertRaisesRegex(ValueError, 'invalid-profile-path'):
                field_collection.verified_profiles(root)
            index['profiles'][0]['file'] = 'profile.json'
            profile['liveApproved'] = True
            (root / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
            index['profiles'][0]['sha256'] = sha(root / 'profile.json')
            write_index()
            with self.assertRaisesRegex(ValueError, 'vehicle-authority-in-offline-profile'):
                field_collection.verified_profiles(root)

    def test_existing_evidence_directory_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'output-already-exists'):
                field_collection.build_pack('unused', directory)
            self.assertEqual(list(Path(directory).iterdir()), [])


class TestAllUnitCollection(unittest.TestCase):
    def test_original_identity_prefix_suffix_produce_real_request_and_span(self):
        record = {'at': 123, 'name': 'independent source identity',
            'request_prefix_hex': '22000090f10000', 'suffix_hex': '020003010000f0',
            'formula': {'text': 'IDENTICAL:BitMask=0,BaseDataType=A_UINT32,BitLength=1,HighLow=1,Radix=10,DataType=A_UINT32;'}}
        groups, missing = read_groups([record], identity=True)
        self.assertEqual(missing, [])
        self.assertEqual(groups[0]['requestHex'], '22F190')
        self.assertEqual(groups[0]['minimumKnownBytes'], 3)
        self.assertEqual(groups[0]['fields'][0]['bitOffset'], 3)
        self.assertIsNone(groups[0]['expectedTotalBytes'])
        self.assertFalse(groups[0]['executionEnabled'])

    def test_malformed_identity_does_not_fallback_to_other_preparsed_values(self):
        record = parameter('identity', '22F190')['record']
        record.update(at=456, request_prefix_hex='00', suffix_hex='00')
        groups, missing = read_groups([record], identity=True)
        self.assertEqual(groups, [])
        self.assertEqual(missing, [{'sourceOffset':456, 'reason':'identity-layout-or-request-unresolved'}])

    def test_duplicate_menu_references_are_retained_but_not_duplicate_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);variants = root/'variants.jsonl'
            variants.write_text(json.dumps({'profile_id':'shared', 'name':'Shared', 'module':'M',
                'generation':None, 'on_target_menu':True, 'pool_records':{}}))
            coverage = {'menu_ecus':[{'ecu_id':87, 'label':'source', 'dsn_modules':['M', 'M']}]}
            result = build_all_unit_pack(root/'pack', variants=variants, coverage=coverage, expected_sha256=sha(variants))
            self.assertEqual(result['ecuVersionBindingCounts'], {'981':1, '982':1})
            self.assertEqual(result['sourceMenuBindingReferenceCounts'], {'981':2, '982':2})
            doc = json.loads((root/'pack/all-units.json').read_text())
            self.assertTrue(all(unit['variantCount']==1 for unit in doc['units']))
            self.assertTrue(all(unit['variants'][0]['sourceMenuReferenceCount']==2 for unit in doc['units']))
            self.assertFalse(doc['diagnosticSource']['present'])

    def test_all_menus_versions_and_coding_gaps_retained_without_fit_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coverage = {'menu_ecus': [{'ecu_id': 1, 'label': 'DME', 'dsn_modules': ['DME']},
                {'ecu_id': 2, 'label': 'PDK', 'dsn_modules': ['PDK']},
                {'ecu_id': 3, 'label': 'unmapped', 'dsn_modules': []}]}
            def variant(pid, module, generation):
                return {'profile_id': pid, 'name': pid, 'module': module, 'generation': generation,
                    'on_target_menu': True, 'pool_records': {
                        'measurement': {'records': [parameter('a')['record'], parameter('b')['record'], {'at': 999}]},
                        'identity': {'records': [parameter('id', '22F190')['record']]},
                        'coding': {'records': [parameter('coding')['record'], {'at': 888}]}}}
            variants = root / 'variants.jsonl'
            variants.write_text('\n'.join(json.dumps(v) for v in [variant('981-version', 'DME', '981'),
                variant('shared-version', 'PDK', None), variant('982-version', 'DME', '982'),
                variant('outside', 'DME', '991')]), encoding='utf8')
            result = build_all_unit_pack(root / 'pack', variants=variants, coverage=coverage, expected_sha256=sha(variants))
            self.assertEqual(result['menuUnitCounts'], {'981': 3, '982': 3})
            self.assertEqual(result['uniqueVersionCounts'], {'981': 2, '982': 2})
            pack = json.loads((root / 'pack/all-units.json').read_text(encoding='utf8'))
            self.assertEqual(len(pack['units']), 6)
            self.assertEqual(sum(u['status'] == 'source-mapping-missing' for u in pack['units']), 2)
            self.assertTrue(all(u['installedOnVehicle'] is None and not u['codingWriteQualified'] for u in pack['units']))
            for unit in pack['units']:
                for row in unit['variants']:
                    self.assertNotEqual(row['profileId'], 'outside')
                    self.assertEqual(row['measurementReadGroups'], 1)
                    self.assertEqual(row['measurementMissing'], 1)
                    profile = json.loads((root / 'pack' / row['file']).read_text(encoding='utf8'))
                    self.assertEqual(len(profile['measurementGroups'][0]['fields']), 2)
                    self.assertEqual(profile['coding']['requestGroups'][0]['identifierKind'], 'LID')
                    self.assertEqual(len(profile['coding']['missing']), 1)
                    self.assertFalse(profile['coding']['completeVehicleCodingScopeQualified'])
                    self.assertFalse(profile['executionEnabled'])
            manifest = json.loads((root / 'pack/manifest.json').read_text(encoding='utf8'))
            from scripts.x431_re import gag_lib
            reader = Path(gag_lib.__file__).resolve()
            reader_rows = [item for item in manifest['code'] if Path(item['path']).resolve() == reader]
            self.assertEqual(len(reader_rows), 1)
            self.assertEqual(reader_rows[0]['sha256'], sha(reader))
            self.assertTrue(all(sha(Path(item['path'])) == item['sha256'] for item in manifest['outputs']))
            with self.assertRaisesRegex(ValueError, 'already-exists'):
                build_all_unit_pack(root / 'pack', variants=variants, coverage=coverage, expected_sha256=sha(variants))
            with self.assertRaisesRegex(ValueError, 'hash-mismatch'):
                build_all_unit_pack(root / 'bad', variants=variants, coverage=coverage, expected_sha256='0' * 64)
            self.assertFalse((root / 'bad').exists())
            duplicate = root / 'duplicate.jsonl'
            record = variant('same-version', 'DME', '981')
            duplicate.write_text(json.dumps(record) + '\n' + json.dumps(record), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'duplicate-profile-id'):
                build_all_unit_pack(root / 'duplicate-pack', variants=duplicate, coverage=coverage, expected_sha256=sha(duplicate))
            self.assertFalse((root / 'duplicate-pack').exists())


if __name__ == '__main__':
    unittest.main()
