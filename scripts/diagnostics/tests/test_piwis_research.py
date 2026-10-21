import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from scripts.diagnostics.piwis_research import CONFIG_NS, WORKFLOWS, inspect_archive, parameters, xml


def config(body):
    return f'<cts xmlns="{CONFIG_NS}"><config-variables>{body}</config-variables></cts>'


def param(name, values, extra=''):
    return '<param><short-name>' + name + '</short-name><type>String</type>' + ''.join(
        '<value>' + value + '</value>' for value in values) + extra + '</param>'


class PiwisResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='piwis-research-')
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name)
        self.rows = []
        self.add('cts-ecu.xml', '''<ecuspecificsequences><series name="9x1">
          <ECU logicallinkname="PSM" variant="PSM_TEST"><CTS oid="CTc_TEST_PSM" default_label="Rollenmodus" umode=".*"/></ECU>
          <ECU logicallinkname="GETRIEBE"><CTS oid="CTc_TEST_OIL" default_label="Oelbefuellung"
            filename="PAG_9x1_PDK_Oelbefuellung.xml" errFileName="cts-config\\missing-error.xml"/>
            <CTS oid="CTc_TEST_CAL" default_label="Kalibrieren (Gesamtablauf)"/></ECU>
          <ECU logicallinkname="ALLRAD"><CTS oid="CTc_EXCLUDED" default_label="Rollenmodus"/></ECU>
          </series><series name="991"><ECU logicallinkname="PSM"><CTS oid="CTc_991" default_label="Rollenmodus"/></ECU></series>
        </ecuspecificsequences>''')
        self.add('cts-platform.xml', '<seriesspecificsequences><series name="9x1"><CTS oid="CTc_TEST_CAR" default_label="Rollenmodus"/></series></seriesspecificsequences>')
        for _, _, _, _, name in WORKFLOWS:
            body = param('serviceVariable', ['TEST_StartRoutine', 'TEST_StopRoutine'])
            if 'Calibration' in name:
                body += param('activateProgramming', ['true']) + param('RuleFileName', ['TEST-rule.xml'])
            self.add('cts-config/' + name, config(body))
        self.add('flash-data/regeln/TEST-rule.xml', '<FLASHTABELLE/>')
        self.add('flash-data/regeln/TEST.asl', 'TEST_ACCESS_REFERENCE\nH-')
        self.manifest()

    def add(self, rel, content):
        path = self.archive / 'files' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = content.encode('utf8') if isinstance(content, str) else content
        path.write_bytes(raw)
        row = {'relativePath': rel, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'metadata': {}}
        self.rows.append(row)
        return row

    def manifest(self):
        (self.archive / 'manifest.json').write_text(json.dumps({'schemaVersion': 1,
            'executionEnabled': False, 'versions': {'pidtApplication': 'TEST'}, 'files': self.rows}), encoding='utf8')

    def test_provenance_multiple_values_and_explicit_vs_candidate_bindings(self):
        result = inspect_archive(self.archive)
        self.assertEqual(result['scope'], 'retained-archive-only')
        self.assertTrue(result['noDeviceIO'])
        self.assertFalse(result['executionEnabled'])
        oil = next(w for w in result['workflows'] if w['functionId'] == 'pdk-oil-fill')
        self.assertEqual(oil['configBinding'], 'explicit-menu-reference')
        self.assertEqual(oil['parameters'][0]['values'], ['TEST_StartRoutine', 'TEST_StopRoutine'])
        self.assertEqual(oil['parameters'][0]['source']['locator'], '/cts[1]/config-variables[1]/param[1]')
        self.assertIn('referenced-file-not-retained', oil['gaps'])
        psm = result['workflows'][0]
        self.assertEqual(psm['configBinding'], 'catalog-reference-unverified')
        self.assertEqual(len(psm['menus']), 1)
        self.assertEqual(psm['menus'][0]['context']['variant'], 'PSM_TEST')
        self.assertIn('/ECU[1]/CTS[1]', psm['menus'][0]['source']['locator'])
        self.assertFalse(oil['liveVerified'])

    def test_dependencies_do_not_authorize_programming(self):
        cal = next(w for w in inspect_archive(self.archive)['workflows'] if w['functionId'] == 'pdk-calibration')
        self.assertEqual(cal['dependencies'][0]['name'], 'flash-data/regeln/TEST-rule.xml')
        self.assertTrue(cal['dependencies'][0]['present'])
        self.assertIn('exact-request/response-and-service-binding', cal['gaps'])
        self.assertFalse(cal['executionEnabled'])

    def test_asl_is_a_text_reference_not_an_algorithm(self):
        result = inspect_archive(self.archive)
        source = next(s for s in result['sources'] if s['file'].endswith('.asl'))
        self.assertEqual(source['kind'], 'access-reference-text')
        self.assertEqual(source['referenceLines'], ['TEST_ACCESS_REFERENCE', 'H-'])
        self.assertEqual(result['summary']['nativeProgramFiles'], 0)
        self.assertFalse(result['nextProbes'][2]['availableInRetainedArchive'])

    def test_diagnostic_counts_are_observed_not_assumed_from_extension(self):
        self.add('flash-data/TEST.odx', '<ODX><FLASH/><DIAG-SERVICE/><REQUEST/><POS-RESPONSE/><DATA-OBJECT-PROP/></ODX>')
        self.manifest()
        result = inspect_archive(self.archive)
        self.assertEqual(result['summary']['diagnosticTags']['DIAG-SERVICE'], 1)
        self.assertTrue(result['nextProbes'][0]['availableInRetainedArchive'])

    def test_pdx_members_are_counted_and_binary_payload_not_exported(self):
        path = self.archive / 'files/flash-data/TEST.pdx'
        path.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('TEST.odx-f', '<ODX><FLASH/></ODX>')
            z.writestr('TEST.bin', b'PRIVATE_PAYLOAD_MUST_NOT_BE_EXPORTED')
        row = {'relativePath': 'flash-data/TEST.pdx', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'bytes': path.stat().st_size, 'metadata': {}}
        with zipfile.ZipFile(path) as z:
            row['metadata']['members'] = [{'name': e.filename, 'bytes': e.file_size, 'crc32': f'{e.CRC:08x}'} for e in z.infolist()]
        self.rows.append(row)
        self.manifest()
        result = inspect_archive(self.archive)
        self.assertEqual(result['summary']['containerMemberTypes']['.bin'], 1)
        self.assertNotIn('PRIVATE_PAYLOAD', json.dumps(result))
        row['metadata']['members'][0]['bytes'] += 1
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'metadata-mismatch'):
            inspect_archive(self.archive)

    def test_modified_source_is_rejected_before_import(self):
        (self.archive / 'files/cts-ecu.xml').write_bytes(b'<changed/>')
        with self.assertRaisesRegex(ValueError, 'source-integrity'):
            inspect_archive(self.archive)

    def test_source_change_during_inspection_is_rejected(self):
        def change_after_parse(raw):
            root = xml(raw)
            (self.archive / 'files/cts-ecu.xml').write_bytes(b'<changed/>')
            return root
        with patch('scripts.diagnostics.piwis_research.xml', side_effect=change_after_parse):
            with self.assertRaisesRegex(ValueError, 'source-changed-during-inspection'):
                inspect_archive(self.archive)

    def test_research_index_transfers_without_originals_or_firmware(self):
        from scripts.diagnostics.definition_bundle import export_bundle, import_bundle
        index = inspect_archive(self.archive)
        source = self.archive / 'bundle-source'
        target = self.archive / 'bundle-target'
        relative = Path('.local/diagnostics/piwis-workshop/research-index.json')
        file = source / relative
        file.parent.mkdir(parents=True)
        file.write_text(json.dumps(index), encoding='utf8')
        firmware = source / '.local/diagnostics/piwis-workshop/TEST.bin'
        firmware.write_bytes(b'PRIVATE_FIRMWARE')
        bundle = self.archive / 'definitions.zip'
        self.assertEqual(export_bundle(bundle, source)['files'], 1)
        self.assertEqual(import_bundle(bundle, target)['imported'], 1)
        self.assertEqual(json.loads((target / relative).read_text()), index)
        self.assertFalse((target / firmware.relative_to(source)).exists())

    def test_duplicate_or_unsafe_manifest_path_is_rejected(self):
        self.rows.append(self.rows[0])
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'duplicate-archive'):
            inspect_archive(self.archive)
        self.rows[-1] = {**self.rows[0], 'relativePath': '../outside.xml'}
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
            inspect_archive(self.archive)

    def test_extra_files_are_not_silently_ignored(self):
        (self.archive / 'files/extra.xml').write_text('<test/>')
        with self.assertRaisesRegex(ValueError, 'file-set-mismatch'):
            inspect_archive(self.archive)

    def test_entity_declarations_are_rejected_in_utf8_and_utf16(self):
        source = '<!DOCTYPE cts [<!ENTITY test "value">]><cts/>'
        for encoding in ('utf8', 'utf-16'):
            with self.assertRaisesRegex(ValueError, 'unsupported-or-oversized'):
                xml(source.encode(encoding))

    def test_parameter_structure_and_namespace_are_validated(self):
        source = {'file': 'test.xml', 'sha256': 'a' * 64}
        for body in (param('x', ['a']) + param('x', ['b']), param('x', ['a'], '<unknown>hidden</unknown>')):
            with self.assertRaises(ValueError):
                parameters(xml(config(body).encode()), source)
        with self.assertRaisesRegex(ValueError, 'namespace'):
            parameters(xml(b'<cts><param/></cts>'), source)
        result = parameters(xml(config(param('x', ['1'], '<!-- in sec -->')).encode()), source)
        self.assertEqual(result[0]['comments'], ['in sec'])


if __name__ == '__main__':
    unittest.main()
