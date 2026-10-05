"""Software boundaries for collection planning; no private logs or hardware."""
import ast
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics import field_collection
from scripts.diagnostics.field_collection import decode_recorded_cycle, request_groups
from scripts.diagnostics.offline_match import FLAGS, sha
from scripts.diagnostics.all_unit_collection import build_all_unit_pack


def parameter(key, request='2110', offset=0, historical=0):
    return {'id': key, 'name': key, 'unit': 'source-unit', 'requestHex': request,
        'decoderReady': True, 'dataSpan': {'kind': 'exact', 'dataMin': offset + 1},
        'byteOffset': offset, 'bitOffset': 0, 'decodedSampleCount': historical,
        'sourceOffsets': [100 + offset], 'nameResolved': True,
        'record': {'readSID': int(request[:2], 16), 'pid': int(request[2:], 16),
            'read_request_candidate_hex': request, 'byteOffset': offset, 'bitOffset': 0,
            'formula': {'text': 'IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1'}}}


class TestFieldCollection(unittest.TestCase):
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
