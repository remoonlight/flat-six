"""LED guards and real read engine with modeled ports/D-PDU events only."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics import headlamp_source as led, manufacturer_dtc as dtc
from scripts.diagnostics import manufacturer_read as read, uds_source_read as uds
from scripts.diagnostics.swsg_source import VERSIONS as SWSG_VERSIONS
from scripts.diagnostics.elm import ElmClient
from scripts.diagnostics.manufacturer_transport import _client_cycles, prepared_read_contract
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.tests.test_headlamp_source import CASES, PREFIX, replies
from scripts.diagnostics.tests.test_manufacturer_ecm_asam import NAME, VERSION
from scripts.diagnostics.tests.test_manufacturer_multifield import FLAG
from scripts.diagnostics.tests.test_vnci import FakeNative, ID
from scripts.diagnostics.vnci import DpuClient


def fixture(pid=PREFIX + CASES[0][0], generation='981'):
    p = dict(profileId=pid, ecuIds=[47], selectedEcuId=47, generation=None,
             requestedGeneration=generation, identity=[], parameters=[], status='definition-only')
    definition = dict(profileId=pid, records=[])
    definition['definitionSha256'] = hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    s = dict(profileId=pid, variant=pid.rsplit(':', 1)[1], module='SCHEINWERFER_LED', sourceGeneration=None,
        ecuIds=[47], generations=['981', '982'], identityGroups=[], measurementGroups=[], dtcDefinition=definition,
        versionRoutes=[dict(requestedEcuId=47, bindingStatus='exact-source-version-rule-match',
            sourceSha256='55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f',
            routes=[dict(routeId=47, routeIndexOffset=106194, flagsWord=0, module='SCHEINWERFER_LED',
                selectors=[dict(zip(led.SELECTOR_KEYS, v)) for v in led.SELECTORS],
                versionMatches=[dict(name=pid.rsplit(':', 1)[1], rules=list(led.VERSION_RULES[pid]))])])],
        diagnosticSources=[dict(requestedEcuId=47, variantName=pid.rsplit(':', 1)[1], bindingStatus='exact-source-name-match',
            sourceSha256='18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522',
            links=[dict(linkIndex=0, communication=dict(modeByte=16, **{'class': 'static-source-confirmed'},
                pairs=[dict(storedTx=0x725 << 5, storedRx=0x78F << 5, subtypeByte=3,
                    parameterIds=[1049511, 2097153, 2097155, 2097162])]),
                dtc=dict(styleByte=4, metadataHex='FF0203FF0009', readCommandSlots=[
                    dict(role='sessionPrecondition', wireCandidateHex='1003', commandFormat='complete-source-command', nativeDescriptorHexCandidate='01021003'),
                    dict(role='dtcRead', wireCandidateHex='190208', commandFormat='complete-source-command', nativeDescriptorHexCandidate='0103190208')]))])])
    return p, s


class Reader:
    def lookup_id(self, group, fid):
        text = {0x8000021D: NAME, 0x8000000A: VERSION, 0xA0000001: FLAG}[fid]
        return dict(id_hex=f'{fid:08X}', structured={'fields': [{'text': text}]})


class HeadlampReadTests(unittest.TestCase):
    def setUp(self):
        led._validate_commands.cache_clear()
        self.addCleanup(led._validate_commands.cache_clear)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        file = Path(folder)/'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
        file.parent.mkdir(parents=True);file.write_bytes(b'explicitly modeled command source')
        self.stack.enter_context(patch.dict('os.environ', PORSCHE981_DEFINITION_ROOT=folder))
        self.sources = {pid: fixture(pid)[1] for pid in led.VERSION_RULES}
        self.commands = {cid: dict(recordFound=True, decodedPayloadHex=hx, decodedPayloadSha256=sha) for cid, hx, sha in led.COMMANDS}
        self.stack.enter_context(patch.object(led, '_source_profile', side_effect=lambda pid, ecu, generation: self.sources[pid]))
        self.stack.enter_context(patch.object(dtc, '_source_profile', side_effect=lambda pid, ecu, generation: self.sources[pid]))
        self.stack.enter_context(patch.object(led, '_lookup_command', side_effect=lambda data, cid: self.commands[cid]))
        self.stack.enter_context(patch('scripts.diagnostics.x431_variant_selection._load_version_language', return_value=Reader()))

    def test_only_declared_target_and_generations_produce_an_identity_only_contract(self):
        for pid in led.VERSION_RULES:
            for generation in ('981', '982'):
                p, _ = fixture(pid, generation)
                c = prepared_read_contract(p, [], identity_only=True)
                self.assertEqual(c['sourceIdentityKind'], led.IDENTITY_KIND)
                self.assertEqual(c['groups'], [])
                self.assertEqual((c['txId'], c['rxId']), (0x725, 0x78F))
                self.assertTrue(all('record' not in r for r in c['identityRequests']))
                self.assertEqual(c['generation'], generation)
                with self.assertRaisesRegex(ValueError, 'parameters-unavailable'):
                    prepared_read_contract(p, [])
                for change in ({'selectedEcuId': 48}, {'selectedEcuId': True}, {'ecuIds': [47, 48]},
                               {'generation': '981'}, {'requestedGeneration': '987'}, {'identity': [{'requestHex': '22F190'}]}):
                    with self.subTest(change=change), self.assertRaises(ValueError):
                        led.source_contract({**p, **change}, [])

    def test_changed_source_route_selector_rule_command_or_identity_is_rejected(self):
        pid = next(iter(led.VERSION_RULES))
        original = deepcopy(self.sources[pid])
        mutations = (
            lambda s: s['versionRoutes'][0]['routes'][0].update(flagsWord=1),
            lambda s: s['versionRoutes'][0]['routes'][0]['selectors'][2].update(rightWordSigned=-3677),
            lambda s: s['versionRoutes'][0]['routes'][0]['versionMatches'][0].update(rules=['EV_OTHER#001']),
            lambda s: s['diagnosticSources'][0]['links'][0]['communication']['pairs'][0].update(storedRx=0x7BE << 5),
            lambda s: s['diagnosticSources'][0]['links'][0]['communication'].update(modeByte=0x30),
            lambda s: s['identityGroups'].append({'requestHex': '22F190'}),
            lambda s: s['measurementGroups'].append({'requestHex': '22F100'}),
        )
        for mutate in mutations:
            self.sources[pid] = deepcopy(original);mutate(self.sources[pid])
            with self.assertRaises(ValueError):led.source_contract(fixture(pid)[0], [])
        self.sources[pid] = original
        self.commands[0x20000A]['decodedPayloadHex'] = '00021002'
        led._validate_commands.cache_clear()
        with self.assertRaisesRegex(ValueError, 'command-unresolved'):
            led.source_contract(fixture(pid)[0], [])

    def test_command_validation_reuses_only_exact_content_and_never_caches_failure(self):
        with patch.object(led, '_lookup_command', side_effect=lambda data, cid: self.commands[cid]) as lookup:
            led._validate_commands(b'first content');led._validate_commands(b'first content')
            self.assertEqual(lookup.call_count, 4)
            led._validate_commands(b'changed content')
            self.assertEqual(lookup.call_count, 8)
            self.commands[0x20000A]['decodedPayloadHex'] = '00021002'
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'command-unresolved'):
                    led._validate_commands(b'invalid changed content')
            self.assertEqual(lookup.call_count, 10)

    def case(self, version=CASES[0][0], family=CASES[0][1], native=False, overrides=None, read_faults=True, anchor=True):
        profiles = [fixture(pid)[0] for pid in led.VERSION_RULES]
        responses = {hx: bytes.fromhex(value) for hx, value in replies(family).items()}
        responses.update({'1003': bytes.fromhex('5003003201F4'), '190208': bytes.fromhex('5902FFABCDEF08')})
        responses.update(overrides or {})
        device = FakeNative();device.make_events = lambda body, cop: FakeNative.reply(responses[body.hex().upper()], cop)
        ports = []
        def factory(cancel):
            if native:return DpuClient(ID, native=device, cancel_event=cancel)
            port = SessionSimPort('porsche-981-2014-dme')
            port.ecu_map = {hx: ath1_prompt(isotp_ath1_lines(0x78F, body)) for hx, body in responses.items()}
            ports.append(port);return ElmClient(port, cancel_event=cancel)
        gateway = dict(ok=True, identityOnly=True, executionEnabled=True, vehicleDataCollected=False,
            vehicleGeneration='981', vehicleIdentitySha256='a'*64, readContract={'ecuId': 9},
            identifiedProfileId=read.GATEWAY_PROFILE, transport=dict(closed=True, closeErrors=[], qualification=dict(
                ecuId=9, sourceCatalogId=led.SOURCE_CATALOG, freshRead=True, observedProfileMatch=True,
                variantLink={'profile_id': read.GATEWAY_PROFILE})))
        result = _client_cycles(profiles[0], [], factory, physical=True, source='modeled-headlamp-read', identify_profiles=profiles,
            read_dtcs=read_faults, vehicle_anchor_result=gateway if read_faults and anchor else None,
            transport_kind='d-pdu-iso-tp' if native else 'elm-iso-tp')
        return result, device.sent if native else ports[0].ecu_payloads, device if native else ports[0]

    def test_all_four_versions_resolve_on_both_read_transports_and_only_read_faults(self):
        for version, family in CASES:
            for native in (False, True):
                with self.subTest(version=version, family=family, native=native):
                    result, sent, client = self.case(version, family, native)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['identifiedProfileId'], PREFIX + version)
                    self.assertEqual(result['dtcRead']['decoded']['records'][0]['dtcHex'], 'ABCDEF')
                    self.assertTrue(result['transport']['closed'])
                    self.assertEqual(sent, ['1003', '22F19E', '22F1A2', '22F1DF', '22F187', '22F189', '22F191', '1003', '190208'])
                    self.assertFalse(result['liveVerified'])
                    self.assertEqual(len(result['identityInfo']), 6)

    def test_partial_or_wrong_identity_never_dispatches_a_fault_read(self):
        cases = ({'22F1A2': b'\x62\xf1\xa2' + b'001'},
                 {'22F19E': b'\x62\xf1\x9e' + CASES[0][1].encode()},
                 {'22F191': b'\x62\xf1\x91' + b'\0'*11},
                 {'22F19E': b'\x62\xf1\x9eEV_OTHER\0'})
        for native in (False, True):
            for changes in cases:
                with self.subTest(native=native, changes=changes):
                    result, sent, _ = self.case(native=native, overrides=changes)
                    self.assertFalse(result['ok'])
                    self.assertNotIn('190208', sent)
                    self.assertTrue(result['transport']['closed'])

    def test_unanchored_unit_does_not_read_faults_and_malformed_fault_is_not_zero(self):
        for native in (False, True):
            result, sent, _ = self.case(native=native, anchor=False)
            self.assertFalse(result['ok']);self.assertNotIn('190208', sent)
            result, _, _ = self.case(native=native, overrides={'190208': bytes.fromhex('5902FFAB')})
            self.assertFalse(result['ok']);self.assertTrue(result['transport']['closed'])

    def discovery_case(self, hit='normal', error=None, closed=True):
        normal = next(iter(SWSG_VERSIONS));headlamp = next(iter(led.VERSION_RULES))
        profiles = {normal: dict(profileId=normal, ecuIds=[47, 48], generation=None, identity=[]),
                    headlamp: fixture(headlamp)[0],
                    read.GATEWAY_PROFILE: dict(profileId=read.GATEWAY_PROFILE, ecuIds=[9], generation=None, identity=[])}
        calls = []
        def cycles(profile, selected, factory, **options):
            pid = profile['profileId'];calls.append((pid, selected, options))
            gateway = pid == read.GATEWAY_PROFILE
            last = options.get('read_dtcs', False)
            ok = gateway or last or error is None and pid == (normal if hit == 'normal' else headlamp if hit == 'led' else None)
            result = read._empty_result(read.SOURCE)
            result.update(ok=ok, error=None if ok else error or 'identity-mismatch', identityOnly=not last,
                identifiedProfileId=pid, vehicleGeneration='981', vehicleIdentitySha256='a'*64)
            result['transport'].update(closed=True if gateway else closed)
            return result
        request = dict(action='manufacturer-read-dtc', generation='981', ecuId=47, profileId=normal,
            parameterIds=[], deviceId='bt:001122334455', confirmedReadOnly=True, x431Inactive=True)
        with patch.object(read, 'READ_PROFILES', {normal: read.READ_PROFILES[normal], read.GATEWAY_PROFILE: read.READ_PROFILES[read.GATEWAY_PROFILE]}), \
                patch.object(read, 'FAULT_READ_PROFILES', {pid: read.FAULT_READ_PROFILES[pid] for pid in profiles}), \
                patch.object(read, 'verified_profile', side_effect=lambda request, root=None: profiles[request['profileId']]), \
                patch.object(read, '_client_cycles', side_effect=cycles), \
                patch('scripts.diagnostics.connection.open_selected_port', side_effect=AssertionError('no physical device')):
            result = read.read_manufacturer(request)
        return result, [c for c in calls if c[0] != read.GATEWAY_PROFILE], normal, headlamp

    def test_normal_identity_avoids_led_probe_and_led_identity_is_separately_requalified(self):
        result, calls, normal, headlamp = self.discovery_case()
        self.assertTrue(result['ok'], result)
        self.assertEqual([r[0] for r in calls], [normal, normal])
        self.assertNotIn(headlamp, [r[0] for r in calls])
        result, calls, normal, headlamp = self.discovery_case('led')
        self.assertTrue(result['ok'], result)
        self.assertEqual([r[0] for r in calls], [normal, headlamp, headlamp])
        self.assertTrue(all(r[1] == [] for r in calls))
        self.assertTrue(calls[-1][2]['read_dtcs'])
        self.assertEqual(len({r[2]['batch_deadline'] for r in calls}), 1)

    def test_unreleased_or_cancelled_probe_never_tries_another_family(self):
        for error, closed in (('cancelled', True), ('adapter-identity-mismatch', True), ('recovery-close-failed', False)):
            result, calls, _, _ = self.discovery_case('led', error, closed)
            self.assertFalse(result['ok']);self.assertEqual(len(calls), 1)
        result, calls, _, _ = self.discovery_case('none')
        self.assertFalse(result['ok']);self.assertEqual(result['error'], 'manufacturer-source-route-unidentified')
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
