import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.piwis_flow import build_index, description, properties, read_snapshots, MODULE_OWNER
from scripts.diagnostics.piwis_research import digest
from scripts.diagnostics.tests.test_piwis_compilation import stream


def source(raw, name):
    return {'raw': raw, 'source': {'file': name, 'bytes': len(raw), 'sha256': digest(raw)}}


def sources():
    global_name = 'workspace/dataservice.properties'
    platform_name = 'workspace/vehicle-platform/9x1/dataservice.properties'
    return {global_name: source(b'deliverable_0=.hsqldb\nCodeServiceSequence=0\n', global_name),
        platform_name: source(b'deliverable_1=./vehicle-platform/9x1/ecu-specific-cts\ndeliverable_2=./vehicle-platform/9x1/override-cts\nCodeServiceSequence=2,g0,1\n', platform_name)}


class PiwisFlowTests(unittest.TestCase):
    def test_snapshot_paths_hashes_and_canonical_pidt_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'Pidt/workspace/dataservice.properties'
            raw = b'deliverable_0=.hsqldb\n'
            path = root / 'sources' / name
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            rows = [{'relativePath': name, 'present': True, 'bytes': len(raw), 'sha256': digest(raw)}]
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'files': rows}))
            self.assertIn('workspace/dataservice.properties', read_snapshots([manifest]))
            path.write_bytes(raw + b'changed')
            with self.assertRaisesRegex(ValueError, 'hash-mismatch'):
                read_snapshots([manifest])
            rows[0]['relativePath'] = '../outside.obj'
            manifest.write_text(json.dumps({'files': rows}))
            with self.assertRaises(ValueError):
                read_snapshots([manifest])

    def test_configured_shared_source_precedes_platform_copy(self):
        data = sources()
        for name in ('workspace/.hsqldb/code/CTS/ctc@test.obj',
                     'workspace/vehicle-platform/9x1/ecu-specific-cts/code/CTS/ctc@test.obj'):
            data[name] = source(stream(), name)
        result = build_index(data, {'test': 'CTc_TEST'})
        selected = result['workflows'][0]['configuredCandidates'][0]
        self.assertEqual(selected['deliverable'], 'g0')
        self.assertEqual(selected['otherRetainedCandidates'][0]['deliverable'], '1')
        self.assertFalse(selected['runtimeVerified'])
        self.assertFalse(result['workflows'][0]['executable'])
        self.assertFalse(result['targetExecuted'])

    def test_multiple_variants_in_preferred_source_are_unresolved(self):
        data = sources()
        for n in range(2):
            name = 'workspace/.hsqldb/code/CTS/ctc@variant' + str(n) + '.obj'
            data[name] = source(stream(), name)
        result = build_index(data, {'test': 'CTc_TEST'})
        self.assertEqual(result['workflows'][0]['configuredCandidates'], [])
        self.assertEqual(result['workflows'][0]['missingDependencies'][0]['reason'], 'variant-selection-unresolved')

    def test_missing_objects_and_unknown_sequence_fail_closed(self):
        data = sources()
        result = build_index(data, {'test': 'CTc_MISSING'})
        self.assertEqual(len(result['workflows'][0]['missingDependencies']), 1)
        self.assertFalse(result['executionEnabled'])
        for seq in (b'3', b'g0,g0', b''):
            name = 'workspace/vehicle-platform/9x1/dataservice.properties'
            data[name] = source(b'deliverable_1=platform\nCodeServiceSequence=' + seq, name)
            with self.assertRaisesRegex(ValueError, 'code-service-sequence'):
                build_index(data)

    def test_duplicate_or_unsupported_property_syntax_rejected(self):
        for raw in (b'a=1\na=2', b'CodeServiceSequence=g0\\\n1', b'not a property'):
            with self.assertRaises(ValueError):
                properties(raw)

    def test_embedded_oid_requires_literal_and_exact_factory_signature(self):
        instructions = [
            {'pc': 0, 'classOffset': 100, 'string': 'UKN_NODE'},
            {'pc': 2, 'classOffset': 102, 'string': 'CTc_CHILD'},
            {'pc': 4, 'classOffset': 104, 'call': {'owner': MODULE_OWNER, 'name': 'newCTSOID',
                'descriptor': '(Ljava/lang/String;)L' + MODULE_OWNER + ';'}},
            {'pc': 7, 'classOffset': 107, 'string': 'config.xml'}]
        data = {'entityOID': 'CTc_ROOT', 'fullyQualifiedClassName': 'ctspackage.Root', 'summary': {},
                'classes': [{'classOffsetInObject': 300, 'class': {'name': 'ctspackage/Root', 'methods': [
                    {'name': 'embeddedCallDescriptionsRoot_0', 'instructions': instructions}]}}]}
        result = description(data)
        self.assertEqual(result['dependencies'][0]['targetOID'], 'CTc_CHILD')
        self.assertEqual(result['dependencies'][0]['objectOffset'], 404)
        self.assertEqual(result['configLiterals'][0]['objectOffset'], 407)
        self.assertFalse(result['dependencies'][0]['executionOrderVerified'])
        instructions[1].pop('string')
        result = description(data)
        self.assertIsNone(result['dependencies'][0]['targetOID'])
        self.assertFalse(result['dependencies'][0]['bindingVerified'])
