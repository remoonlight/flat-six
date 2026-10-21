import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_recursive_numeric import (
    probe_recursive_structure_leaf, synthetic_recursive_structure_probes, write_recursive_response_probes)
from scripts.diagnostics.tests.test_piwis_nested_numeric import nested_fixture
from scripts.diagnostics.tests.test_piwis_table_struct import fixture as table_fixture
from scripts.diagnostics.tests.test_piwis_table_key import reference
from scripts.diagnostics.tests.test_piwis_texttable import enum_dop


def extend(structure, dop, local=2):
    inner = copy.deepcopy(structure)
    inner.update(sourceOffset=structure['sourceOffset']+10, record=structure['record']+1,
                 recordSha256=('8' if structure['recordSha256']!='8'*64 else '9')*64)
    child = structure['layout']['parameters']['parsedParameters'][0]
    inner['layout']['parameters']['parsedParameters'][0]['objectReference'] = reference(dop, 0x2c, 44)
    child['objectReference'] = reference(inner, 0xaa, 18)
    ref = child['objectReference']; stem = Path(inner['source']['file']).stem
    ref['nativeField0c']['text'] = stem
    ref['indexLookup'].update(container=stem, keyIndexSource={'file': stem+'.key', 'bytes': 4096, 'sha256': 'f'*64})
    child['serializedBytePosition'] = local; child['bitPosition'] = 0
    return inner


def fixture():
    parent, structure, dop = nested_fixture()
    inner = extend(structure, dop)
    return parent, [structure, inner], [0, 0], dop


class PiwisRecursiveTests(unittest.TestCase):
    def probe(self, values, data, **options):
        return probe_recursive_structure_leaf(*values, data, kernel_sha256=options.pop('kernel_sha256', PROFILE), **options)

    def test_two_local_slices_keep_full_position_chain_and_gaps(self):
        values = fixture()
        rows = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE)
        result = rows[0]['result']
        self.assertTrue(result['ok'], result['reason'])
        self.assertEqual(result['absoluteBytePosition'], 12)
        self.assertEqual(result['structureChildPositions'], [2, 3])
        self.assertEqual(result['byteOffset'], 3)
        self.assertEqual(bytes.fromhex(rows[0]['pduHex'])[:12], b'\x5a'*7+b'\x60'*2+b'\xa7'*3)
        for flag in ('parameterBindingVerified', 'referenceResolutionVerified', 'runtimeOwnerCollectionVerified',
            'rowValueDecoded', 'structureEnvelopeVerified', 'siblingProcessingVerified', 'responseEnvelopeVerified',
            'serviceResponseMatchingVerified', 'physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled'):
            self.assertFalse(result[flag])
        self.assertEqual(rows[-1]['result']['reason'], 'numeric-probe-truncated-field')
        self.assertEqual(self.probe(values, b'\x5a'*8)['reason'], 'recursive-probe-truncated-structure-position')

    def test_signed_mask_and_enum_leaf_use_declared_bits_inside_last_slice(self):
        for order in (0, 1):
            values = fixture(); c = values[3]['layout']['codedType']
            c.update(bitLength=13, nativeEncodingFieldsRaw=[0, 3, order, 0], bitMaskPresent=True, bitMaskRawHex='1fff')
            values[3]['layout']['physicalType']['nativeBaseDataType'] = 0
            values[1][-1]['layout']['parameters']['parsedParameters'][0]['bitPosition'] = 5
            rows = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE)
            negatives = [p['result']['candidateValue'] for p in rows if p['case'] in ('negative-minimum', 'minus-one')]
            self.assertEqual(negatives, [-4096, -1])
            values[3]['layout'] = enum_dop()
            values[3]['layout']['codedType']['nativeEncodingFieldsRaw'][2] = order
            rows = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE)
            self.assertEqual([p['result']['candidateValue'] for p in rows if p['result']['ok']], ['关', '开', '条件未满足'])

    def test_three_structure_layers_have_distinct_position_slices(self):
        values = fixture(); last = extend(values[1][-1], values[3], local=4)
        values[1].append(last); values[2].append(0)
        rows = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE)
        self.assertEqual(rows[0]['result']['structureChildPositions'], [2, 4, 3])
        self.assertEqual(rows[0]['result']['absoluteBytePosition'], 16)
        self.assertEqual(rows[0]['result']['structureDepth'], 3)

    def test_sources_indexes_kernel_and_dynamic_or_nonplain_parents_fail_closed(self):
        for change in ('hash', 'offset', 'bit', 'dynamic', 'protocol', 'kind', 'collection'):
            values = fixture(); child = values[1][0]['layout']['parameters']['parsedParameters'][0]
            if change=='hash': values[1][1]['recordSha256']='f'*64
            elif change=='offset': values[1][1]['sourceOffset']+=1
            elif change=='bit': child['bitPosition']=1
            elif change=='dynamic': child['bytePositionAvailable']=False
            elif change=='protocol': child['nativeFlags']|=0x80
            elif change=='kind': child['parameterType']=0x7012
            else: values[1][1]['layout']['parameters']['parameterCollectionLayoutComplete']=False
            result = self.probe(values, b'\0'*30)
            self.assertFalse(result['ok']); self.assertNotIn('candidateValue', result)
        self.assertFalse(self.probe(fixture(), b'\0'*30, kernel_sha256='0'*64)['ok'])

    def test_cycles_path_count_and_combined_offsets_are_rejected(self):
        values = fixture(); values[1][1] = values[1][0]
        self.assertEqual(self.probe(values, b'\0'*30)['reason'], 'response-probe-dop-record-identity-mismatch')
        values = fixture(); child = values[1][0]['layout']['parameters']['parsedParameters'][0]
        child['objectReference'] = copy.deepcopy(values[0]['objectReference']); values[1][1] = values[1][0]
        self.assertEqual(self.probe(values, b'\0'*30)['reason'], 'recursive-probe-cycle')
        values = fixture(); values[2].append(0)
        self.assertEqual(self.probe(values, b'\0'*30)['reason'], 'recursive-probe-path-depth-or-index-count-invalid')
        values = fixture()
        for _ in range(7):
            values[1].append(extend(values[1][-1], values[3])); values[2].append(0)
        self.assertEqual(self.probe(values, b'\0'*30)['reason'], 'recursive-probe-path-depth-or-index-count-invalid')
        values = fixture(); values[0]['serializedBytePosition']=4094
        self.assertEqual(self.probe(values, b'\0'*30)['reason'], 'recursive-probe-combined-position-invalid')

    def test_table_root_selects_key_before_any_recursive_leaf_value(self):
        response, table, row, key_dop, structure, dop, pool = table_fixture()
        inner = extend(structure, dop)
        context = {'parentIndex': 1, 'table': table, 'row': row, 'keyDop': key_dop, 'asciiStrings': pool}
        values = (response, [structure, inner], [0, 0], dop)
        rows = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE, table_context=context)
        self.assertTrue(rows[0]['result']['ok'], rows[0]['result']['reason'])
        self.assertEqual(rows[0]['result']['absoluteBytePosition'], 8)
        self.assertEqual(rows[0]['result']['candidateKeyValue'], 12)
        for p in rows[-2:]:
            self.assertFalse(p['result']['ok']); self.assertNotIn('candidateValue', p['result'])
        self.assertEqual(rows[-2]['result']['reason'], 'recursive-probe-key:table-key-probe-declared-row-key-mismatch')
        self.assertEqual(rows[-1]['result']['reason'], 'recursive-probe-key:numeric-probe-truncated-field')
        self.assertFalse(self.probe(values, bytes.fromhex(rows[0]['pduHex']), table_context=context)['ok'])

    def test_writer_preserves_every_source_and_child_index_and_cycle_reason(self):
        parent, structures, indexes, dop = fixture()
        response = {'source': structures[0]['source'], 'record': 9, 'sourceOffset': 900,
            'compressedBytes': 100, 'recordSha256': 'a'*64, 'parameters': {'parsedParameters': [parent]}}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); rp=p/'responses.jsonl'; lp=p/'layouts.jsonl'; out=p/'recursive.jsonl'
            rp.write_text(json.dumps(response)+'\n', encoding='utf8')
            lp.write_text(''.join(json.dumps(s)+'\n' for s in structures+[dop]), encoding='utf8')
            counts=write_recursive_response_probes(rp,lp,out,ascii_strings={},kernel_sha256=PROFILE)
            rows=[json.loads(x) for x in out.read_text(encoding='utf8').splitlines()]
            self.assertEqual(counts['supportedPaths'], 1)
            self.assertEqual(rows[0]['childIndexes'], indexes)
            self.assertEqual([r['recordSha256'] for r in rows[0]['structureRecords']], [s['recordSha256'] for s in structures])
            self.assertEqual(rows[0]['dopRecord']['recordSha256'], dop['recordSha256'])
            structures[-1]['layout']['parameters']['parsedParameters'][0]['objectReference']=copy.deepcopy(parent['objectReference'])
            lp.write_text(''.join(json.dumps(s)+'\n' for s in structures+[dop]), encoding='utf8')
            counts=write_recursive_response_probes(rp,lp,p/'cycle.jsonl',ascii_strings={},kernel_sha256=PROFILE)
            self.assertEqual(counts['case:recursive-probe-cycle'],1)

    def test_writer_resource_guard_aborts_expansion_without_qualifying_partial_output(self):
        parent, structures, _, dop = fixture()
        last = structures[-1]['layout']['parameters']
        last['parsedParameters'].append(copy.deepcopy(last['parsedParameters'][0])); last['declaredParameters']=2
        response = {'source': structures[0]['source'], 'record': 9, 'sourceOffset': 900,
            'compressedBytes': 100, 'recordSha256': 'a'*64, 'parameters': {'parsedParameters': [parent]}}
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory); rp=p/'responses.jsonl'; lp=p/'layouts.jsonl'
            rp.write_text(json.dumps(response)+'\n',encoding='utf8')
            lp.write_text(''.join(json.dumps(s)+'\n' for s in structures+[dop]),encoding='utf8')
            with patch('scripts.diagnostics.piwis_recursive_numeric.MAX_PATHS_PER_ROOT',1):
                with self.assertRaisesRegex(ValueError,'recursive-probe-path-limit'):
                    write_recursive_response_probes(rp,lp,p/'limited.jsonl',ascii_strings={},kernel_sha256=PROFILE)
