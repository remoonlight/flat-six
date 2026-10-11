"""Mixed coding services use complete source-version context, never a global alias."""
from copy import deepcopy
import struct
import unittest

from scripts.x431_re.request_fields import (
    CODING_DATA_SHA256, coding_profile_variant, derived_read_request,
)
from scripts.diagnostics.response_values import request_from_record, normalize_response
from scripts.diagnostics.coding_read_plan import coding_read_plan, read_coding_rehearsal
from scripts.diagnostics.definition_decoder_audit import DefinitionAuditor


def record(sid, pid, at):
    prefix = bytes([sid]) + struct.pack('<HI', 0x2E, pid)
    return {'at': at, 'request_prefix_hex': prefix.hex(), 'readSID': sid,
        'companionSID': 0x2E, 'pid': pid,
        'read_request_candidate_hex': derived_read_request(prefix)['request_hex'],
        'source': {'file': '9X1_ALLDATA.BIN.dec', 'sha256': CODING_DATA_SHA256,
                   'module': 'test-source-module', 'variant': 'test-source-version'},
        'name': 'synthetic field', 'byteOffset': 0, 'bitOffset': 0,
        'formula': 'IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT8,BitLength=8,'
                   'Encoding=Undefined,HighLow=1,Radix=10,DataType=A_UINT8,Precision=0;'}


def variant(records):
    return {'profile_id': 'source-fixture', 'module': 'test-source-module', 'name': 'test-source-version',
            'generation': '981', 'pool_records': {'coding': {'records': records,
                'pointer': records[0]['at'] - 4 if records else 0, 'count': len(records)}}}


class CodingReadOriginTests(unittest.TestCase):
    def test_mixed_prefixes_bind_to_first_service_without_changing_originals(self):
        original = variant([record(0x22, 0xF100, 100), record(0x2E, 0x0318, 140),
                            record(0x31, 0x25F4, 180), record(0x21, 9, 220)])
        before = deepcopy(original)
        prepared = coding_profile_variant(original)
        self.assertEqual(original, before)
        self.assertEqual(coding_profile_variant(prepared), prepared)
        records = prepared['pool_records']['coding']['records']
        self.assertEqual([r['read_request_candidate_hex'] for r in records],
                         ['22F100', '220318', '2225F4', '220009'])
        self.assertEqual([r['sourceReadRequestCandidateHex'] for r in records],
                         ['22F100', None, None, '2109'])
        for source, current in zip(original['pool_records']['coding']['records'], records):
            self.assertEqual(current['request_prefix_hex'], source['request_prefix_hex'])
            self.assertEqual(current['readSID'], source['readSID'])
            req = request_from_record(current)
            self.assertTrue(req['ok'], req)
            self.assertEqual(req['sid'], 0x22)
            self.assertFalse(req['alias31to22'])
            self.assertFalse(req['readOrigin']['executionEnabled'])
            self.assertFalse(req['readOrigin']['actualDynamicBindingQualified'])
        self.assertFalse(request_from_record(original['pool_records']['coding']['records'][2])['ok'])

    def test_native_request_order_and_source_prefixes_remain_in_plan(self):
        plan = coding_read_plan(variant([record(0x22, 0xF100, 100), record(0x21, 9, 140),
                                         record(0x2E, 0xF100, 180)]))
        self.assertEqual([g['requestHex'] for g in plan['requestGroups']], ['220009', '22F100'])
        self.assertEqual(len(plan['requestGroups'][1]['fields']), 2)
        changed = plan['requestGroups'][0]['fields'][0]
        self.assertEqual(changed['originalRecordReadSID'], 0x21)
        self.assertEqual(changed['originalCandidateHex'], '2109')
        self.assertEqual(plan['requestGroups'][0]['readOrigin']['firstRecordOffset'], 100)
        self.assertFalse(plan['executionEnabled'])
        self.assertFalse(plan['completeVehicleCodingScopeQualified'])
        self.assertFalse(plan['writeRecipeQualified'])

    def test_responses_require_the_profile_service_and_full_identifier(self):
        prepared = coding_profile_variant(variant([record(0x22, 0xF100, 100), record(0x21, 9, 140)]))
        rec = prepared['pool_records']['coding']['records'][1]
        self.assertTrue(normalize_response(bytes.fromhex('620009AA'), rec, 'pdu')['ok'])
        for pdu in ('6109AA', '62000AAA', '7F2231', '7F2278'):
            self.assertFalse(normalize_response(bytes.fromhex(pdu), rec, 'pdu')['ok'], pdu)
        self.assertEqual(normalize_response(bytes.fromhex('620009AA'), rec, 'pdu')['payload'], b'\xAA')

    def test_source_context_and_original_candidates_cannot_be_swapped(self):
        source = variant([record(0x22, 0xF100, 100), record(0x21, 9, 140)])
        for mutate in (
            lambda r: r['source'].update(variant='different'),
            lambda r: r.update(readSID=0x22),
            lambda r: r.update(read_request_candidate_hex='2108'),
            lambda r: r.update(at=90),
        ):
            bad = deepcopy(source);mutate(bad['pool_records']['coding']['records'][1])
            with self.assertRaises(ValueError):coding_profile_variant(bad)
        bad = deepcopy(source);bad['pool_records']['coding']['records'].reverse()
        with self.assertRaises(ValueError):coding_profile_variant(bad)
        prepared = coding_profile_variant(source)
        rec = prepared['pool_records']['coding']['records'][1]
        for mutate in (
            lambda r: r['codingReadOrigin'].update(executionEnabled=True),
            lambda r: r['codingReadOrigin'].update(readSID=True),
            lambda r: r['codingReadOrigin'].update(firstPrefixHex='21000009000000'),
            lambda r: r['codingReadOrigin'].update(recordIndex=0),
            lambda r: r['codingReadOrigin'].update(variant='different'),
            lambda r: r.update(sourceReadRequestCandidateHex='2108'),
            lambda r: r.update(requestHex='2109'),
            lambda r: r.update(rawSID=0x31, wireSID=0x22),
        ):
            bad = deepcopy(rec);mutate(bad)
            self.assertFalse(request_from_record(bad)['ok'], bad)

    def test_origin_validation_runs_before_shared_field_audit_cache(self):
        rec = coding_profile_variant(variant([record(0x22, 0xF100, 100)]))['pool_records']['coding']['records'][0]
        auditor = DefinitionAuditor()
        self.assertEqual(auditor.check(rec)['status'], 'matched')
        bad = deepcopy(rec);bad['codingReadOrigin']['module'] = 'other'
        checked = auditor.check(bad)
        self.assertEqual(checked['fieldStatus'], 'matched')
        self.assertEqual(checked['readRequestStatus'], 'unresolved')
        self.assertEqual(checked['status'], 'unverified')
        # Same pure field semantics and request bytes, different source context.
        detached = record(0x22, 0xF100, 100)
        detached.update(propertyByte=1, suffix_hex='01000000070000f0')
        unreviewed = deepcopy(detached);unreviewed['source']['sha256'] = '0'*64
        auditor = DefinitionAuditor()
        self.assertEqual(auditor.check(unreviewed)['status'], 'matched')
        self.assertEqual(auditor.check(detached)['readRequestStatus'], 'unresolved')

    def test_truncated_or_shifted_table_cannot_supply_its_own_first_service(self):
        source = variant([record(0x22, 0xF100, 100), record(0x21, 9, 140)])
        for mode in ('drop-first', 'drop-last', 'drop-all', 'move-table', 'missing-count'):
            bad = deepcopy(source);table = bad['pool_records']['coding']
            if mode == 'drop-first':table['records'].pop(0)
            elif mode == 'drop-last':table['records'].pop()
            elif mode == 'drop-all':table['records'].clear()
            elif mode == 'move-table':table['pointer'] += 1
            else:table.pop('count')
            with self.assertRaisesRegex(ValueError, 'complete-table-required'):
                coding_profile_variant(bad)

    def test_one_byte_service_remains_one_byte_and_overflow_never_truncates(self):
        rec = coding_profile_variant(variant([record(0x21, 0xA4, 100)]))['pool_records']['coding']['records'][0]
        self.assertEqual(request_from_record(rec)['request'], b'\x21\xA4')
        self.assertTrue(normalize_response(b'\x61\xA4\xAA', rec, 'pdu')['ok'])
        with self.assertRaisesRegex(ValueError, 'pid_overflow_u8'):
            coding_profile_variant(variant([record(0x21, 0xA4, 100), record(0x22, 0x0942, 140)]))

    def test_missing_unreviewed_or_detached_context_never_supplies_a_request(self):
        rec = record(0x2E, 0x0942, 100)
        with self.assertRaisesRegex(ValueError, 'first-service-unreviewed'):
            coding_profile_variant(variant([rec]))
        rec['source']['sha256'] = '0'*64
        self.assertEqual(coding_profile_variant(variant([rec])), variant([rec]))
        self.assertFalse(request_from_record(rec)['ok'])
        rec['codingReadOrigin'] = {}
        with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
            coding_profile_variant(variant([rec]))
        detached = record(0x21, 9, 140)
        detached.update(propertyByte=1, suffix_hex='01000000070000f0')
        self.assertEqual(request_from_record(detached)['reason'], 'coding-profile-read-origin-required')

    def test_source_context_cannot_enable_live_rehearsal(self):
        plan = coding_read_plan(variant([record(0x22, 0xF100, 100)]))
        def forbidden(_):raise AssertionError('unexpected vehicle callback')
        result = read_coding_rehearsal(plan, {}, forbidden, mode='live')
        self.assertEqual(result['error'], 'coding-read-transport-unqualified')
        self.assertFalse(result['completeOriginalBackup'])
        self.assertFalse(result['baselineCreated'])


if __name__ == '__main__':unittest.main()
