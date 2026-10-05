from copy import deepcopy
import unittest
from scripts.diagnostics.coding_read_plan import coding_read_plan, read_coding_rehearsal
from scripts.diagnostics.tests.test_realtime_preparation import field

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

if __name__ == '__main__': unittest.main()
