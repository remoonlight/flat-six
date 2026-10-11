from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.diagnostics.identity_version_capture import CaptureVersionRules, GUARDS
from scripts.diagnostics.tests import test_x431_variant_selection as variant_tests


class CaptureVersionRuleTests(unittest.TestCase):
    def fixture(self):
        definition, language = variant_tests.VariantSelectionTests().calculation_fixture()
        route = {'routeIndexOffset': 12, 'definitionOffset': definition['definitionOffset'],
                 'selectors': definition['selectors'], 'versionMatches': definition['variants']}
        profiles = [{'profileId': 'pdk-source', 'versionRoutes': [{
            'sourceSha256': definition['sourceSha256'], 'requestedEcuId': 2, 'routes': [route]}]}]
        return profiles, language

    def row(self, request='22F187', response='62F1874100424344', synthetic=False):
        return {'context': {'requestHex': request}, 'pduHex': response, 'synthetic': synthetic}

    def observed(self, collector, synthetic=False):
        collector.observe(self.row(synthetic=synthetic))
        collector.observe(self.row('22F189', '62F18901', synthetic))

    def calculate(self, collector, language):
        with patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=language):
            return collector.finish(Path('explicit-language'))

    def test_complete_capture_keeps_all_matches_prefix_rule_and_unqualified_flags(self):
        profiles, language = self.fixture();collector = CaptureVersionRules(profiles, 2)
        self.observed(collector)
        result = self.calculate(collector, language);observed, synthetic = result['reports']
        self.assertTrue(observed['calculationComplete'])
        self.assertEqual([c['variant'] for c in observed['matchingCandidates']], ['literal-match', 'prefix-match'])
        self.assertTrue(observed['matchingCandidates'][0]['matchingRules'][0]['allFieldsCompared'])
        self.assertFalse(observed['matchingCandidates'][1]['matchingRules'][0]['allFieldsCompared'])
        self.assertFalse(synthetic['calculationComplete'])
        for report in (result, observed, synthetic):
            for key, value in GUARDS.items(): self.assertEqual(report[key], value)

    def test_identical_repeats_are_bounded_and_changed_responses_never_select_a_snapshot(self):
        profiles, language = self.fixture();collector = CaptureVersionRules(profiles, 2)
        self.observed(collector)
        for _ in range(10000):collector.observe(self.row(response='62f1874100424344'))
        self.assertEqual(len(collector.inputs['observed']), 2)
        self.assertTrue(self.calculate(collector, language)['reports'][0]['calculationComplete'])
        collector.observe(self.row(response='62F1874200424344'))
        collector.observe(self.row())
        with patch('scripts.diagnostics.identity_version_capture.audit_application_version_observations') as calculate:
            result = collector.finish('not-opened')
        calculate.assert_not_called()
        self.assertEqual(result['reports'][0]['conflictingRequests'], ['22F187'])
        self.assertEqual(result['reports'][0]['matchingCandidates'], [])

    def test_synthetic_inputs_never_complete_observed_groups_or_hide_conflicts(self):
        profiles, language = self.fixture();collector = CaptureVersionRules(profiles, 2)
        collector.observe(self.row())
        collector.observe(self.row('22F189', '62F18901', True))
        self.assertFalse(any(r['calculationComplete'] for r in self.calculate(collector, language)['reports']))
        self.observed(collector, True)
        result = self.calculate(collector, language)
        self.assertFalse(result['reports'][0]['calculationComplete']);self.assertTrue(result['reports'][1]['calculationComplete'])

    def test_native_failure_or_cleanup_poison_only_its_provenance_scope(self):
        profiles, language = self.fixture();collector = CaptureVersionRules(profiles, 2)
        self.observed(collector);self.observed(collector, True)
        collector.observe(self.row(), 'native-result-error')
        self.observed(collector)
        result = self.calculate(collector, language)
        self.assertEqual(result['reports'][0]['failedRequests'], ['22F187'])
        self.assertFalse(result['reports'][0]['calculationComplete']);self.assertTrue(result['reports'][1]['calculationComplete'])

    def test_invalid_positive_echo_negative_pending_short_or_large_input_blocks_matches(self):
        for response in ('61F1874100', '62F1884100', '7F2231', '7F2278', '62F1', 'AA'*514):
            with self.subTest(response=response[:16]):
                profiles, language = self.fixture();collector = CaptureVersionRules(profiles, 2)
                collector.observe(self.row(response=response));collector.observe(self.row('22F189', '62F18901'))
                report=self.calculate(collector, language)['reports'][0]
                self.assertFalse(report['calculationComplete']);self.assertEqual(report['matchingCandidates'], [])

    def test_missing_source_rules_language_drift_and_formula_gaps_never_fallback(self):
        profiles, language=self.fixture();collector=CaptureVersionRules(profiles, 2);self.observed(collector)
        report=collector.finish('absent-language')['reports'][0]
        self.assertEqual(report['status'], 'selector-calculation-failed')
        profiles[0]['versionRoutes'][0]['routes'][0]['selectors'][0]['formulaId']=0x7f000001
        collector=CaptureVersionRules(profiles, 2);self.observed(collector)
        report=self.calculate(collector, language)['reports'][0]
        self.assertFalse(report['calculationComplete']);self.assertEqual(report['matchingCandidates'], [])
        profiles[0]['versionRoutes'][0]['routes'][0]['versionMatches']=[]
        collector=CaptureVersionRules(profiles, 2);self.observed(collector)
        self.assertEqual(collector.finish('absent-language')['reports'][0]['status'], 'source-version-rules-missing')

    def test_routes_merge_only_same_selectors_and_keep_all_source_versions(self):
        profiles, language=self.fixture();second=deepcopy(profiles[0]);second['profileId']='other-source'
        second['versionRoutes'][0]['routes'][0]['versionMatches']=second['versionRoutes'][0]['routes'][0]['versionMatches'][1:]
        collector=CaptureVersionRules(profiles+[second], 2);self.observed(collector)
        report=self.calculate(collector, language)['reports'][0]
        self.assertEqual(report['sourceProfileIds'], ['pdk-source','other-source'])
        self.assertEqual(report['sourceVariantCount'], 2)
        second['versionRoutes'][0]['routes'][0]['selectors'][0]['byteOffsetSigned']=3
        with self.assertRaisesRegex(ValueError, 'selector-conflict'):CaptureVersionRules(profiles+[second], 2)

    def test_wrong_source_unit_and_disagreeing_rules_rejected(self):
        profiles, _=self.fixture()
        with self.assertRaisesRegex(ValueError, 'unit-conflict'):CaptureVersionRules(profiles, 9)
        second=deepcopy(profiles[0]);second['versionRoutes'][0]['routes'][0]['versionMatches'][0]['rules']=['different']
        with self.assertRaisesRegex(ValueError, 'rule-conflict'):CaptureVersionRules(profiles+[second], 2)
        profiles[0]['versionRoutes'][0]['sourceSha256']='0'*64
        with self.assertRaisesRegex(ValueError, 'source-unreviewed'):CaptureVersionRules(profiles, 2)

    def test_profile_shared_by_two_units_never_borrows_other_unit_rule_routes(self):
        profiles, _=self.fixture();profiles[0]['ecuIds']=[2, 3]
        other=deepcopy(profiles[0]['versionRoutes'][0]);other['requestedEcuId']=3
        other['routes'][0]['routeIndexOffset']=48
        profiles[0]['versionRoutes'].append(other)
        current=CaptureVersionRules(profiles, 2);adjacent=CaptureVersionRules(profiles, 3)
        self.assertEqual([r['routeIndexOffset'] for r in current.routes.values()], [12])
        self.assertEqual([r['routeIndexOffset'] for r in adjacent.routes.values()], [48])

    def test_empty_source_and_unrelated_requests_never_load_language(self):
        collector=CaptureVersionRules([{'profileId':'source'}], 2)
        collector.observe(self.row('22F190', '62F1904142'))
        self.assertEqual(collector.finish('not-opened')['reports'], [])

    def test_damaged_capture_never_matches_even_when_all_requests_were_received(self):
        profiles, _=self.fixture();collector=CaptureVersionRules(profiles, 2);self.observed(collector)
        with patch('scripts.diagnostics.identity_version_capture.audit_application_version_observations') as calculate:
            result=collector.finish('not-opened', capture_complete=False)
        calculate.assert_not_called()
        self.assertFalse(result['captureIntegrityComplete'])
        self.assertTrue(all(r['status']=='capture-integrity-incomplete' and not r['matchingCandidates'] for r in result['reports']))
