import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_unit_groups import (probe_unit_group_selection,
    unit_group_definition_layout, write_unit_group_selections)
from scripts.diagnostics.tests.test_piwis_data_objects import COMMON, POOLS
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture
from scripts.diagnostics.tests.test_piwis_units import unit


def group(*units):
    row = unit(400)
    row['layout'] = {'recordType': 0x104, 'fieldLayoutComplete': True,
        'nativeCategoryRaw': 0xf00, 'unitReferences': {'declaredReferences': len(units), 'items': []}}
    for u in units:
        parameter, _ = fixture(); ref = parameter['objectReference']; lookup = ref['indexLookup']
        lookup['target'].update({k: u[k] for k in ('sourceOffset', 'compressedBytes', 'recordSha256', 'record')})
        lookup['target']['recordType'] = 0x102
        lookup['databaseSource'] = copy.deepcopy(u['source'])
        row['layout']['unitReferences']['items'].append({'name': {'text': 'retained-unit'}, 'reference': ref})
    return row


class PiwisUnitGroupTests(unittest.TestCase):
    def select(self, source, g, *units, **options):
        rows = {(r['source']['file'], r['sourceOffset']): r for r in units}
        return probe_unit_group_selection(source, g, rows, kernel_sha256=PROFILE, **options)

    def test_loaded_category_ordered_named_refs_and_tail(self):
        raw = b'\x04\1'+COMMON+struct.pack('<HH', 0xf00, 2)+struct.pack('<6I', *([1]*6))+b'#>\0'
        result = unit_group_definition_layout(raw, POOLS)
        self.assertTrue(result['fieldLayoutComplete'], result)
        self.assertEqual(result['nativeCategoryRaw'], 0xf00)
        self.assertEqual(result['unitReferences']['declaredReferences'], 2)
        self.assertEqual([i['reference']['nativeField0c']['text'] for i in result['unitReferences']['items']], ['source-name']*2)
        self.assertEqual(result['remainingRawHex'], '233e00')
        self.assertFalse(result['runtimeUnitSelectionVerified'])

    def test_truncated_count_reference_and_oversized_collection_stop(self):
        raw = b'\x04\1'+COMMON+struct.pack('<HH', 0xf00, 1)+struct.pack('<3I', 1, 1, 1)
        for n in range(2, len(raw)):
            self.assertFalse(unit_group_definition_layout(raw[:n], POOLS)['fieldLayoutComplete'], n)
        result = unit_group_definition_layout(b'\x04\1'+COMMON+struct.pack('<HH', 0xf00, 4097), POOLS)
        self.assertEqual(result['blockedReason'], 'unit-group-reference-count-limit')
        self.assertIsNone(unit_group_definition_layout(b'\x02\1'+COMMON, POOLS))

    def test_first_name_and_seven_exponents_match_stops_before_later_refs(self):
        source, missing, wrong, first, second = unit(), unit(91, dimensions=None), unit(92), unit(93, 2), unit(94, 3)
        wrong['layout']['physicalDimension']['fields'][0]['text'] = 'OtherDimension'
        g = group(missing, wrong, first, second)
        g['layout']['unitReferences']['items'][3]['reference']['indexLookup']['indexTargetMatched'] = False
        r = self.select(source, g, missing, wrong, first, second)
        self.assertTrue(r['ok'], r); self.assertEqual(r['matchedIndex'], 2)
        self.assertEqual(r['selectedUnitRecord']['sourceOffset'], 93)
        self.assertEqual(r['examinedIndexes'], [0, 1, 2])

    def test_equal_names_but_different_exponents_do_not_match(self):
        source, target = unit(), unit(91, dimensions=(1, 0, 0, 0, 1, 0, 0))
        r = self.select(source, group(target), target)
        self.assertTrue(r['ok'], r); self.assertEqual(r['selectionKind'], 'no-selected-unit')
        self.assertIsNone(r['selectedUnitRecord'])
        r = self.select(source, group(target), target, require_match=True)
        self.assertEqual(r['reason'], 'unit-group-no-matching-unit')

    def test_empty_and_missing_source_dimension_respect_require_match_flag(self):
        source = unit(dimensions=None)
        # Native missing-source-dimension branch never iterates group entries.
        g = group(unit(91)); g['layout']['unitReferences']['items'][0]['reference']['indexLookup']['indexTargetMatched'] = False
        r = self.select(source, g)
        self.assertTrue(r['ok'], r); self.assertEqual(r['examinedIndexes'], [])
        self.assertEqual(self.select(source, g, require_match=True)['reason'], 'unit-group-source-dimension-missing')
        self.assertEqual(self.select(unit(), group(), require_match=True)['reason'], 'unit-group-no-matching-unit')

    def test_earlier_unresolved_or_wrong_hash_target_blocks_instead_of_skipping(self):
        source, first, later = unit(), unit(91), unit(92)
        g = group(first, later)
        for field, value in (('indexTargetMatched', False), ('target', {'recordType': 0x104})):
            changed = copy.deepcopy(g); changed['layout']['unitReferences']['items'][0]['reference']['indexLookup'][field] = value
            r = self.select(source, changed, first, later)
            self.assertFalse(r['ok'], r); self.assertNotIn('selectedUnitRecord', r)
        first = copy.deepcopy(first); first['recordSha256'] = 'e'*64
        self.assertFalse(self.select(source, g, first, later)['ok'])

    def test_missing_name_and_bad_group_metadata_flags_rejected(self):
        source, target = unit(), unit(91)
        target['layout']['physicalDimension']['fields'][0]['resolved'] = False
        self.assertEqual(self.select(source, group(target), target)['reason'], 'unit-group-dimension-name-unqualified')
        for change in ({'recordSha256': 'bad'}, {'layout': []}):
            g = group(); g.update(change); self.assertFalse(self.select(source, g)['ok'])
        self.assertEqual(self.select(source, group(), require_match=0)['reason'], 'unit-group-require-match-flag-invalid')

    def test_writer_explicit_cases_and_all_qualification_flags_false(self):
        source, target = unit(), unit(91); g = group(target)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); raw = path/'layouts.jsonl'; output = path/'selections.jsonl'
            raw.write_text(''.join(json.dumps(r)+'\n' for r in (source, target, g)))
            counts = write_unit_group_selections(raw, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['unitGroupRecords'], 1); self.assertEqual(counts['explicitUnitGroupPairs'], 2)
            self.assertEqual(counts['first-matching-dimension'], 4)
            for line in output.read_text().splitlines():
                r = json.loads(line); self.assertEqual(r['unitGroupRecord'], g)
                for p in r['probes']:
                    result = p['result']
                    for f in ('runtimeUnitSelectionVerified', 'runtimeUnitIdentityVerified', 'unitGroupSemanticsVerified',
                              'physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled', 'actualVehicleResponse'):
                        self.assertFalse(result[f], f)
