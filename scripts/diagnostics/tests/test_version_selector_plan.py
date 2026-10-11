import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.version_selector_plan import VersionSelectorSource, GUARDS
from scripts.diagnostics.x431_variant_selection import REVIEWED_DSN_SHA256
from scripts.diagnostics.all_unit_collection import build_all_unit_pack
from scripts.diagnostics.field_collection import build_pack
from scripts.diagnostics.precar import prepare


def diagnostic():
    pair = {'pairIndex': 0, 'subtypeByte': 3, 'parameterIds': [1, 2, 1, 3]}
    return {'systemKey': 0xd09, 'bindingStatus': 'exact-source-name-match', 'links': [
        {'linkIndex': 0, 'serviceVariantIndex': 1, 'communicationSourceOffset': 100,
         'qualificationGaps': [], 'communication': {'class': 'static-source-confirmed',
                                                    'modeByte': 0x10, 'pairs': [pair]}}]}


def versions():
    return {'sourceSha256': REVIEWED_DSN_SHA256, 'bindingStatus': 'exact-source-version-rule-match',
        'routes': [{'routeIndexOffset': 12, 'definitionOffset': 200, 'versionMatchCount': 1,
          'selectors': [{'leftWordSigned': 0x22, 'rightWordSigned': 0xf1a2,
                         'byteOffsetSigned': 2, 'bitOffsetByte': 0, 'formulaId': 0x80000002}]}]}


def lookup(_blob, command_id):
    body = {1: '010322f1a2', 2: '0003108900', 3: '010414ffffff'}[command_id]
    return {'recordFound': True, 'commandRecordIndex': command_id, 'decodedPayloadHex': body,
            'languageSourceSha256': 'fixture-language', 'commandGagSha256': 'fixture-command',
            'decodedPayloadSha256': hashlib.sha256(bytes.fromhex(body)).hexdigest()}


class SelectorTests(unittest.TestCase):
    def source(self, root, diag=None, version=None):
        path = root/'language';path.write_bytes(b'fixture-only')
        diagnostic_ref = diagnostic() if diag is None else diag
        version_ref = versions() if version is None else version
        d = SimpleNamespace(reference=lambda ecu, name: diagnostic_ref)
        v = SimpleNamespace(reference=lambda ecu, module, name, **kwargs: version_ref)
        with patch('scripts.diagnostics.version_selector_plan.REVIEWED_LANGUAGE_SHA256',
                   hashlib.sha256(path.read_bytes()).hexdigest()):
            source=VersionSelectorSource(d, v, path)
        source.gap_inspector=SimpleNamespace(reference=lambda ecu,name,request: {'status':'fixture-only'})
        return source

    def test_exact_read_occurrences_preserve_indices_duplicates_and_postprocessing_gap(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                'scripts.diagnostics.version_selector_plan._lookup_command', side_effect=lookup) as read:
            source = self.source(Path(temp));result = source.reference(9, 'M', 'V')
            self.assertTrue(result['allSelectorRequestsHaveSourceOccurrences'])
            group = result['routes'][0]['requestGroups'][0]
            self.assertEqual(group['requestCandidateHex'], '22F1A2')
            self.assertEqual([o['requestIndex'] for o in group['sourceOccurrences']], [0, 2])
            self.assertFalse(group['sourceOccurrences'][0]['sourceCommand']['postprocessingAudit']['sourcePostprocessingComplete'])
            self.assertIn('command-string-termination-unqualified', result['routes'][0]['qualificationGaps'])
            self.assertEqual(read.call_count, 3)  # Repeated IDs share only source lookup, not occurrences.
            self.assertNotIn('14FFFFFF', str(result));self.assertNotIn('108900', str(result))
            for key, value in GUARDS.items():self.assertEqual(result[key], value)

    def test_other_modes_and_unbounded_communication_records_cannot_cover_reads(self):
        for mode, subtype, trailing in [(0x60, 3, False), (0x30, 3, False), (0x50, 3, False), (0x10, 3, True)]:
            d = diagnostic();d['links'][0]['communication']['modeByte'] = mode
            d['links'][0]['communication']['pairs'][0]['subtypeByte'] = subtype
            if trailing:d['links'][0]['qualificationGaps'].append('communication-record-trailing-bytes-unresolved')
            with tempfile.TemporaryDirectory() as temp, patch(
                    'scripts.diagnostics.version_selector_plan._lookup_command') as read:
                result = self.source(Path(temp), d).reference(9, 'M', 'V')
                self.assertFalse(result['allSelectorRequestsHaveSourceOccurrences']);read.assert_not_called()

    def test_reviewed_mode30_and_mode50_lookup_paths_cover_only_source_reads(self):
        for mode, subtype, site in ((0x30, 1, 0x19c40), (0x30, 4, 0x19c40), (0x50, 5, 0x19e20)):
            d = diagnostic();d['links'][0]['communication']['modeByte'] = mode
            d['links'][0]['communication']['pairs'][0]['subtypeByte'] = subtype
            with tempfile.TemporaryDirectory() as temp, patch(
                    'scripts.diagnostics.version_selector_plan._lookup_command', side_effect=lookup):
                result = self.source(Path(temp), d).reference(9, 'M', 'V')
                self.assertTrue(result['allSelectorRequestsHaveSourceOccurrences'])
                route = result['routes'][0]
                self.assertNotIn('command-response-association-mode-unreviewed', route['qualificationGaps'])
                self.assertNotIn('command-lookup-caller-mode-unreviewed', route['qualificationGaps'])
                rows = route['requestGroups'][0]['sourceOccurrences']
                self.assertEqual([r['requestIndex'] for r in rows], [0, 2])
                self.assertEqual(rows[0]['enterSysCallerSourceAudit']['lookupCallSite'], site)
                self.assertFalse(result['runtimeSourcePathQualified'])
                self.assertFalse(rows[0]['enterSysCallerSourceAudit']['actualLookupCallQualified'])

    def test_missing_names_and_duplicate_links_or_routes_never_choose_first(self):
        for case in ('missing', 'links', 'routes', 'version'):
            d, v = diagnostic(), versions()
            if case == 'missing':d.update(bindingStatus='source-name-missing', links=[])
            if case == 'links':
                d['links'].append(copy.deepcopy(d['links'][0]));d['links'][1]['linkIndex'] = 1
                d['bindingStatus'] = 'source-link-ambiguous'
            if case == 'routes':
                v['routes'].append(copy.deepcopy(v['routes'][0]));v['bindingStatus'] = 'source-route-or-version-ambiguous'
            if case == 'version':v['bindingStatus'] = 'source-version-name-missing';v['routes'][0]['versionMatchCount'] = 0
            with tempfile.TemporaryDirectory() as temp, patch(
                    'scripts.diagnostics.version_selector_plan._lookup_command', side_effect=lookup):
                result = self.source(Path(temp), d, v).reference(9, 'M', 'V')
                self.assertFalse(result['allSelectorRequestsHaveSourceOccurrences']);self.assertFalse(result['fallbackUsed'])
                self.assertEqual(result['bindingStatus'], 'source-binding-missing-or-ambiguous')
                if case == 'links':self.assertEqual(result['routes'][0]['requestGroups'][0]['sourceOccurrenceCount'], 4)

    def test_missing_read_unresolved_selector_and_map_collision_are_separate_gaps(self):
        for case in ('read', 'unresolved', 'collision'):
            v = versions()
            if case == 'read':v['routes'][0]['selectors'][0]['rightWordSigned'] = 0xf190
            if case == 'unresolved':v['routes'][0]['selectors'][0]['leftWordSigned'] = 0x31
            if case == 'collision':
                v['routes'][0]['selectors'][0].update(leftWordSigned=0x21, rightWordSigned=0xf8)
                other=copy.deepcopy(v['routes'][0]['selectors'][0]);other.update(leftWordSigned=0x1a, rightWordSigned=0xff)
                v['routes'][0]['selectors'].append(other)
            with tempfile.TemporaryDirectory() as temp, patch(
                    'scripts.diagnostics.version_selector_plan._lookup_command', side_effect=lookup):
                result=self.source(Path(temp), version=v).reference(9, 'M', 'V')
                self.assertFalse(result['allSelectorRequestsHaveSourceOccurrences'])
                route=result['routes'][0]
                if case=='read':self.assertIsNotNone(route['requestGroups'][0]['missingSourceAudit'])
                if case=='unresolved':self.assertTrue(route['unresolvedSelectors'])
                if case=='collision':self.assertTrue(route['mapKeyCollisions'])

    def test_missing_incomplete_and_nonread_command_never_supply_read_bytes(self):
        for body in ('', '010322f1', '010414ffffff', '010422f1a2aa'):
            with tempfile.TemporaryDirectory() as temp, patch(
                    'scripts.diagnostics.version_selector_plan._lookup_command', return_value={
                        'recordFound': bool(body), 'commandRecordIndex': 1 if body else None,
                        'decodedPayloadHex': body, 'languageSourceSha256': 'fixture', 'commandGagSha256': 'fixture'}):
                result=self.source(Path(temp)).reference(9, 'M', 'V')
                self.assertFalse(result['allSelectorRequestsHaveSourceOccurrences'])

    def test_unreviewed_or_changed_language_rejected_before_command_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'language';path.write_bytes(b'unknown')
            with self.assertRaisesRegex(ValueError, 'hash-mismatch'):VersionSelectorSource(None, None, path)
            with patch('scripts.diagnostics.version_selector_plan.verify_source', return_value={'sha256': '0'*64}):
                with self.assertRaisesRegex(ValueError, 'changed-before-parse'):VersionSelectorSource(None, None, path)

    def test_optional_language_requires_both_pinned_sources_before_any_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);output=root/'.local/out';language=root/'language'
            calls=[lambda: build_all_unit_pack(output, language_ggp=language),
                   lambda: build_pack('unused', output, language_ggp=language),
                   lambda: prepare(output, repo=root, language_ggp=language)]
            for call in calls:
                with self.assertRaisesRegex(ValueError, 'requires-SYS_DATA-and-DSN'):call()
                self.assertFalse(output.exists())


if __name__ == '__main__':unittest.main()
