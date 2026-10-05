import unittest

from scripts.diagnostics.coding_readiness import reserved_candidates
from scripts.diagnostics.x431_capture import ADDRESSES


def record(offset, bit):
    return {'at': offset, 'readSID': 0x22, 'pid': 0x600,
        'read_request_candidate_hex': '220600', 'byteOffset': 0, 'bitOffset': bit,
        'formula': {'text': 'TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;'},
        'enumText': {'F000010F': '否', 'F0000110': '是'},
        'labels': [{'text': '编码值'}, {'text': '系列'}, {'text': '保留'}]}


class TestCodingReadiness(unittest.TestCase):
    def setUp(self):
        self.variant = {'module': 'BCM_hinten', 'generation': '9x1', 'profile_id': 'candidate',
            'pool_records': {'coding': {'records': [record(100, 2), record(150, 3)]}}}
        self.observations = [{'module': 'BCM_hinten', 'observationId': f'obs-{i}',
            'field': f'系列--保留（第{i}个可见项）', 'displayedValue': value,
            'evaluations': [{'responseFrameId': 'coding'}]} for i, value in ((1, '否'), (2, '是'))]
        self.pairs = [{'txId': ADDRESSES['BCM_hinten'], 'status': 'positive-candidate',
            'requestHex': '220600', 'dataHex': '08', 'requestFrameId': 'req', 'responseFrameId': 'coding'}]

    def test_order_hypothesis_not_vehicle_qualification(self):
        rows = reserved_candidates([self.variant], self.observations, self.pairs)
        self.assertEqual([(r['byteOffset'], r['bitOffset']) for r in rows], [(0, 2), (0, 3)])
        self.assertTrue(all(r['evaluations'][0]['matchesVisibleValue'] for r in rows))
        self.assertTrue(all(not r['nativeRowOrderProven'] and not r['fullVehicleVersionQualified'] for r in rows))

    def test_never_borrow_other_ecu_or_phase(self):
        self.pairs += [{**self.pairs[0], 'txId': 1}, {**self.pairs[0], 'responseFrameId': 'before-coding'}]
        rows = reserved_candidates([self.variant], self.observations, self.pairs)
        self.assertTrue(all(len(r['evaluations']) == 1 for r in rows))

    def test_duplicate_slots_and_ambiguous_record_count_stay_unresolved(self):
        self.variant['pool_records']['coding']['records'][1]['bitOffset'] = 2
        self.assertEqual(reserved_candidates([self.variant], self.observations, self.pairs), [])
        self.variant['pool_records']['coding']['records'].append(record(200, 4))
        self.assertEqual(reserved_candidates([self.variant], self.observations, self.pairs), [])

    def test_982_is_not_used_as_981_evidence(self):
        self.variant['generation'] = '982'
        self.assertEqual(reserved_candidates([self.variant], self.observations, self.pairs), [])
