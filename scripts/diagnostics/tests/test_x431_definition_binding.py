import unittest
import io
import json
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.diagnostics.x431_definition_binding import (
    bind_version_candidates, audit_source_binding, audit_source_system_route,
    audit_dsn_route_context, audit_service_name_loop, bind_source_service_candidates, GUARDS, main)
from contextlib import ExitStack
import hashlib


def fixture():
    definition = {'definitionOffset': 100, 'variants': [
        {'name': 'VersionA', 'ruleOffset': 201}, {'name': 'VersionB', 'ruleOffset': 202}]}
    catalog = [{'module': 'ModuleA', 'indexError': None, 'variants': [
        {'name': 'VersionA', 'off': 301}, {'name': 'ModuleA_BaseSys', 'off': 302}]}]
    return definition, catalog


class DefinitionBindingTests(unittest.TestCase):
    def test_cli_unicode_labels_survive_a_non_utf8_windows_stdout(self):
        result = {'fieldName': '巡航控制', **GUARDS}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.bin'
            path.write_bytes(b'synthetic source fixture')
            args = ['tool', '--decoded-dsn', str(path), '--decoded-alldata', str(path),
                    '--language-ggp', str(path), '--definition-offset', '100', '--system-name', 'ModuleA']
            raw = io.BytesIO()
            output = io.TextIOWrapper(raw, encoding='gbk')
            with patch('sys.argv', args), redirect_stdout(output), patch(
                    'scripts.diagnostics.x431_definition_binding.audit_source_binding', return_value=result):
                self.assertEqual(main(), 0)
            output.flush()
            self.assertEqual(json.loads(raw.getvalue().decode('ascii')), result)

    def test_exact_source_binding_preserves_missing_and_never_selects_vehicle(self):
        definition, catalog = fixture()
        result = bind_version_candidates(definition, 'ModuleA', catalog)
        self.assertEqual([row['status'] for row in result['bindings']],
                         ['exact-source-name-match', 'variant-missing'])
        self.assertEqual(result['bindings'][0]['variantOffset'], 301)
        self.assertIsNone(result['bindings'][1]['variantOffset'])
        for report in [result, *result['bindings']]:
            for key, value in GUARDS.items():
                self.assertEqual(report[key], value)
        self.assertFalse(any(row['fallbackUsed'] for row in result['bindings']))

    def test_cross_module_names_and_case_variants_cannot_bind(self):
        definition, catalog = fixture()
        catalog.append({'module': 'ModuleB', 'indexError': None,
                        'variants': [{'name': 'VersionB', 'off': 500}]})
        self.assertEqual(bind_version_candidates(definition, 'ModuleA', catalog)['bindings'][1]['status'],
                         'variant-missing')
        report = bind_version_candidates(definition, 'modulea', catalog)
        self.assertEqual(report['byStatus'], {'module-missing': 2})

    def test_duplicate_module_or_variant_names_are_ambiguous(self):
        definition, catalog = fixture()
        catalog[0]['variants'].append({'name': 'VersionA', 'off': 400})
        report = bind_version_candidates(definition, 'ModuleA', catalog)
        self.assertEqual(report['bindings'][0]['matchedDefinitionOffsets'], [301, 400])
        self.assertEqual(report['bindings'][0]['status'], 'variant-ambiguous')
        self.assertIsNone(report['bindings'][0]['variantOffset'])
        catalog.append(catalog[0].copy())
        report = bind_version_candidates(definition, 'ModuleA', catalog)
        self.assertEqual(report['byStatus'], {'module-ambiguous': 2})

    def test_unsupported_index_and_empty_or_unknown_subset_stay_explicit(self):
        definition, catalog = fixture()
        catalog[0].update(indexError='truncated index', variants=[])
        self.assertEqual(bind_version_candidates(definition, 'ModuleA', catalog)['byStatus'],
                         {'module-index-unsupported': 2})
        self.assertEqual(bind_version_candidates(definition, 'ModuleA', catalog, [])['bindings'], [])
        with self.assertRaisesRegex(ValueError, 'outside-version-definition'):
            bind_version_candidates(definition, 'ModuleA', catalog, ['ModuleA_BaseSys'])
        for names in ['VersionA', [None], ['']]:
            with self.assertRaisesRegex(ValueError, 'candidates-invalid'):
                bind_version_candidates(definition, 'ModuleA', catalog, names)

    def test_subset_keeps_every_requested_candidate_without_promoting_identity(self):
        definition, catalog = fixture()
        report = bind_version_candidates(definition, 'ModuleA', catalog, ['VersionB', 'VersionA'])
        self.assertEqual([row['variant'] for row in report['bindings']], ['VersionA', 'VersionB'])
        self.assertIsNone(report['selectedVehicleVariant'])
        self.assertFalse(report['ecuVersionQualified'])

    def test_unreviewed_source_is_rejected_before_any_language_access(self):
        with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
            audit_source_binding(b'unknown', 0, 'ModuleA', b'unknown', 'missing')


class SystemRouteTests(unittest.TestCase):
    def test_named_caller_current_byte_maps_to_argument_without_qualifying_runtime(self):
        for caller in ('PorscheCodeMain', 'PorscheSpeMain', 'GetOdxAddForExtern', 'PorscheVOSysEnter'):
            context = {'caller': caller, 'iComCmtByte': 70, 'token63dByte': 1}
            report = audit_dsn_route_context(64, 0x0146, 0xd40, context)
            self.assertTrue(report['sourceRouteEligibleCandidate'])
            self.assertEqual(report['selectorSecondArgumentByteCandidate'], 70)
            origin = report['sourceCallerAudit']
            self.assertEqual(origin['sourceGlobalName'], 'iComCmt')
            self.assertEqual(origin['sourceGlobalBytes'], 1)
            self.assertTrue(origin['sourceGlobalIsBss'])
            self.assertFalse(origin['onDiskGotWordIsRuntimeByte'])
            self.assertFalse(origin['actualGlobalByteQualified'])
            self.assertFalse(origin['actualLoaderBindingQualified'])
            self.assertFalse(report['actualContextQualified'])
            for key, value in GUARDS.items():
                self.assertEqual(report[key], value)

    def test_unreviewed_caller_and_missing_or_mixed_byte_sources_are_rejected(self):
        for caller in ('PorscheVerInfo', 'GetOdxXmlDsnInfo', None, ['PorscheCodeMain']):
            with self.assertRaisesRegex(ValueError, 'caller-unreviewed'):
                audit_dsn_route_context(64, 0, 0xd40,
                    {'caller': caller, 'iComCmtByte': 0, 'token63dByte': 0})
        for context in ({'caller': 'PorscheCodeMain', 'token63dByte': 0},
                {'caller': 'PorscheCodeMain', 'iComCmtByte': True, 'token63dByte': 0},
                {'caller': 'PorscheCodeMain', 'iComCmtByte': 256, 'token63dByte': 0},
                {'caller': 'PorscheCodeMain', 'iComCmtByte': 0, 'token63dByte': 0, 'secondArgumentByte': 0}):
            with self.assertRaisesRegex(ValueError, 'context-fields-invalid'):
                audit_dsn_route_context(64, 0, 0xd40, context)

    def test_plain_context_cannot_invent_a_named_caller_or_global_origin(self):
        report = audit_dsn_route_context(64, 0, 0xd40, {'secondArgumentByte': 0, 'token63dByte': 0})
        self.assertIsNone(report['sourceCallerAudit'])
        self.assertTrue(report['sourceRouteEligibleCandidate'])
        self.assertFalse(report['actualContextQualified'])

    def test_named_caller_context_rejection_is_preserved_in_full_source_route(self):
        context = {'caller': 'PorscheCodeMain', 'iComCmtByte': 1, 'token63dByte': 0}
        report = self.audit(context=context)
        self.assertFalse(report['sourcePredicateAudit']['sourceRouteEligibleCandidate'])
        self.assertEqual(report['eligibleRouteCountCandidate'], 0)
        self.assertEqual(report['sourcePredicateAudit']['sourceCallerAudit']['caller'], 'PorscheCodeMain')
        self.assertFalse(report['actualDsnRouteSelectionQualified'])

    def test_context_absence_never_chooses_even_a_zero_flag_route(self):
        report = audit_dsn_route_context(64, 0, 0xd40)
        self.assertIsNone(report['sourceRouteEligibleCandidate'])
        self.assertEqual(report['status'], 'source-context-unobserved')
        self.assertFalse(report['fileEntryPredicateApplicable'])
        for key, value in GUARDS.items():
            self.assertEqual(report[key], value)

    def test_both_context_bytes_are_checked_and_zero_high_byte_is_wildcard(self):
        for flags, arg, token, expected in ((0, 0, 255, True), (0, 1, 0, False),
                (0x0100, 0, 1, True), (0x0100, 0, 0, False), (0x0146, 70, 1, True),
                (0x0146, 0, 1, False), (0xff01, 1, 255, True), (0xff01, 1, 1, False)):
            report = audit_dsn_route_context(64, flags, 0xd40,
                {'secondArgumentByte': arg, 'token63dByte': token})
            self.assertEqual(report['sourceRouteEligibleCandidate'], expected)
            self.assertFalse(report['actualContextQualified'])

    def test_other_system_id_cannot_use_matching_context_or_version_names(self):
        for context in (None, {'secondArgumentByte': 0, 'token63dByte': 0}):
            self.assertFalse(audit_dsn_route_context(65, 0, 0xd40, context)['sourceRouteEligibleCandidate'])
        with self.assertRaisesRegex(ValueError, 'system-id-mismatch'):
            self.audit(routes=[self.route(id=65)])

    def test_unknown_context_fields_and_other_families_are_rejected(self):
        for context in ({}, {'secondArgumentByte': 0}, {'secondArgumentByte': True, 'token63dByte': 0},
                {'secondArgumentByte': 256, 'token63dByte': 0},
                {'secondArgumentByte': 0, 'token63dByte': 0, 'actualContextQualified': True}):
            with self.assertRaises(ValueError):
                audit_dsn_route_context(64, 0, 0xd40, context)
        for args in ((64, -1, 0xd40), (64, 65536, 0xd40), (True, 0, 0xd40), (64, 0, 0xc40)):
            with self.assertRaises(ValueError):
                audit_dsn_route_context(*args)

    def route(self, **extra):
        return {'unk': 0, 'id': 64, 'name': 'ACC3', 'off': 100, 'at': 90, **extra}

    def audit(self, routes=None, service=None, selection=None, context=None, name='ACC3'):
        mod = 'scripts.diagnostics.x431_definition_binding.'
        definition, _ = fixture()
        routes = routes if routes is not None else [self.route()]
        service = service if service is not None else [
            {'name': 'VersionA', 'slots': [1, 2, 3, 4, 5, 6]},
            {'name': 'ACC3_BaseSys', 'slots': [7, 8, 9, 10, 11, 12]}]
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(mod[:-1],
                REVIEWED_DSN_SHA256=hashlib.sha256(b'dsn').hexdigest(),
                SYS_DATA_LOGICAL_SHA256=hashlib.sha256(b'sys').hexdigest()))
            for function, result in (
                ('parse_dsn_index', {'entries': [{'id': 13, 'name': '9x1', 'off': 10}]}),
                ('parse_l2_u16unk_id_name_off', {'entries': routes}),
                ('parse_family_index', [{'familyId': 13, 'off': 20}]),
                ('parse_systems', [{'systemKey': 0xd40, 'links': [
                    {'commOff': 30, 'serviceOff': 40, 'service': {'variants': service}}]}]),
                ('parse_version_definition', definition)):
                stack.enter_context(patch(mod + function, return_value=result))
            return audit_source_system_route(b'dsn', b'sys', 100, name,
                selection or {'familyId': 13, 'systemKey': 0xd40, 'linkIndex': 0}, context)

    def test_service_link_preserves_missing_and_ambiguous_exact_names(self):
        report = self.audit()
        self.assertEqual(report['byStatus'], {'exact-source-service-name-match': 1, 'service-name-missing': 1})
        self.assertEqual(report['versionServiceBindings'][0]['serviceMatches'][0]['sourceSlots'], [1, 2, 3, 4, 5, 6])
        report = self.audit(service=[{'name': 'VersionA', 'slots': [1]*6}]*2)
        self.assertEqual(report['versionServiceBindings'][0]['status'], 'service-name-ambiguous')
        self.assertFalse(any(row['fallbackUsed'] for row in report['versionServiceBindings']))
        self.assertIsNone(report['selectedVehicleVariant'])

    def test_same_id_multiple_eligible_routes_are_preserved_without_auto_selection(self):
        report = self.audit(routes=[self.route(), self.route(name='Other', off=200, at=95)],
            context={'secondArgumentByte': 0, 'token63dByte': 0})
        self.assertEqual(report['eligibleRouteCountCandidate'], 2)
        self.assertEqual([row['routeName'] for row in report['sameIdRouteCandidates']], ['ACC3', 'Other'])
        self.assertFalse(report['actualDsnRouteSelectionQualified'])
        self.assertIsNone(report['sourceGeneration'])
        report = self.audit(context={'secondArgumentByte': 1, 'token63dByte': 0})
        self.assertFalse(report['sourcePredicateAudit']['sourceRouteEligibleCandidate'])
        self.assertEqual(report['eligibleRouteCountCandidate'], 0)

    def test_route_pair_and_source_link_must_be_exact(self):
        for routes, name in (([self.route(name='Other')], 'ACC3'), ([self.route(off=200)], None)):
            with self.assertRaisesRegex(ValueError, 'definition-not-unique'):
                self.audit(routes=routes, name=name)
        for index in (-1, 1):
            with self.assertRaisesRegex(ValueError, 'link-index-out-of-range'):
                self.audit(selection={'familyId': 13, 'systemKey': 0xd40, 'linkIndex': index})
        self.assertEqual(self.audit(name=None)['systemName'], 'ACC3')

    def test_shared_definition_offsets_are_separated_by_id_and_flags_remain_ambiguous(self):
        report = self.audit(routes=[self.route(), self.route(id=65, at=91)])
        self.assertEqual(report['matchingSourceRouteOffsets'], [90])
        self.assertTrue(report['sourceRoutePairUnique'])
        report = self.audit(routes=[self.route(), self.route(unk=1, at=91)])
        self.assertEqual(report['matchingSourceRouteOffsets'], [90, 91])
        self.assertFalse(report['sourceRoutePairUnique'])
        self.assertIsNone(report['routeIndexOffset'])
        self.assertIsNone(report['sourcePredicateAudit'])
        self.assertEqual([row['routeFlagLowByte'] for row in report['sourcePredicateAudits']], [0, 1])
        self.assertFalse(report['actualDsnRouteSelectionQualified'])

    def test_unreviewed_sources_and_noninteger_selection_fail_before_index_parsing(self):
        with patch('scripts.diagnostics.x431_definition_binding.parse_dsn_index') as parser:
            with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
                audit_source_system_route(b'dsn', b'sys', 100, 'ACC3', {})
            parser.assert_not_called()
        for selection in ({'familyId': 13, 'systemKey': 0xd40, 'linkIndex': True},
                {'familyId': 12, 'systemKey': 0xc40, 'linkIndex': 0},
                {'familyId': 13, 'systemKey': 0xd40, 'linkIndex': 0, 'pairIndex': 0}):
            with self.assertRaises(ValueError):
                self.audit(selection=selection)


class ServiceNameLoopTests(unittest.TestCase):
    def setUp(self):
        # Keep synthetic route fixtures under the same test-only source pins.
        pins = patch.multiple('scripts.diagnostics.x431_definition_binding',
            REVIEWED_DSN_SHA256=hashlib.sha256(b'dsn').hexdigest(),
            SYS_DATA_LOGICAL_SHA256=hashlib.sha256(b'sys').hexdigest())
        pins.start()
        self.addCleanup(pins.stop)

    def service(self):
        return {'variants': [{'name': 'A', 'slots': [1, 2, 3, 4, 5, 6]},
                             {'name': 'B', 'slots': [0, 7, 8, 9, 10, 0xffffffff]}]}

    def test_source_missing_name_returns_one_but_last_slots_remain_unqualified(self):
        report = audit_service_name_loop(self.service(), 'Missing')
        self.assertEqual(report['sourceNormalReturnCandidate'], 1)
        self.assertEqual(report['sourceLastReadIndexCandidate'], 1)
        self.assertEqual(report['sourceSlotSnapshotCandidate'], [0, 7, 8, 9, 10, 0xffffffff])
        self.assertTrue(report['nativeMismatchRetainsLastSlotsCandidate'])
        self.assertFalse(report['sourceReturnOneProvesNameMatch'])
        self.assertIsNone(report['qualifiedServiceSlotsCandidate'])
        self.assertFalse(report['projectFallbackUsed'])

    def test_exact_first_or_last_match_keeps_all_six_slots_and_normal_return(self):
        for name, index in (('A', 0), ('B', 1)):
            report = audit_service_name_loop(self.service(), name)
            self.assertEqual(report['sourceFirstMatchIndexCandidate'], index)
            self.assertEqual(report['sourceLastReadIndexCandidate'], index)
            self.assertEqual(report['qualifiedServiceSlotsCandidate'], self.service()['variants'][index]['slots'])
            self.assertEqual(report['sourceNormalReturnCandidate'], 1)
            self.assertFalse(report['nativeMismatchRetainsLastSlotsCandidate'])
            self.assertFalse(report['actualCopyResultsQualified'])
            for key, value in GUARDS.items():
                self.assertEqual(report[key], value)

    def test_empty_service_table_keeps_unknown_previous_slots_without_inventing_zeros(self):
        report = audit_service_name_loop({'variants': []}, 'A')
        self.assertTrue(report['nativeEmptyTablePriorSlotsUnknown'])
        self.assertIsNone(report['sourceSlotSnapshotCandidate'])
        self.assertIsNone(report['sourceLastReadIndexCandidate'])
        self.assertIsNone(report['qualifiedServiceSlotsCandidate'])
        self.assertEqual(report['sourceNormalReturnCandidate'], 1)

    def test_duplicate_name_records_expose_native_first_copy_but_do_not_qualify(self):
        service = self.service(); service['variants'].append({'name': 'A', 'slots': [20]*6})
        report = audit_service_name_loop(service, 'A')
        self.assertEqual(report['sourceExactMatchCountCandidate'], 2)
        self.assertEqual(report['sourceFirstMatchIndexCandidate'], 0)
        self.assertEqual(report['sourceSlotSnapshotCandidate'], [1, 2, 3, 4, 5, 6])
        self.assertIsNone(report['qualifiedServiceSlotsCandidate'])
        self.assertEqual(report['status'], 'service-name-ambiguous-first-slots-unqualified')

    def test_case_prefix_and_base_names_never_alias_a_different_name(self):
        for name in ('a', 'A1', 'A_BaseSys', 'B '):
            report = audit_service_name_loop(self.service(), name)
            self.assertIsNone(report['qualifiedServiceSlotsCandidate'])
            self.assertTrue(report['nativeMismatchRetainsLastSlotsCandidate'])

    def test_unreviewed_scratch_layout_and_pointer_values_are_rejected(self):
        for slots in ([1]*5, [1]*7, [True]*6, [-1]*6, [0x100000000]*6):
            with self.assertRaisesRegex(ValueError, 'layout-unqualified'):
                audit_service_name_loop({'variants': [{'name': 'A', 'slots': slots}]}, 'A')
        for name in ('', 'A\0B', '中文', 'A'*100):
            with self.assertRaises(ValueError):
                audit_service_name_loop({'variants': [{'name': name, 'slots': [0]*6}]}, 'A')
        with self.assertRaises(ValueError):
            audit_service_name_loop({'variants': self.service()['variants']*128}, 'A')

    def test_calculated_source_candidate_needs_exact_service_match_not_native_success(self):
        route = SystemRouteTests().audit()
        exact = bind_source_service_candidates(route, ['VersionA'])
        self.assertTrue(exact['sourceBindingsCompleteCandidate'])
        missing = bind_source_service_candidates(route, ['VersionB'])
        self.assertFalse(missing['sourceBindingsCompleteCandidate'])
        row = missing['bindings'][0]
        self.assertEqual(row['serviceNameLoopAudit']['sourceNormalReturnCandidate'], 1)
        self.assertIsNone(row['qualifiedServiceSlotsCandidate'])
        self.assertFalse(row['projectFallbackUsed'])
        mixed = bind_source_service_candidates(route, ['VersionA', 'VersionB'])
        self.assertFalse(mixed['sourceBindingsCompleteCandidate'])
        self.assertEqual(len(mixed['bindings']), 2)
        self.assertFalse(mixed['ecuVersionQualified'])
        self.assertFalse(bind_source_service_candidates(route, [])['sourceBindingsCompleteCandidate'])

    def test_candidate_outside_declared_version_or_duplicate_names_is_rejected(self):
        route = SystemRouteTests().audit()
        for names in (['Unknown'], ['VersionA', 'VersionA'], [''], 'VersionA'):
            with self.assertRaises(ValueError):
                bind_source_service_candidates(route, names)
        with self.assertRaisesRegex(ValueError, 'route-audit-invalid'):
            bind_source_service_candidates(route | {'logicalSourceSha256': 'unreviewed'}, ['VersionA'])
