import unittest
import hashlib
import tempfile
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from scripts.diagnostics import x431_variant_selection as variant_module

from scripts.diagnostics.x431_variant_selection import (
    parse_version_definition, substitute_version_rule, match_version_rules, audit_version_vectors,
    calculate_version_vectors, audit_version_observations, REVIEWED_DSN_SHA256,
    classify_version_formula_route, audit_application_version_observations,
)


def string(value):
    raw = value.encode() + b'\0'
    return len(raw).to_bytes(2, 'little') + raw


def definition_fixture():
    # Two explicit selectors; the second pair demonstrates signed-word wrap.
    data = bytearray(b'\2' + string('SW') + bytes.fromhex('220087f1000000000001ff')
                     + string('HW') + bytes.fromhex('ffff0200000000000001ff'))
    data += b'\2' + string('exact') + (128).to_bytes(4, 'little')
    data += string('prefix') + (160).to_bytes(4, 'little')
    data += bytes(128 - len(data))
    data += b'\1\0' + string('A#B')
    data += bytes(160 - len(data))
    data += b'\1\0' + string('A')
    return bytes(data)


class VariantSelectionTests(unittest.TestCase):
    def test_isolated_script_entry_finds_module_without_project_cwd_or_pythonpath(self):
        entry = Path(variant_module.__file__).resolve().parents[1] / 'run-x431-version.py'
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-B', str(entry), '--help'], cwd=directory,
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--application-responses-json', result.stdout)

    def application_rows(self):
        return [{'requestHex': '22f187', 'responseHex': '62F1874100424344', 'sourceKind': 'simulation'},
                {'requestHex': '22F189', 'responseHex': '62F18901', 'sourceKind': 'simulation'}]

    def test_application_offsets_retain_identifiers_and_preserve_all_rule_candidates(self):
        definition, language = self.calculation_fixture()
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=language):
            result = audit_application_version_observations(definition, self.application_rows(), Path('synthetic'))
        self.assertTrue(result['applicationInputsComplete']);self.assertTrue(result['calculationComplete'])
        self.assertEqual(result['versionRuleMatch']['matchingCandidateCount'], 2)
        self.assertEqual(result['calculatedFields'][0]['textSha256'], hashlib.sha256(b'A').hexdigest())
        for flag in ('ecuVersionQualified', 'freshReadProven', 'nativeResponseMapOriginProven',
                     'applicationVectorEqualsNativeMapProven', 'actualDsnRouteSelectionQualified'):
            self.assertFalse(result[flag])
        self.assertIsNone(result['selectedVehicleVariant'])

    def test_application_wrong_sid_echo_negative_pending_and_truncation_block_calculation(self):
        definition, _ = self.calculation_fixture()
        for response, reason in [('61F1874100', 'wrong_sid'), ('62F1884100', 'wrong_did'),
                ('62F1', 'truncated'), ('7F2278', 'pending'), ('7F2231', 'negative'),
                ('7F2131', 'negative_wrong_sid'), ('7F22', 'truncated_negative')]:
            rows = self.application_rows();rows[0]['responseHex'] = response
            with patch('scripts.diagnostics.x431_variant_selection.calculate_version_vectors') as calculate:
                result = audit_application_version_observations(definition, rows, Path('not-loaded'))
                calculate.assert_not_called()
            self.assertFalse(result['applicationInputsComplete']);self.assertFalse(result['calculationComplete'])
            self.assertEqual(result['applicationResponseAudits'][0]['reason'], reason)
            self.assertIsNone(result['versionRuleMatch'])

    def test_application_21_and_1a_echo_are_checked_before_formula_vectors(self):
        definition, _ = self.calculation_fixture();definition['selectors'] = definition['selectors'][:1]
        for sid, positive in ((0x21, 0x61), (0x1a, 0x5a)):
            definition['selectors'][0].update(leftWordSigned=sid, rightWordSigned=0x9f, byteOffsetSigned=1)
            row = {'requestHex': f'{sid:02X}9F', 'responseHex': f'{positive:02X}9F414243',
                   'sourceKind': 'historical-capture'}
            result = audit_application_version_observations(definition, [row])
            self.assertTrue(result['applicationInputsComplete']);self.assertFalse(result['calculationComplete'])
            row['responseHex'] = f'{positive:02X}9E414243'
            self.assertEqual(audit_application_version_observations(definition, [row])[
                'applicationResponseAudits'][0]['reason'], 'wrong_lid')

    def test_application_duplicates_unknown_inputs_live_and_unpinned_definition_rejected(self):
        definition, _ = self.calculation_fixture()
        cases = [None, self.application_rows()*2,
                 [dict(self.application_rows()[0], requestHex='22F190')],
                 [dict(self.application_rows()[0], sourceKind='live')],
                 [dict(self.application_rows()[0], vectorHex='F18741')],
                 [dict(self.application_rows()[0], responseHex='62 F18741')],
                 [dict(self.application_rows()[0], responseHex='AA'*514)]]
        for rows in cases:
            with self.assertRaises(ValueError):audit_application_version_observations(definition, rows)
        definition['sourceSha256'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
            audit_application_version_observations(definition, [])

    def test_application_missing_groups_short_payload_and_empty_definition_do_not_match(self):
        definition, _ = self.calculation_fixture()
        for rows in ([], self.application_rows()[:1], [dict(row, responseHex=row['responseHex'][:6])
                                                       for row in self.application_rows()]):
            result = audit_application_version_observations(definition, rows)
            self.assertFalse(result['applicationInputsComplete']);self.assertIsNone(result['versionRuleMatch'])
        definition['selectors'] = []
        result = audit_application_version_observations(definition, [])
        self.assertTrue(result['emptySelectorDefinition']);self.assertFalse(result['applicationInputsComplete'])

    def test_application_map_collision_unresolved_selector_and_unreviewed_formula_stay_incomplete(self):
        definition, _ = self.calculation_fixture()
        definition['selectors'][1].update(leftWordSigned=0x21, rightWordSigned=0xf18a)
        result = audit_application_version_observations(definition, self.application_rows()[:1])
        self.assertTrue(result['unresolvedSelectors']);self.assertFalse(result['applicationInputsComplete'])
        definition, _ = self.calculation_fixture()
        definition['selectors'][1].update(leftWordSigned=0x1a, rightWordSigned=0xff)
        definition['selectors'][0].update(leftWordSigned=0x21, rightWordSigned=0xf8)
        result = audit_application_version_observations(definition, [])
        self.assertTrue(result['mapKeyCollisions']);self.assertFalse(result['applicationInputsComplete'])
        definition, language = self.calculation_fixture();definition['selectors'][0]['formulaId'] = 0x7f000001
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=language):
            result = audit_application_version_observations(definition, self.application_rows(), Path('synthetic'))
        self.assertTrue(result['applicationInputsComplete']);self.assertFalse(result['calculationComplete'])
        self.assertIsNone(result['versionRuleMatch'])

    def test_reviewed_expression_does_not_become_legacy_token_family_by_dropping_a_nibble(self):
        reviewed=classify_version_formula_route(0xa0000001)
        legacy=classify_version_formula_route(0x0a000001)
        self.assertTrue(reviewed['offlineCalculatorImplemented'])
        self.assertEqual(reviewed['sourceRoute'],'reviewed-expression')
        self.assertIsNone(reviewed['legacyTokenTailFamilyTargetCandidate'])
        self.assertFalse(legacy['offlineCalculatorImplemented'])
        self.assertEqual(legacy['legacyTokenTailFamilyTargetCandidate'],0x1bc08)
        self.assertFalse(legacy['legacyTokenIdEqualsSelectorIdProven'])
        self.assertFalse(legacy['nativeFormulaRuntimeQualified'])

    def test_native_special_paths_are_reported_as_unimplemented_and_cannot_match_versions(self):
        cases=[(0x7f000001,'special-dsn-unqualified'),(0x8f000001,'special-1a87-dsn-unqualified'),
               (0xff010001,'legacy-zero-or-ff-text-unqualified'),
               (0xff010002,'legacy-three-byte-text-unqualified')]
        for fid,route in cases:
            definition,language=self.calculation_fixture();definition['selectors'][0]['formulaId']=fid
            with patch('scripts.diagnostics.x431_variant_selection._load_version_language',return_value=language):
                result=calculate_version_vectors(definition,[{'requestHex':'22F187','vectorHex':'F1874142434445'},
                    {'requestHex':'22F189','vectorHex':'F18901'}],Path('synthetic'))
            self.assertEqual(result['formulaRoutes'][0]['sourceRoute'],route)
            self.assertFalse(result['formulaRoutes'][0]['offlineCalculatorImplemented'])
            self.assertFalse(result['calculationComplete'])
            self.assertIsNone(result['versionRuleMatch'])

    def test_presence_of_unreviewed_language_id_does_not_enable_a_formula(self):
        r=classify_version_formula_route(0x80000003)
        self.assertEqual(r['sourceRoute'],'unreviewed-expression')
        self.assertFalse(r['offlineCalculatorImplemented'])
        self.assertIsNone(r['reviewedLanguageSha256'])
        self.assertFalse(r['executionEnabled'])

    def test_formula_id_is_unsigned_exact_no_wrapping_or_boolean(self):
        for fid in (-1,0x180000001,True,'80000001',None):
            with self.assertRaises(ValueError):classify_version_formula_route(fid)

    def response_observation(self, site=0x19d94):
        return {'requestHex':'22f187','producerCallSite':site,'communicationStatusByte':0,
                'communication':{'wrapper':'mode30-non-subtype4-0x11208','internal':
                    {'path':'direct','capacity':1024,'transportReturn':12,
                     'frameHex':'001200000000030000f18741'}}}

    def test_response_origin_gap_blocks_formula_and_does_not_substitute_body(self):
        definition=parse_version_definition(definition_fixture(),0)
        definition['selectors']=definition['selectors'][:1]
        with patch('scripts.diagnostics.x431_variant_selection.calculate_version_vectors') as calculate:
            result=audit_version_observations(definition,[self.response_observation()],Path('not-loaded'))
        calculate.assert_not_called()
        self.assertEqual(result['missingRequests'],['22F187'])
        self.assertEqual(result['nativeOriginAudits'][0]['initializationBytesCopiedCandidate'],6)
        self.assertFalse(result['calculationComplete'])
        self.assertFalse(result['freshReadProven'])
        self.assertIsNone(result['versionRuleMatch'])
        self.assertIsNone(result['selectedVehicleVariant'])

    def test_forced_status_prefix_is_not_a_version_identifier(self):
        definition=parse_version_definition(definition_fixture(),0)
        definition['selectors']=definition['selectors'][:1]
        result=audit_version_observations(definition,[self.response_observation(0x1a136)])
        self.assertTrue(result['requestGroups'][0]['vectorPresent'])
        self.assertFalse(result['requestGroups'][0]['identifierPrefixMatches'])
        self.assertEqual(result['errors'][0]['reason'],'version-vector-identifier-prefix-mismatch')
        self.assertFalse(result['originInputsComplete'])

    def test_observation_duplicates_unknown_requests_and_non_arrays_rejected(self):
        definition=parse_version_definition(definition_fixture(),0)
        for rows in [None,[self.response_observation()]*2,
                     [dict(self.response_observation(),requestHex='22f190')]]:
            with self.assertRaises(ValueError):audit_version_observations(definition,rows)

    def subtype4_observation(self,request,stream):
        row=b'\0'+bytes.fromhex(stream)
        return {'requestHex':request,'producerCallSite':0x19d94,'communicationStatusByte':0x7e,
                'communication':{'wrapper':'subtype4-0x10da4',
                                 'aggregateHex':(bytes([1,len(row)])+row).hex()}}

    def test_subtype4_reassembly_reaches_formula_with_complete_explicit_candidates(self):
        definition,language=self.calculation_fixture()
        observations=[self.subtype4_observation('22F187','000862F1874100424344'),
                      self.subtype4_observation('22F189','000462F18901')]
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language',return_value=language):
            result=audit_version_observations(definition,observations,Path('synthetic'))
        self.assertTrue(result['originInputsComplete'])
        self.assertTrue(result['calculationComplete'])
        self.assertEqual(result['versionRuleMatch']['matchingCandidateCount'],2)
        self.assertFalse(result['freshReadProven'])
        self.assertFalse(result['ecuVersionQualified'])
        self.assertFalse(result['executionEnabled'])
        self.assertIsNone(result['selectedVehicleVariant'])

    def test_subtype4_truncation_blocks_formula_and_short_case_fails_identifier(self):
        definition=parse_version_definition(definition_fixture(),0)
        definition['selectors']=definition['selectors'][:1]
        for stream in ('000462f187','000162'):
            with patch('scripts.diagnostics.x431_variant_selection.calculate_version_vectors') as calculate:
                result=audit_version_observations(definition,
                    [self.subtype4_observation('22f187',stream)],Path('not-loaded'))
            calculate.assert_not_called()
            self.assertFalse(result['originInputsComplete'])
            self.assertIsNone(result['versionRuleMatch'])

    def calculation_fixture(self):
        definition = parse_version_definition(definition_fixture(), 0)
        # Synthetic source/model fixture only; the real reviewed bytes are checked
        # separately by the device-source acceptance, not invented by this test.
        definition['sourceSha256'] = REVIEWED_DSN_SHA256
        definition['selectors'][0].update(byteOffsetSigned=2, formulaId=0x80000001)
        definition['selectors'][1].update(leftWordSigned=0x22, rightWordSigned=0xf189,
                                           byteOffsetSigned=2, formulaId=0xA0000001)
        definition['variants'] = [{'name': 'literal-match', 'ruleOffset': 128, 'rules': ['A#ja']},
                                  {'name': 'prefix-match', 'ruleOffset': 160, 'rules': ['A']}]
        class Language:
            def lookup_id(self, gag, fid):
                text = ('IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=40,'
                        'Encoding=ISO-8859-2,HighLow=1,DataType=A_UNICODE2STRING;' if fid == 0x80000001 else
                        'TEXTTABLE:DataType=A_UINT32,[0]->nein;[1]->ja;BitLength=1,BitMask=0,HighLow=1;')
                return {'id_hex': f'{fid:08X}', 'structured': {'fields': [{'text': text}]}}
        return definition, Language()

    def test_bytefield_version_text_matches_literal_rules_without_changing_field_values(self):
        from scripts.diagnostics.x431_values import decode_record
        definition, _ = self.calculation_fixture()
        definition['selectors'] = definition['selectors'][:1]
        definition['selectors'][0].update(leftWordSigned=0x1a, rightWordSigned=0x9f,
                                         byteOffsetSigned=1, formulaId=0x8000056e)
        class Language:
            def lookup_id(self, gag, fid):
                return {'id_hex': '8000056E', 'structured': {'fields': [{'text':
                    'IDENTICAL:Standar,BitMask=0,BaseDataType=A_BYTEFIELD,BitLength=16,'
                    'Encoding=Undefined,HighLow=1,Radix=10,DataType=A_BYTEFIELD,Precision=null;'}]}}
        language = Language()
        for raw, native_text in [('0000', '0x00 00'), ('4210', '0x42 10'), ('FFFF', '0xFF FF')]:
            with self.subTest(raw=raw):
                definition['variants'] = [{'name': 'literal-version', 'ruleOffset': 0,
                                          'rules': [native_text]}]
                with patch('scripts.diagnostics.x431_variant_selection._load_version_language',
                           return_value=language):
                    result = calculate_version_vectors(definition,
                        [{'requestHex': '1A9F', 'vectorHex': '9F' + raw}], Path('synthetic'))
                self.assertTrue(result['calculationComplete'])
                self.assertEqual(result['versionRuleMatch']['sourceFirstMatchingCandidate'], 'literal-version')
                self.assertEqual(result['calculatedFields'][0]['textSha256'],
                                 hashlib.sha256(native_text.encode('ascii')).hexdigest())
                ordinary = decode_record({'formula': {'text': language.lookup_id('', 0)[
                    'structured']['fields'][0]['text']}, 'byteOffset': 1, 'bitOffset': 0},
                    bytes.fromhex('9F' + raw))
                self.assertEqual(ordinary['value'], raw)
                self.assertFalse(result['ecuVersionQualified'])
                self.assertFalse(result['freshReadProven'])
                self.assertIsNone(result['selectedVehicleVariant'])
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=language):
            result = calculate_version_vectors(definition,
                [{'requestHex': '1A9F', 'vectorHex': '9F42'}], Path('synthetic'))
        self.assertFalse(result['calculationComplete'])
        self.assertEqual(result['calculatedFields'][0]['reason'], 'truncated')
        self.assertIsNone(result['versionRuleMatch'])

    def minmax_fixture(self, minimum=3, maximum=25, fid=0x400001E4):
        definition, _ = self.calculation_fixture()
        definition['selectors'] = definition['selectors'][:1]
        definition['selectors'][0].update(formulaId=fid)
        text = (f'IDENTICAL:MinMax,MinLength={minimum},BaseDataType=A_ASCIISTRING,'
                f'MaxLength={maximum},BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,'
                'Radix=10,DataType=A_UNICODE2STRING,Precision=null;')
        class Language:
            def lookup_id(self, gag, formula_id):
                return {'id_hex': f'{formula_id:08X}',
                        'structured': {'fields': [{'text': text}]}}
        return definition, Language(), text

    def minmax_calculate(self, definition, language, body):
        with patch.object(variant_module, '_load_version_language', return_value=language):
            return calculate_version_vectors(definition,
                [{'requestHex': '22F187', 'vectorHex': (b'\xf1\x87' + body).hex()}],
                Path('synthetic'))

    def test_version_minmax_matches_raw_extent_zero_padding_and_maximum(self):
        from scripts.diagnostics.x431_values import decode_record
        cases = [(b'ABC', 'ABC', 'initialized-zero-after-vector', 'missing_terminator'),
                 (b'A'*40 + b'\0', 'A'*25, 'max-length', 'overlong'),
                 (b'AB\0', 'AB', 'explicit-zero', 'truncated'),
                 (b'A\0', '', 'minimum-raw-extent-empty-result', 'truncated')]
        for body, expected, termination, ordinary_reason in cases:
            with self.subTest(body=body):
                definition, language, text = self.minmax_fixture()
                definition['variants'] = [{'name': 'literal-version', 'ruleOffset': 0,
                                          'rules': [expected]}]
                result = self.minmax_calculate(definition, language, body)
                self.assertTrue(result['calculationComplete'], result)
                field = result['calculatedFields'][0]
                self.assertEqual(field['textSha256'], hashlib.sha256(expected.encode()).hexdigest())
                self.assertEqual(field['sourceVersionMinMaxAudit']['terminationCandidate'], termination)
                self.assertEqual(field['sourceVersionMinMaxAudit']['availableRawBytes'], len(body))
                self.assertEqual(result['versionRuleMatch']['hasEmptyInputFields'], not bool(expected))
                self.assertFalse(result['ecuVersionQualified'])
                self.assertFalse(result['freshReadProven'])
                self.assertFalse(result['executionEnabled'])
                self.assertIsNone(result['writePayload'])
                self.assertIsNone(result['selectedVehicleVariant'])
                self.assertNotIn('text', field)
                ordinary = decode_record({'formula': {'text': text}, 'byteOffset': 2,
                                          'bitOffset': 0}, b'\xf1\x87' + body)
                self.assertEqual(ordinary['reason'], ordinary_reason)

    def test_version_minmax_short_source_names_include_zero_in_minimum(self):
        for fid, minimum, maximum, body in [(0x8000002A, 6, 26, b'BKE__\0'),
                (0x80000423, 6, 31, b'BKE__\0'), (0x80000819, 5, 26, b'PADM\0'),
                (0x8000021D, 3, 25, b'AB\0'), (0xF000000D, 3, 25, b'AB\0')]:
            with self.subTest(fid=fid):
                definition, language, _ = self.minmax_fixture(minimum, maximum, fid)
                result = self.minmax_calculate(definition, language, body)
                self.assertTrue(result['calculationComplete'])
                self.assertEqual(result['calculatedFields'][0]['textSha256'],
                                 hashlib.sha256(body[:-1]).hexdigest())
                self.assertFalse(result['freshReadProven'])

    def test_version_minmax_different_source_text_uses_ordinary_decoder(self):
        definition, language, _ = self.minmax_fixture(maximum=24)
        result = self.minmax_calculate(definition, language, b'ABC')
        self.assertFalse(result['calculationComplete'])
        self.assertEqual(result['calculatedFields'][0]['reason'], 'missing_terminator')
        self.assertNotIn('sourceVersionMinMaxAudit', result['calculatedFields'][0])

    def test_version_minmax_unreviewed_encoding_and_scratch_bounds_refused(self):
        definition, language, _ = self.minmax_fixture()
        result = self.minmax_calculate(definition, language, b'AB\xff\0')
        self.assertFalse(result['calculationComplete'])
        self.assertEqual(result['calculatedFields'][0]['reason'],
                         'version-minmax-nonascii-source-output-unreviewed')
        for changes in ({'bitOffsetByte': 1}, {'byteOffsetSigned': 488}):
            definition, language, _ = self.minmax_fixture()
            definition['selectors'][0].update(changes)
            result = self.minmax_calculate(definition, language, b'A'*510)
            self.assertFalse(result['calculationComplete'])
            self.assertEqual(result['calculatedFields'][0]['reason'],
                             'version-minmax-source-buffer-bounds-unqualified')

    def test_calculation_connects_vector_offsets_formulas_and_rules_without_qualifying_ecu(self):
        definition, language = self.calculation_fixture()
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=language):
            result = calculate_version_vectors(definition, [
                {'requestHex': '22F187', 'vectorHex': 'F1874100424344'},
                {'requestHex': '22F189', 'vectorHex': 'F18901'}], Path('synthetic'))
        self.assertTrue(result['calculationComplete'])
        self.assertEqual([f['textSha256'] for f in result['calculatedFields']],
                         [hashlib.sha256(b'A').hexdigest(), hashlib.sha256(b'ja').hexdigest()])
        self.assertEqual(result['versionRuleMatch']['matchingCandidateCount'], 2)
        self.assertNotIn('text', result['calculatedFields'][0])
        for report in [result, result['versionRuleMatch']]:
            self.assertFalse(report['executionEnabled'])
            self.assertFalse(report['ecuVersionQualified'])
            self.assertFalse(report['freshReadProven'])
            self.assertIsNone(report['selectedVehicleVariant'])
            self.assertIsNone(report['writePayload'])

    def test_incomplete_buffers_and_unreviewed_formulas_cannot_match_versions(self):
        definition, language = self.calculation_fixture()
        complete = [{'requestHex': '22F187', 'vectorHex': 'F1874142434445'},
                    {'requestHex': '22F189', 'vectorHex': 'F18900'}]
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=language):
            for vectors in [complete[:1], [dict(complete[0], vectorHex='62F1874142434445'), complete[1]],
                            [dict(complete[0], vectorHex='F18741'), complete[1]]]:
                result = calculate_version_vectors(definition, vectors, Path('synthetic'))
                self.assertFalse(result['calculationComplete'])
                self.assertIsNone(result['versionRuleMatch'])
            definition['selectors'][1]['formulaId'] = 0xff010001
            result = calculate_version_vectors(definition, complete, Path('synthetic'))
            self.assertEqual(result['calculatedFields'][1]['reason'], 'version-formula-route-unreviewed')
            self.assertIsNone(result['versionRuleMatch'])

    def test_calculation_requires_reviewed_source_bytes(self):
        with self.assertRaisesRegex(ValueError, 'definition-source-unreviewed'):
            calculate_version_vectors(parse_version_definition(definition_fixture(), 0), [], Path('missing'))
        definition, _ = self.calculation_fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'different.ggp'
            path.write_bytes(b'different language source')
            with self.assertRaisesRegex(ValueError, 'language-source-unreviewed'):
                calculate_version_vectors(definition, [], path)

    def test_vector_audit_preserves_identifier_and_never_exposes_values(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        report = audit_version_vectors(parsed, [{'requestHex': '22f187', 'vectorHex': 'F1874142'}])
        group = report['requestGroups'][0]
        self.assertTrue(group['identifierPrefixMatches'])
        self.assertEqual(group['vectorBytes'], 4)
        self.assertNotIn('vectorHex', group)
        self.assertFalse(report['fieldValuesCalculated'])
        self.assertFalse(report['freshReadProven'])
        self.assertFalse(report['executionEnabled'])
        self.assertIsNone(report['selectedVehicleVariant'])
        self.assertIsNone(report['writePayload'])
        self.assertEqual(report['unresolvedSelectors'][0]['selectorIndex'], 1)

    def test_complete_pdu_and_stripped_coding_bytes_are_not_native_vectors(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        for raw in ['62F1874142', '4142', 'F1894142']:
            report = audit_version_vectors(parsed, [{'requestHex': '22F187', 'vectorHex': raw}])
            self.assertEqual(report['errors'][0]['reason'], 'version-vector-identifier-prefix-mismatch')

    def test_map_key_collisions_are_preserved_instead_of_overwritten(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        parsed['selectors'][0].update(leftWordSigned=0x1a, rightWordSigned=0x9f)
        parsed['selectors'][1].update(leftWordSigned=0x21, rightWordSigned=0x98)
        report = audit_version_vectors(parsed, [])
        self.assertEqual(report['mapKeyCollisions'], [{'nativeResponseMapKey': 0xb9, 'requests': ['1A9F', '2198']}])
        self.assertEqual(report['missingRequests'], ['1A9F', '2198'])

    def test_missing_extra_and_outside_buffer_offsets_remain_failures(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        parsed['selectors'][0]['byteOffsetSigned'] = 4
        report = audit_version_vectors(parsed, [{'requestHex': '22F187', 'vectorHex': 'F1874142'},
                                               {'requestHex': '22F188', 'vectorHex': 'F1884142'}])
        self.assertEqual([x['reason'] for x in report['errors']],
                         ['version-vector-offset-outside-buffer', 'version-vector-outside-definition'])
        self.assertFalse(report['responseOriginAndFormulaQualified'])

    def test_ambiguous_rows_and_invalid_vectors_are_rejected(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        good = {'requestHex': '22F187', 'vectorHex': 'F1874142'}
        for rows in [[good, good], [dict(good, extra=True)], [dict(good, vectorHex='')],
                     [dict(good, vectorHex='FF' * 513)], [dict(good, vectorHex='XY')],
                     [dict(good, requestHex=None)], None]:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    audit_version_vectors(parsed, rows)

    def test_empty_selector_block_does_not_read_following_bytes(self):
        parsed = parse_version_definition(b'\0', 0)
        self.assertEqual(parsed['indexEnd'], 1)
        self.assertEqual(parsed['variants'], [])
        self.assertEqual(match_version_rules(parsed, [])['matchingCandidateCount'], 0)

    def test_fixture_layout_signed_key_and_guarded_results(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        self.assertEqual(parsed['selectors'][0]['nativeResponseMapKey'], (0x22 + 0xf187) & 65535)
        self.assertEqual(parsed['selectors'][1]['nativeResponseMapKey'], 1)
        self.assertFalse(parsed['matchesReviewedSource'])
        result = match_version_rules(parsed, ['A', 'B'])
        self.assertEqual(result['matchingCandidateCount'], 2)
        self.assertEqual(result['sourceFirstMatchingCandidate'], 'exact')
        self.assertIsNone(result['selectedVehicleVariant'])
        self.assertIsNone(result['writePayload'])
        for key in ['ecuIdentityQualified', 'ecuVersionQualified', 'freshReadProven',
                    'completeVehicleCodingScopeQualified', 'executionEnabled']:
            self.assertFalse(result[key])
        self.assertFalse(result['matchingCandidates'][1]['matchingRules'][0]['allFieldsCompared'])
        self.assertNotIn('values', result)

    def test_actual_style_zero_padded_rule_advances_by_stored_length(self):
        # Actual DSN rules use a 51-byte storage slot with a C-string prefix.
        raw = definition_fixture()
        rule = b'A#B' + bytes(48)
        data = raw[:128] + b'\1\0' + b'\x33\0' + rule
        data += b'\1\0' + string('A')
        pointer = data.index(string('prefix')) + len(string('prefix'))
        data = data[:pointer] + (183).to_bytes(4, 'little') + data[pointer + 4:]
        parsed = parse_version_definition(data, 0)
        self.assertEqual(parsed['variants'][0]['rules'], ['A#B'])
        self.assertEqual(parsed['variants'][1]['rules'], ['A'])
        self.assertEqual(match_version_rules(parsed, ['A', 'B'])['matchingCandidateCount'], 2)
        damaged = data[:132] + b'A\0B' + data[135:]
        with self.assertRaises(ValueError):
            parse_version_definition(damaged, 0)

    def test_multiple_declared_rules_remain_bounded_and_keep_later_match(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        parsed['variants'][0]['rules'] = ['X', 'A#B', 'X']
        result = match_version_rules(parsed, ['A', 'B'])
        self.assertEqual(result['matchingCandidates'][0]['matchingRules'][0]['ruleIndex'], 1)
        self.assertTrue(result['sourceDeclaredRuleLoopBounded'])
        self.assertFalse(result['nativeNonmatchLoopQualified'])

    def test_hash_segments_and_trailing_empty_behavior(self):
        values = ['A', 'B', 'C']
        cases = {'A#B': ['A', 'B'], '#B#': ['A', 'B'], '##': ['A', 'B'],
                 '###': values, 'A': ['A'], '': [], 'A#': ['A']}
        for pattern, expected in cases.items():
            with self.subTest(pattern=pattern):
                self.assertEqual(substitute_version_rule(pattern, values), expected)

    def test_empty_values_can_match_source_without_qualifying_identity(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        parsed['variants'][0]['rules'] = ['##']
        result = match_version_rules(parsed, ['', ''])
        self.assertTrue(result['hasEmptyInputFields'])
        self.assertEqual(result['matchingCandidateCount'], 1)
        self.assertFalse(result['ecuVersionQualified'])

    def test_rule_longer_than_fields_and_placeholder_overrun_do_not_match(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        parsed['variants'][0]['rules'] = ['A#B#C', '###']
        result = match_version_rules(parsed, ['A', 'B'])
        self.assertEqual(result['matchingCandidateCount'], 1)  # Prefix is retained.
        self.assertEqual(result['errors'][0]['error'], 'version-rule-placeholder-out-of-range')

    def test_no_match_does_not_create_base_fallback(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        result = match_version_rules(parsed, ['X', 'Y'])
        self.assertEqual(result['matchingCandidateCount'], 0)
        self.assertIsNone(result['selectedVehicleVariant'])
        self.assertFalse(result['nativeNonmatchLoopQualified'])

    def test_malformed_source_and_unterminated_strings_are_rejected(self):
        raw = definition_fixture()
        for invalid in [b'', raw[:10], raw[:129], raw.replace(b'SW\0', b'SWX'),
                        raw[:128] + b'\xff\xff' + raw[130:]]:
            with self.subTest(size=len(invalid)):
                with self.assertRaises(ValueError):
                    parse_version_definition(invalid, 0)
        for offset in [-1, len(raw), True]:
            with self.assertRaises(ValueError):
                parse_version_definition(raw, offset)

    def test_untrusted_value_types_bounds_and_control_bytes_are_rejected(self):
        parsed = parse_version_definition(definition_fixture(), 0)
        for values in [['A'], ['A', None], ['A', '\0'], ['A', '中'], ['A', 'X' * 256]]:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    match_version_rules(parsed, values)
        for pattern in ['X' * 256, 'A\0B', '中']:
            with self.assertRaises(ValueError):
                substitute_version_rule(pattern, ['A'])


if __name__ == '__main__':
    unittest.main()
