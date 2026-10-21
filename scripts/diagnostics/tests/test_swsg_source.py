"""SWSG contract guards with explicitly modeled definitions; no adapter I/O."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import swsg_source as swsg

PID = '9x1:SWSG:SWSG_Master_mit_AFS_ab_1310'


def fixture(ecu=47):
    records = [dict(requestHex=hx, decoderReady=True, byteOffset=0, bitOffset=0,
                    enumText={}, formula={'text': swsg.DSN_FORMULA}) for hx in swsg.IDENTITY_REQUESTS]
    measurement = dict(byteOffset=1, bitOffset=0, enumText={}, formula={'text': 'modeled-linear'})
    profile = dict(profileId=PID, ecuIds=[47, 48], selectedEcuId=ecu, generation=None,
        requestedGeneration='981', identity=records,
        parameters=[dict(id='a' * 64, nameResolved=True, definitionOffset=7,
                         requestHex='22F105', record=copy.deepcopy(measurement))])
    source = dict(profileId=PID, module='SWSG', ecuIds=[47, 48], sourceGeneration=None,
        generations=['981', '982'],
        identityGroups=[dict(requestHex=r['requestHex'], fields=[{'decodingDefinition': r}]) for r in records],
        measurementGroups=[dict(requestHex='22F105', fields=[dict(sourceOffset=7, decodingDefinition=measurement)])],
        versionRoutes=[dict(requestedEcuId=ecu,
            sourceSha256='55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f',
            bindingStatus='exact-source-version-rule-match', routes=[dict(routeId=ecu,
                routeIndexOffset={47: 106234, 48: 106220}[ecu], module='SWSG', flagsWord=0,
                selectors=[dict(zip(swsg.SELECTOR_KEYS, row)) for row in swsg.SELECTORS],
                versionMatches=[dict(name=PID.rsplit(':', 1)[1], rules=['100010'])])])],
        diagnosticSources=[dict(requestedEcuId=ecu,
            sourceSha256='18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522',
            bindingStatus='source-name-missing', links=[])])
    return profile, source


class SwsgSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        file = root / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'explicitly-modeled-command-library')
        self.env = patch.dict('os.environ', PORSCHE981_DEFINITION_ROOT=str(root))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.commands = {
            0x20000A: dict(recordFound=True, decodedPayloadHex='00021003',
                decodedPayloadSha256='90a95828ebc1916d89ff0ef9249c8ab6542bc065c81255004ee21c7fc074592c'),
            0x200001: dict(recordFound=True, decodedPayloadHex='010322F1A2',
                decodedPayloadSha256='d0d052a6db998f9edc31a725b5f6bbded719920a02b94348936cd81eb60c774c'),
        }

    def contract(self, profile, source):
        with patch.object(swsg, '_source_profile', return_value=source), \
                patch.object(swsg, '_lookup_command', side_effect=lambda raw, cid: self.commands[cid]):
            return swsg.source_contract(profile, ['a' * 64])

    def test_both_targets_keep_their_own_pair_and_only_normal_dsn_reads(self):
        for ecu in (47, 48):
            for generation in ('981', '982'):
                profile, source = fixture(ecu)
                profile['requestedGeneration'] = generation
                contract = self.contract(profile, source)
                self.assertEqual((contract['txId'], contract['rxId']), swsg.PAIRS[ecu])
                self.assertEqual(contract['identityConstraints'], {'dsnText': '100010'})
                self.assertEqual(contract['sourceCommands'], ['1003', '22F1A2'])
                self.assertEqual([r['requestHex'] for r in contract['identityRequests']], list(swsg.IDENTITY_REQUESTS))

    def test_unprogrammed_and_other_targets_are_not_normal_read_recipes(self):
        for change in ({'profileId': '9x1:SWSG:unprogrammiertes_SG'},
                       {'selectedEcuId': 88}, {'selectedEcuId': True},
                       {'ecuIds': [47]}, {'generation': '981'}, {'requestedGeneration': '987'}):
            profile, source = fixture()
            profile.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'manufacturer-profile-unqualified'):
                self.contract(profile, source)

    def test_an_extra_version_condition_cannot_be_discarded(self):
        profile, source = fixture()
        source['versionRoutes'][0]['routes'][0]['versionMatches'][0]['rules'] = ['100010#ja']
        with self.assertRaisesRegex(ValueError, 'swsg-source-version-unresolved'):
            self.contract(profile, source)

    def test_route_source_changes_do_not_fall_back_to_the_piwis_bridge(self):
        profile, source = fixture()
        source['diagnosticSources'][0]['bindingStatus'] = 'exact-source-name-match'
        with self.assertRaisesRegex(ValueError, 'swsg-source-route-mismatch'):
            self.contract(profile, source)

    def test_side_and_selector_changes_are_rejected(self):
        for field, value in (('routeId', 48), ('routeIndexOffset', 106220), ('flagsWord', 1)):
            profile, source = fixture()
            source['versionRoutes'][0]['routes'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'swsg-source-version-unresolved'):
                self.contract(profile, source)
        profile, source = fixture()
        source['versionRoutes'][0]['routes'][0]['selectors'][0]['formulaId'] += 1
        with self.assertRaisesRegex(ValueError, 'swsg-source-version-unresolved'):
            self.contract(profile, source)

    def test_measurement_or_identity_edits_do_not_open_a_contract(self):
        profile, source = fixture()
        profile['parameters'][0]['record']['formula']['text'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'swsg-source-parameter-mismatch'):
            self.contract(profile, source)
        profile, source = fixture()
        profile['identity'][0]['decoderReady'] = False
        with self.assertRaisesRegex(ValueError, 'swsg-source-identity-unresolved'):
            self.contract(profile, source)

    def test_command_body_changes_are_rejected(self):
        profile, source = fixture()
        self.commands[0x20000A]['decodedPayloadHex'] = '00021002'
        with self.assertRaisesRegex(ValueError, 'swsg-source-command-unresolved'):
            self.contract(profile, source)

    def test_normal_fault_profiles_keep_exact_targets_and_exclude_unprogrammed(self):
        from scripts.diagnostics.manufacturer_acquisition import READ_PROFILES, FAULT_READ_PROFILES, read_profile_generations, read_profile_targets
        for pid in swsg.VERSIONS:
            self.assertIn(pid, READ_PROFILES)
            self.assertIn(pid, FAULT_READ_PROFILES)
            self.assertEqual(read_profile_targets(pid), (47, 48))
            self.assertEqual(read_profile_generations(pid), ('981', '982'))
        self.assertNotIn('9x1:SWSG:unprogrammiertes_SG', FAULT_READ_PROFILES)
