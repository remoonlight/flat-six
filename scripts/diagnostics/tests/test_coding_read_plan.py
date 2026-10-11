from copy import deepcopy
import unittest
from scripts.diagnostics.coding_read_plan import coding_read_plan, read_coding_rehearsal, coding_read_observation
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_coding_numeric import linear
from scripts.diagnostics.tests.test_coding_fields import enum
from scripts.diagnostics.coding_fields import coding_field_readiness

class CodingReadTests(unittest.TestCase):
    def setUp(self):
        self.variant = {'profile_id': 'known', 'module': 'DME', 'name': 'version', 'generation': '981',
            'pool_records': {'coding': {'records': [field('a'), field('b', offset=2)]}}}
        self.identity = {'profileId': 'known', 'generation': '981', 'vin': 'WP0ZZZ98ZES123456',
                         'ecu': 'dme', 'hardware': 'hw', 'software': 'sw'}

    def test_group_scope_not_full_length_or_vehicle_baseline(self):
        plan = coding_read_plan(self.variant)
        self.assertEqual(len(plan['requestGroups']), 1)
        self.assertEqual(plan['requestGroups'][0]['minimumKnownBytes'], 3)
        self.assertIsNone(plan['requestGroups'][0]['expectedTotalBytes'])
        self.assertFalse(plan['completeVehicleCodingScopeQualified'])
        requests = []
        def receive(hx): requests.append(hx); return bytes.fromhex('62F100010203FFFF')
        out = read_coding_rehearsal(plan, self.identity, receive)
        self.assertTrue(out['ok']); self.assertEqual(requests, ['22F100'])
        self.assertEqual(out['blocks'][0]['dataHex'], '010203FFFF')
        self.assertFalse(out['baselineCreated']); self.assertFalse(out['completeOriginalBackup'])

    def test_live_wrong_identity_and_incomplete_definition_never_read(self):
        def forbidden(_): raise AssertionError('read called')
        plan = coding_read_plan(self.variant)
        self.assertEqual(read_coding_rehearsal(plan, self.identity, forbidden, mode='live')['error'],
                         'coding-read-transport-unqualified')
        identity = {**self.identity, 'profileId': 'other'}
        self.assertEqual(read_coding_rehearsal(plan, identity, forbidden)['error'], 'coding-read-identity-mismatch')
        self.variant['pool_records']['coding']['records'][1]['byteOffset'] = None
        plan = coding_read_plan(self.variant)
        self.assertFalse(plan['definitionCoverageComplete'])
        self.assertEqual(read_coding_rehearsal(plan, self.identity, forbidden)['error'], 'coding-read-definition-incomplete')

    def test_lid_and_did_not_conflated(self):
        rec = field('a', '21A4'); rec.update(readSID=0x21, pid=0xA4)
        self.variant['pool_records']['coding']['records'] = [rec]
        group = coding_read_plan(self.variant)['requestGroups'][0]
        self.assertEqual((group['identifierKind'], group['identifierHex']), ('LID', 'A4'))

    def test_enum_and_numeric_preview_counts_do_not_qualify_native_editor(self):
        records = [enum(), linear(), linear(lower='null')]
        self.variant['pool_records']['coding']['records'] = records
        plan = coding_read_plan(self.variant)
        self.assertEqual(plan['previewReadiness'], {'named-options-ready': 1,
            'source-bounded-linear-preview-ready': 1, 'numeric-complete-source-bounds-required': 1})
        self.assertEqual(plan['editorReadiness'], plan['previewReadiness'])
        self.assertEqual(plan['editorReadinessScope'], 'offline-field-preview-only')
        self.assertFalse(plan['codingWriteQualified'])
        self.assertFalse(plan['nativeEditingBehaviorVerified'])
        fields = plan['requestGroups'][0]['fields']
        self.assertEqual([f['offlinePreviewReady'] for f in fields], [True, True, False])
        self.assertEqual([f['previewKind'] for f in fields], ['named-options',
            'source-bounded-linear', 'source-bounded-linear'])
        self.assertEqual(fields[1]['namedOptionCount'], 0)
        for f in fields:
            self.assertFalse(f['codingWriteQualified'])
            self.assertFalse(f['editorEnabled'])
            self.assertFalse(f['propertyMeaningQualified'])

    def test_native_property_values_do_not_promote_preview_to_write_qualification(self):
        for value in (None, 0, 1, 255):
            record = linear() | {'propertyByte': value, 'property_status': 'unresolved'}
            result = coding_field_readiness(record)
            self.assertTrue(result['offlinePreviewReady'])
            self.assertFalse(result['propertyMeaningQualified'])
            self.assertFalse(result['editorEnabled'])
            self.assertFalse(result['codingWriteQualified'])

    def test_numeric_source_failures_are_specific_and_bad_read_still_stops_first(self):
        for record, blocker in ((linear(lower='null'), 'numeric-complete-source-bounds-required'),
                                (linear(xb='0'), 'numeric-inverse-not-unique'),
                                (linear() | {'name': ''}, 'numeric-field-name-unresolved'),
                                (linear() | {'pid': 0xF101}, 'coding-read-request-unresolved')):
            with self.subTest(blocker=blocker):
                result = coding_field_readiness(record)
                self.assertFalse(result['offlinePreviewReady'])
                self.assertEqual(result['editorBlocker'], blocker)

    def test_partial_failure_keeps_complete_prior_block_no_baseline(self):
        self.variant['pool_records']['coding']['records'][1] = field('b', '22F101')
        plan = coding_read_plan(self.variant)
        out = read_coding_rehearsal(plan, self.identity,
            lambda hx: bytes.fromhex('62F10001') if hx == '22F100' else bytes.fromhex('7F2231'))
        self.assertFalse(out['ok']); self.assertEqual(len(out['blocks']), 1)
        self.assertEqual(out['missing'], ['22F101']); self.assertFalse(out['baselineCreated'])

    def test_cancel_and_short_block(self):
        plan = coding_read_plan(self.variant)
        out = read_coding_rehearsal(plan, self.identity, lambda _: bytes.fromhex('62F1000000'))
        self.assertEqual(out['error'], 'coding-read-short-block')
        self.assertEqual(read_coding_rehearsal(plan, self.identity, lambda _: None, cancelled=lambda: True)['error'], 'cancelled')

    def test_imported_complete_response_preserves_trailing_bytes_and_never_baseline(self):
        plan = coding_read_plan(self.variant)
        identity = {key: value for key, value in self.identity.items() if key != 'profileId'}
        response = {'requestHex': '22F100', 'pduHex': '62F100010203FFFF', 'receivedUtc': '2026-10-06T00:00:00Z'}
        out = coding_read_observation(plan, identity, [response])
        self.assertTrue(out['allDefinedGroupsReceived'])
        self.assertEqual(out['blocks'][0]['dataHex'], '010203FFFF')
        self.assertFalse(out['baselineCreated']); self.assertFalse(out['completeOriginalBackup'])
        self.assertFalse(out['freshReadProven'])
        for malformed in ([response, response], [{**response, 'requestHex': '22F101'}],
                          [{**response, 'pduHex': '62F1000'}], [{**response, 'receivedUtc': '2026-10-06'}]):
            with self.assertRaises(ValueError): coding_read_observation(plan, identity, malformed)
        out = coding_read_observation(plan, identity, [{**response, 'pduHex': '7F2231'}])
        self.assertEqual(out['missing'], ['22F100']); self.assertEqual(len(out['failures']), 1)
        self.assertEqual(out['failures'][0]['pduHex'], '7F2231')
        self.assertFalse(out['allDefinedGroupsReceived'])
        out = coding_read_observation(plan, identity, [{**response, 'pduHex': '62F10001'}])
        self.assertEqual(out['failures'][0]['reason'], 'coding-read-short-block')

if __name__ == '__main__': unittest.main()
