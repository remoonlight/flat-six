import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.source_catalog import PREFIX, GUARDS, handle_source_catalog
from scripts.diagnostics import source_catalog
from scripts.diagnostics.offline_match import ANCHORS
from scripts.diagnostics.definition_bundle import export_bundle, import_bundle


class SourceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(('\\\\?\\'+self.temp.name) if os.name=='nt' else self.temp.name)
        self.library=self.directory/'library';self.library.mkdir()
        self.file='profiles/'+'a'*64+'.json';self.profile='9x1:LED:Reviewed'
        self.data={'profileId':self.profile,'variant':'Reviewed','module':'LED','ecuIds':[47], 'generations':['981','982'],
            'versionSelectorReadSources':[dict(requestedEcuId=47,routes=[dict(requestGroups=[dict(requestCandidateHex='22F19E',selectorIndices=[0],sourceOccurrences=[],missingSourceAudit={'status':'read-command-only-in-other-ECUs'})])])],**GUARDS}
        self.ref=dict(profileId=self.profile,file=self.file,variant='Reviewed',diagnosticSourceBindingStatus='exact-source-name-match',versionRouteBindingStatus='exact-source-route-name-match',versionSelectorSourceBindingStatus='selector-read-source-coverage-incomplete',identityReadGroups=3,measurementDefinitions=9,measurementReadGroups=4,codingFields=2,codingReadGroups=1)
        self.index={'units':[dict(generation=g,ecuId=47,label='LED',variants=[self.ref]) for g in ('981','982')], 'versionSelectorSourceBindingCounts':{'selector-read-source-coverage-incomplete':2}}
        self.documents={'all-units.json':json.dumps(self.index).encode(),self.file:json.dumps(self.data).encode()}
        m={'kind':'x431-offline-source-catalog','schemaVersion':1,'sourceFingerprints':[{'sha256':ANCHORS['variants']}],
            'generatorCode':[{'name':'scripts/diagnostics/source_catalog.py','sha256':hashlib.sha256(Path(source_catalog.__file__).read_bytes()).hexdigest()}],
            'files':[{'name':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in self.documents.items()],**GUARDS}
        raw=json.dumps(m).encode();self.id=hashlib.sha256(raw).hexdigest();self.root=self.library/PREFIX/self.id;self.root.mkdir(parents=True)
        (self.root/'manifest.json').write_bytes(raw)
        for n,b in self.documents.items():
            f=self.root/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)

    def request(self, **changes):
        return dict(action='source-profile',sourceCatalogId=self.id,generation='981',ecuId=47,profileId=self.profile)|changes

    def test_portable_package_import_keeps_candidate_and_missing_other_ecu_status(self):
        zipfile=self.directory/'source.zip';target=self.directory/'target'
        export_bundle(zipfile,self.library);import_bundle(zipfile,target)
        r=handle_source_catalog(self.request(),target)
        self.assertEqual(r['selectorGroups'][0]['missingReason'],'read-command-only-in-other-ECUs')
        self.assertEqual(r['selectorGroups'][0]['sourceOccurrences'],0)
        self.assertEqual(r['actualVehicleStatus'],'unqualified')
        self.assertEqual({k:r[k] for k in GUARDS},GUARDS)
        self.assertTrue(r['generatorCodeMatchesCurrent'])

    def test_all_generations_and_unmapped_units_retained_without_selection(self):
        catalogs=handle_source_catalog({'action':'source-catalogs'},self.library)['catalogs']
        self.assertEqual(len(catalogs),1)
        for generation in ('981','982'):
            r=handle_source_catalog(dict(action='source-units',generation=generation,sourceCatalogId=self.id),self.library)
            self.assertEqual(r['units'][0]['ecuId'],47)
            self.assertEqual(len(r['units'][0]['variants']),1)

    def test_cross_ecu_generation_path_and_unknown_request_fields_rejected(self):
        for change in ({'ecuId':48},{'generation':'991'},{'profileId':'unknown'},{'sourceCatalogId':'../outside'}, {'path':str(self.root)}, {'executionEnabled':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                handle_source_catalog(self.request(**change),self.library)

    def test_manifest_and_selected_profile_drift_rejected(self):
        file=self.root/self.file;file.write_text('changed')
        with self.assertRaisesRegex(ValueError,'file-hash-mismatch'):
            handle_source_catalog(self.request(),self.library)
        (self.root/'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'manifest-hash-mismatch'):
            handle_source_catalog({'action':'source-catalogs'},self.library)

    def test_index_drift_is_not_hidden_by_known_profile(self):
        (self.root/'all-units.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'file-hash-mismatch'):
            handle_source_catalog(self.request(),self.library)

    def test_symlink_profile_cannot_escape_snapshot(self):
        file=self.root/self.file;file.unlink();outside=self.directory/'outside';outside.write_bytes(self.documents[self.file]);file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'path-invalid'):
            handle_source_catalog(self.request(),self.library)

    def test_code_drift_remains_reference_and_never_promotes_vehicle_flags(self):
        with patch('scripts.diagnostics.source_catalog.APP',(self.directory/'missing-app').resolve()):
            r=handle_source_catalog(self.request(),self.library)
            self.assertFalse(r['generatorCodeMatchesCurrent'])
            self.assertEqual(len(r['generatorCodeDrift']),1)
            self.assertEqual({k:r[k] for k in GUARDS},GUARDS)

    def test_injected_profile_authority_cannot_leak_to_output(self):
        from scripts.diagnostics.source_catalog import _document
        with patch('scripts.diagnostics.source_catalog._document',side_effect=[self.index,self.data|{'vehicleVerified':True,'executionEnabled':True}]):
            r=handle_source_catalog(self.request(),self.library)
            self.assertEqual({k:r[k] for k in GUARDS},GUARDS)

    def test_numeric_preview_summary_is_separate_from_vehicle_authority(self):
        coding = {'definitionFieldCount':2, 'editorReadinessScope':'offline-field-preview-only',
            'previewReadiness':{'named-options-ready':1, 'source-bounded-linear-preview-ready':1},
            'codingWriteQualified':True}
        with patch('scripts.diagnostics.source_catalog._document', side_effect=[self.index,self.data|{'coding':coding}]):
            r=handle_source_catalog(self.request(),self.library)
        self.assertEqual(r['codingPreviewSummary'], {'totalFields':2, 'namedOptionFields':1,
            'numericCandidateFields':1, 'blockedFields':0, 'scope':'offline-field-preview-only',
            'nativeEditingBehaviorVerified':False, 'codingWriteQualified':False})
        self.assertEqual({k:r[k] for k in GUARDS},GUARDS)
        self.assertIsNone(handle_source_catalog(self.request(),self.library)['codingPreviewSummary'])

    def test_inconsistent_preview_counts_are_rejected_instead_of_showing_false_coverage(self):
        for readiness in ({'named-options-ready':True}, {'named-options-ready':-1},
                          {'named-options-ready':3}, {'named-options-ready':1}, []):
            coding={'definitionFieldCount':2, 'editorReadinessScope':'offline-field-preview-only',
                    'previewReadiness':readiness}
            with self.subTest(readiness=readiness), patch('scripts.diagnostics.source_catalog._document',
                    side_effect=[self.index,self.data|{'coding':coding}]), self.assertRaisesRegex(ValueError,'coding-preview-counts-invalid'):
                handle_source_catalog(self.request(),self.library)


if __name__=='__main__':unittest.main()
