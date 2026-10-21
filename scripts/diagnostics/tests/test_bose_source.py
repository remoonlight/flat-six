"""BOSE source guards; all source/command objects here are explicit models."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import bose_source as bose


def fixture():
    records = [dict(requestHex=hx, decoderReady=True, byteOffset=0, bitOffset=0,
                    enumText={}, formula={'text': 'modeled-identity'}) for hx in bose.IDENTITY_REQUESTS]
    measurement = dict(byteOffset=1, bitOffset=0, enumText={}, formula={'text': 'modeled-linear'})
    profile = dict(profileId=bose.PROFILE, ecuIds=[65], selectedEcuId=65, generation=None,
        requestedGeneration='981', identity=records,
        parameters=[dict(id='a'*64, nameResolved=True, definitionOffset=7,
                         requestHex='22F100', record=copy.deepcopy(measurement))])
    source = dict(profileId=bose.PROFILE, module='BV_SoundSysteUDS', sourceGeneration=None,
        ecuIds=[65], generations=['981','982'],
        identityGroups=[dict(requestHex=r['requestHex'], fields=[{'decodingDefinition': r}]) for r in records],
        measurementGroups=[dict(requestHex='22F100', fields=[dict(sourceOffset=7, decodingDefinition=measurement)])],
        versionRoutes=[dict(requestedEcuId=65, bindingStatus='exact-source-version-rule-match',
            sourceSha256='55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f',
            routes=[dict(routeId=65, routeIndexOffset=105663, flagsWord=1, module='BV_SoundSysteUDS',
                selectors=[dict(zip(bose.SELECTOR_KEYS, row)) for row in bose.SELECTORS],
                versionMatches=[dict(name=bose.PROFILE.rsplit(':',1)[1], versionIndex=2,
                    ruleOffset=118740, rules=[bose.RULE])])])],
        diagnosticSources=[dict(requestedEcuId=65, bindingStatus='source-name-missing', links=[],
            sourceSha256='18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522')])
    return profile, source


class BoseSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name)/'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'explicitly-modeled-command-library')
        env = patch.dict('os.environ', PORSCHE981_DEFINITION_ROOT=self.tmp.name)
        env.start(); self.addCleanup(env.stop)
        self.commands = {
            0x20000A: dict(recordFound=True, decodedPayloadHex='00021003', decodedPayloadSha256='90a95828ebc1916d89ff0ef9249c8ab6542bc065c81255004ee21c7fc074592c'),
            0x200002: dict(recordFound=True, decodedPayloadHex='010322F19E', decodedPayloadSha256='af7e9fd8f0e0461f31027ab5baa5f8f2e9bf19be5b62d91575fc2d34117d828e'),
            0x200001: dict(recordFound=True, decodedPayloadHex='010322F1A2', decodedPayloadSha256='d0d052a6db998f9edc31a725b5f6bbded719920a02b94348936cd81eb60c774c'),
        }
        self.formula_found = True

    def contract(self, profile, source):
        owner = self
        class Reader:
            def lookup_id(self, group, fid):
                return dict(id_hex=f'{fid:08X}', structured={'fields':[{'text':'modeled-selector'}]}) if owner.formula_found else None
        with patch.object(bose,'_source_profile',return_value=source), \
                patch.object(bose,'_lookup_command',side_effect=lambda raw,cid:self.commands[cid]), \
                patch('scripts.diagnostics.x431_variant_selection._load_version_language',return_value=Reader()):
            return bose.source_contract(profile, ['a'*64])

    def test_both_generations_keep_the_own_pair_and_full_version_rule(self):
        for generation in ('981','982'):
            p,s=fixture();p['requestedGeneration']=generation;c=self.contract(p,s)
            self.assertEqual((c['txId'],c['rxId']),(0x76F,0x7D9))
            self.assertEqual(c['identityConstraints'],{'versionRules':[bose.RULE]})
            self.assertEqual(c['sourceCommands'],['1003','22F19E','22F1A2'])
            self.assertEqual([r['requestHex'] for r in c['identityRequests']],list(bose.IDENTITY_REQUESTS))

    def test_other_units_versions_and_source_generations_are_rejected(self):
        for changes in ({'selectedEcuId':66},{'selectedEcuId':True},{'profileId':bose.PROFILE[:-3]+'001'},
                        {'ecuIds':[65,66]},{'generation':'981'},{'requestedGeneration':'987'}):
            p,s=fixture();p.update(changes)
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'manufacturer-profile-unqualified'):self.contract(p,s)

    def test_changed_source_rule_or_selector_is_not_tolerated(self):
        for field,value in [('routeIndexOffset',0),('routeId',70),('flagsWord',0)]:
            p,s=fixture();s['versionRoutes'][0]['routes'][0][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'bose-source-version-unresolved'):self.contract(p,s)
        p,s=fixture();s['versionRoutes'][0]['routes'][0]['versionMatches'][0]['rules']=['EV_AMPMst16C4Gen2BOSE#001']
        with self.assertRaisesRegex(ValueError,'bose-source-version-unresolved'):self.contract(p,s)
        p,s=fixture();s['versionRoutes'][0]['routes'][0]['selectors'][0]['formulaId']+=1
        with self.assertRaisesRegex(ValueError,'bose-source-version-unresolved'):self.contract(p,s)

    def test_updated_or_borrowed_diagnostic_reference_requires_review(self):
        for field,value in [('sourceSha256','0'*64),('bindingStatus','exact-source-name-match'),('links',[{}])]:
            p,s=fixture();s['diagnosticSources'][0][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'bose-source-route-mismatch'):self.contract(p,s)

    def test_measurement_and_identity_edits_cannot_open_a_contract(self):
        p,s=fixture();p['parameters'][0]['record']['formula']['text']='changed'
        with self.assertRaisesRegex(ValueError,'bose-source-parameter-mismatch'):self.contract(p,s)
        p,s=fixture();p['identity'][0]['decoderReady']=False
        with self.assertRaisesRegex(ValueError,'bose-source-identity-unresolved'):self.contract(p,s)

    def test_missing_selector_definition_is_rejected_before_transport(self):
        self.formula_found=False
        with self.assertRaisesRegex(ValueError,'bose-source-selector-unresolved'):self.contract(*fixture())

    def test_command_body_change_cannot_be_used(self):
        self.commands[0x20000A]['decodedPayloadHex']='00021002'
        with self.assertRaisesRegex(ValueError,'bose-source-command-unresolved'):self.contract(*fixture())

    def test_qualified_fault_profile_keeps_the_same_target_and_generations(self):
        from scripts.diagnostics.manufacturer_acquisition import READ_PROFILES,FAULT_READ_PROFILES,read_profile_targets,read_profile_generations
        self.assertIn(bose.PROFILE,READ_PROFILES);self.assertIn(bose.PROFILE,FAULT_READ_PROFILES)
        self.assertEqual(read_profile_targets(bose.PROFILE),(65,));self.assertEqual(read_profile_generations(bose.PROFILE),('981','982'))
