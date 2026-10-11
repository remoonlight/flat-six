import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.x431_diagnostic_source import DiagnosticSource, source_reference, GUARDS
from scripts.x431_re.sysdata import find_named_variant, find_named_variants, parse_cmd
from scripts.x431_re.protocol_inventory import group_services


def fixture():
    # Independent source table records: the n byte belongs to the file, not wire.
    command = {'off': 100, 'tableHex': '108900', 'wireHex': '1089', 'role': 'sessionPrecondition',
               'class': 'static-source-confirmed'}
    dtc = {'off': 80, 'style': 1, 'metadataHex': '000000000000',
           'read': [command, {'off': 104, 'tableHex': '180002FF00', 'wireHex': '1800FF00',
                            'role': 'dtcRead', 'class': 'static-source-confirmed'}],
           'clear': [{'off': 110, 'tableHex': '14FF02FFFF', 'wireHex': '14FFFFFF',
                      'role': 'dtcClear', 'class': 'static-source-confirmed'}], 'clearType': 1, 'otherPointers': [200]}
    variant = {'name': 'Exact', 'slots': [0, 0, 80, 0, 0, 0], 'dtcTableOff': 80, 'dtc': dtc}
    comm = {'class': 'static-source-confirmed', 'protocolKind': 0x10, 'pairCount': 1, 'consumedEnd': 70,
            'pairs': [{'storedTx': 0xFC00, 'storedRx': 0xFD00, 'mode': 3, 'params': [0xF123]}]}
    return {'systemKey': 0xD01, 'links': [{'index': 0, 'commOff': 20, 'serviceOff': 70,
                                        'comm': comm, 'service': {'variants': [variant]}}]}


class DiagnosticSourceTests(unittest.TestCase):
    def test_exact_name_source_order_and_every_pointer_slot_preserved(self):
        ref = source_reference(fixture(), 'Exact')
        self.assertEqual(ref['bindingStatus'], 'exact-source-name-match')
        self.assertEqual(ref['linkMatchCount'], 1)
        read = ref['links'][0]['dtc']['readCommandSlots']
        self.assertEqual([r['wireCandidateHex'] for r in read], ['1089', '1800FF00'])
        self.assertEqual([r['tableHex'] for r in read], ['108900', '180002FF00'])
        self.assertEqual([r['sourceOffset'] for r in read], [100, 104])
        self.assertEqual(ref['links'][0]['sourceSlots'], [0, 0, 80, 0, 0, 0])
        self.assertIsNone(ref['links'][0]['dtc']['expectedResponseHex'])
        self.assertFalse(ref['links'][0]['dtc']['runtimeReadSequenceQualified'])
        self.assertEqual(ref['links'][0]['communication']['pairs'][0]['storedTx'], 0xFC00)
        self.assertNotIn('txId', ref['links'][0]['communication'])
        self.assertIsNone(ref['selectedLinkIndex'])
        for row in [ref, *ref['links']]:
            for key, value in GUARDS.items():self.assertEqual(row[key], value)

    def test_shared_name_never_collapses_identical_dtc_tables_or_selects_first(self):
        system = fixture(); other = copy.deepcopy(system['links'][0]);other['index'] = 1
        other['comm']['protocolKind'] = 0x30;system['links'].append(other)
        ref = source_reference(system, 'Exact')
        self.assertEqual(ref['bindingStatus'], 'source-link-ambiguous')
        self.assertEqual([r['linkIndex'] for r in ref['links']], [0, 1])
        self.assertEqual([r['communication']['modeByte'] for r in ref['links']], [0x10, 0x30])
        self.assertEqual(len(find_named_variants(system, 'Exact')), 2)
        self.assertIsNone(find_named_variant(system, 'Exact'))

    def test_missing_name_system_case_and_base_never_fallback(self):
        for system, name in [(None, 'Exact'), (fixture(), 'exact'), (fixture(), 'BaseSys')]:
            ref = source_reference(system, name)
            self.assertEqual(ref['links'], []);self.assertEqual(ref['bindingStatus'], 'source-name-missing')
            self.assertFalse(ref['fallbackUsed'])
        self.assertEqual(find_named_variant(fixture(), 'Exact')['linkIndex'], 0)
        self.assertIsNone(find_named_variant(fixture(), 'Absent'))

    def test_null_unsupported_and_empty_commands_remain_explicit_gaps(self):
        system = fixture();dtc = system['links'][0]['service']['variants'][0]['dtc']
        dtc['read'] = [None, {'off': 105, 'class': 'not-confirmed', 'reason': 'cmd length oob'}]
        ref = source_reference(system, 'Exact');slots = ref['links'][0]['dtc']['readCommandSlots']
        self.assertEqual(len(slots), 2);self.assertEqual([r['wireCandidateHex'] for r in slots], [None, None])
        self.assertIn('dtc-read-command-layout-unresolved', ref['links'][0]['qualificationGaps'])
        dtc['read'] = []
        self.assertIn('dtc-read-command-table-empty', source_reference(system, 'Exact')['links'][0]['qualificationGaps'])
        system['links'][0]['service']['variants'][0]['dtc'] = None
        self.assertIn('dtc-table-missing', source_reference(system, 'Exact')['links'][0]['qualificationGaps'])

    def test_clear_source_is_retained_without_write_capability(self):
        ref = source_reference(fixture(), 'Exact');row = ref['links'][0]['dtc']['clearCommandSlots'][0]
        self.assertEqual(row['wireCandidateHex'], '14FFFFFF');self.assertIsNone(row['writePayload'])
        self.assertFalse(row['executionEnabled']);self.assertFalse(ref['diagnosticsQualified'])

    def test_single_sid_clear_parser_reaches_reference_without_enabling_execution(self):
        system = fixture()
        system['links'][0]['service']['variants'][0]['dtc']['clear'] = [parse_cmd(bytes.fromhex('0400FFDEAD'), 0)]
        ref = source_reference(system, 'Exact')
        row = ref['links'][0]['dtc']['clearCommandSlots'][0]
        self.assertEqual(row['wireCandidateHex'], '04')
        self.assertEqual(row['tableHex'], '0400FF')
        self.assertEqual(row['nativeDescriptorHexCandidate'], '010104')
        self.assertEqual(row['commandFormat'], 'single-byte-source-command')
        self.assertFalse(row['runtimeReadSequenceQualified'])
        self.assertFalse(row['executionEnabled']);self.assertIsNone(row['writePayload'])
        self.assertFalse(ref['diagnosticsQualified'])

    def test_unreviewed_bytes_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'unreviewed';p.write_bytes(b'unknown')
            with patch('scripts.diagnostics.x431_diagnostic_source.parse_payload') as parse:
                with self.assertRaisesRegex(ValueError, 'hash-mismatch'):DiagnosticSource(p)
            parse.assert_not_called()

    def test_change_between_hash_check_and_parse_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'changing';p.write_bytes(b'changed')
            with patch('scripts.diagnostics.x431_diagnostic_source.verify_source', return_value={'sha256': '0'*64}),\
                 patch('scripts.diagnostics.x431_diagnostic_source.parse_payload') as parse:
                with self.assertRaisesRegex(ValueError, 'changed-before-parse'):DiagnosticSource(p)
            parse.assert_not_called()

    def test_even_named_catalog_preference_requires_unique_source_link(self):
        from scripts.x431_re.protocol_inventory import EU5
        system = fixture();variant = system['links'][0]['service']['variants'][0];variant['name'] = EU5
        variant['dtc']['dtcRead'] = variant['dtc']['read'][-1]
        variant['dtc']['dtcClear'] = variant['dtc']['clear'][0]
        variant['dtc']['sessionPrecondition'] = [variant['dtc']['read'][0]]
        system['links'].append(copy.deepcopy(system['links'][0]))
        services = group_services(1, [], None, system)
        self.assertEqual(len(services['dtcRead']['nativeTemplate']['sysConstructors']), 2)
        self.assertIsNone(services['dtcRead']['nativeTemplate']['fullPayloadHex'])
        self.assertIsNone(services['dtcClear']['requestHex'])


if __name__ == '__main__':unittest.main()
