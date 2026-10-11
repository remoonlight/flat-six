from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.definition_decoder_audit import audit_field, audit_source, DefinitionAuditor, record_hash
from scripts.diagnostics.coverage_readiness import function_rows, transport_preparation
from scripts.diagnostics.catalog import load_catalog
from scripts.diagnostics.manufacturer_acquisition import DME_PROFILE
from scripts.diagnostics.tests.test_realtime_preparation import field


class DefinitionAuditTests(unittest.TestCase):
    def test_transport_binding_does_not_leak_to_other_versions_generations_or_units(self):
        variant = {'profile_id': DME_PROFILE}
        catalog = load_catalog()
        named = transport_preparation(variant, '981', 1, catalog)
        self.assertEqual(named['session']['requestHex'], '1089')
        self.assertEqual(len(named['identityRequests']), 5)
        self.assertFalse(named['executionEnabled'])
        for profile, generation, ecu in [(DME_PROFILE, '982', 1), (DME_PROFILE, '981', 2),
                                         ('9x1:other-version', '981', 1)]:
            other = transport_preparation({'profile_id': profile}, generation, ecu, catalog)
            self.assertIsNone(other['session']); self.assertIsNone(other['address'])
            self.assertEqual(other['identityRequests'], [])

    def test_independent_integer_and_response_boundaries(self):
        result = audit_field(field())
        self.assertEqual(result['status'], 'matched', result)
        self.assertGreater(result['cases'], 4)
        self.assertEqual(result['responseChecks'], 5)
        self.assertFalse(result['vehicleVerified'])
        self.assertEqual(result['fieldStatus'], 'matched')
        self.assertEqual(result['responseStatus'], 'matched')

    def test_write_only_source_formula_is_checked_without_fabricating_a_read(self):
        rec = field()
        rec.pop('read_request_candidate_hex', None)
        rec.pop('disabledreadrequestcandidate', None)
        rec.update(readSID=0x2E, pid=0x0942, request_prefix_hex='2e000042090000')
        result = audit_field(rec)
        self.assertEqual(result['fieldStatus'], 'matched')
        self.assertEqual(result['responseStatus'], 'unverified')
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['responseChecks'], 0)
        self.assertGreater(result['cases'], 4)
        self.assertEqual(result['reason'], 'reference-read-request-unresolved:no_read_candidate')
        self.assertEqual(result['readRequestStatus'], 'unresolved')
        self.assertEqual(result['readRequestReason'], 'no_read_candidate')

    def test_bad_layout_does_not_hide_missing_read_request(self):
        rec = field(); rec.pop('read_request_candidate_hex', None); rec.pop('disabledreadrequestcandidate', None)
        rec.update(readSID=0x2E, formula='TEXTTABLE:DataType=A_UINT32,[0x00]->0x310007B4xF0002A75;BitLength=1;')
        result = audit_field(rec)
        self.assertEqual(result['fieldStatus'], 'unverified')
        self.assertEqual(result['responseStatus'], 'unverified')
        self.assertEqual(result['readRequestReason'], 'no_read_candidate')

    def test_unresolved_mapping_does_not_erase_a_resolved_read_request(self):
        rec = field()
        rec['formula'] = 'TEXTTABLE:DataType=A_UINT32,[0x00]->0x310007B4xF0002A75;BitLength=1;'
        result = audit_field(rec)
        self.assertEqual(result['readRequestStatus'], 'resolved')
        self.assertIsNone(result['readRequestReason'])
        self.assertEqual(result['fieldStatus'], 'unverified')
        self.assertEqual(result['responseStatus'], 'unverified')
        self.assertEqual(result['cases'], 0)

    def test_missing_read_and_wrong_field_value_stays_a_mismatch(self):
        rec = field(); rec.pop('read_request_candidate_hex', None); rec.pop('disabledreadrequestcandidate', None)
        rec.update(readSID=0x2E)
        with patch('scripts.diagnostics.definition_decoder_audit.decode_application_response',
                   return_value={'ok': True, 'raw': 999, 'value': 999, 'numeric': True}):
            result = audit_field(rec)
        self.assertEqual(result['fieldStatus'], 'mismatch')
        self.assertEqual(result['status'], 'mismatch')
        self.assertEqual(result['responseStatus'], 'unverified')

    def test_data_agreement_does_not_hide_response_contract_mismatch(self):
        from scripts.diagnostics.response_values import decode_application_response
        def decode(record, data, mode, **kwargs):
            return {'ok': True} if mode == 'pdu' else decode_application_response(record, data, mode, **kwargs)
        with patch('scripts.diagnostics.definition_decoder_audit.decode_application_response', side_effect=decode):
            result = audit_field(field())
        self.assertEqual(result['fieldStatus'], 'matched')
        self.assertEqual(result['responseStatus'], 'mismatch')
        self.assertEqual(result['status'], 'mismatch')

    def test_empty_reference_cases_never_pass(self):
        with patch('scripts.diagnostics.definition_decoder_audit.reference_cases', return_value=[]):
            result = audit_field(field())
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['reason'], 'reference-no-boundary-cases')

    def test_full_source_audit_separates_read_gaps_and_preserves_all_references(self):
        read = dict(field(), at=10)
        write = dict(field(), at=20, readSID=0x2E)
        write.pop('read_request_candidate_hex', None); write.pop('disabledreadrequestcandidate', None)
        profile = {'profile_id': 'fixture-only', 'pool_records': {'measurement': {'records': [read]}, 'coding': {'records': [write]}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'variants.jsonl'
            source.write_text(json.dumps(profile) + '\n')
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            output = root / 'result'
            result = audit_source(source, sha, output)
            self.assertTrue(result['prefixComplete']); self.assertFalse(result['reviewedOriginal'])
            self.assertEqual(result['sourceCounts'], {'measurement': 1, 'coding': 1})
            self.assertEqual(result['audit']['uniqueFieldStatus'], {'matched': 2})
            self.assertEqual(result['audit']['uniqueResponseStatus'], {'matched': 1, 'unverified': 1})
            rows = [json.loads(l) for l in (output / 'fields.jsonl').read_text().splitlines()]
            self.assertEqual([r['sourceOffset'] for r in rows], [10, 20])
            self.assertEqual(rows[1]['responseChecks'], 0)
            self.assertEqual(rows[1]['reason'], 'reference-read-request-unresolved:no_read_candidate')
            self.assertEqual(set(p.name for p in output.iterdir()), {'report.json', 'fields.jsonl', 'definitions.json'})
            self.assertFalse(result['codingWriteQualified']); self.assertFalse(result['ecuVersionQualified'])
            with self.assertRaises(FileExistsError): audit_source(source, sha, output)
            with self.assertRaisesRegex(ValueError, 'input-integrity'): audit_source(source, '0'*64, root / 'none')
            self.assertFalse((root / 'none').exists())

    def test_malformed_source_tail_keeps_completed_field_evidence(self):
        profile = {'profile_id': 'fixture-only', 'pool_records': {'measurement': {'records': [dict(field(), at=10)]}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'variants.jsonl'
            source.write_text(json.dumps(profile) + '\n{')
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            result = audit_source(source, sha, root / 'result')
            self.assertFalse(result['prefixComplete']); self.assertEqual(result['lastSourceLine'], 2)
            self.assertEqual(result['sourceCounts'], {'measurement': 1})
            self.assertEqual(len((root / 'result/fields.jsonl').read_text().splitlines()), 1)

    def test_empty_catalog_is_an_explicit_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'empty.jsonl'
            source.write_text(json.dumps({'profile_id': 'empty', 'pool_records': {}}) + '\n')
            result = audit_source(source, hashlib.sha256(source.read_bytes()).hexdigest(), root / 'result')
            self.assertFalse(result['prefixComplete'])
            self.assertEqual(result['error'], 'source-audit-no-fields')

    def test_wrong_production_result_is_a_mismatch(self):
        with patch('scripts.diagnostics.definition_decoder_audit.decode_application_response',
                   return_value={'ok': True, 'raw': 999, 'value': 999, 'numeric': True}):
            result = audit_field(field())
        self.assertEqual(result['status'], 'mismatch')
        self.assertTrue(result['mismatches'])

    def test_unsupported_and_excessive_layouts_are_not_passes(self):
        for formula, offset in [('COMPUCODE:BitLength=8;', 0),
                                ('IDENTICAL:BitLength=1000000;', 0),
                                (field()['formula']['text'], 4095)]:
            rec = field(); rec.update(formula=formula, byteOffset=offset)
            result = audit_field(rec)
            self.assertEqual(result['status'], 'unverified')
            self.assertTrue(result['reason'])

    def test_cache_retains_calculation_unit_enum_and_layout_boundaries(self):
        auditor = DefinitionAuditor()
        rec = field(); first = auditor.check(rec)
        same = deepcopy(rec); same.update(name='another name', at=20)
        self.assertEqual(first, auditor.check(same))
        for key, value in [('unit', 'A'), ('bitOffset', 1), ('enumText', {'01': 'other'})]:
            changed = deepcopy(rec); changed[key] = value
            self.assertNotEqual(first['definitionSha256'], auditor.check(changed)['definitionSha256'])
        self.assertEqual(auditor.summary()['uniqueDefinitionsChecked'], 4)

    def test_matrix_read_preparation_is_version_bound_and_caps_selected_fields(self):
        records = [dict(field('a', offset=index), at=index) for index in range(25)]
        unsupported = dict(field('b', offset=25), at=25, formula='COMPUCODE:BitLength=8;')
        records.append(unsupported)
        variant = {'profile_id': '9x1:other', 'name': 'other-version', 'module': 'pdk',
                   'generation': '9x1', 'pool_records': {'measurement': {'records': records}}}
        auditor = DefinitionAuditor()
        audits = {('measurement', record_hash(rec)): auditor.check(rec) for rec in records}
        row = next(r for r in function_rows(variant, '981', 2, audits=audits) if r['category'] == 'measurement')
        plan = row['readPreparation']
        self.assertEqual([len(batch) for batch in plan['selectionBatches']], [12, 12, 1])
        self.assertEqual(plan['minimumDataBytes'], 25)
        self.assertEqual(plan['positivePrefixHex'], '62F100')
        self.assertEqual(len(plan['excludedFields']), 1)
        self.assertIsNone(plan['session']); self.assertIsNone(plan['address'])
        self.assertFalse(plan['executionEnabled']); self.assertFalse(plan['liveVerified'])
        other = next(r for r in function_rows(variant, '982', 2, audits=audits) if r['category'] == 'measurement')
        self.assertNotEqual(row['rowId'], other['rowId'])


if __name__ == '__main__': unittest.main()
