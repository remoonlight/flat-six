from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.diagnostics.identity_capture import (
    FLAGS, ROUTE_REFERENCES, field_result, observations, profile_evidence,
    reference_routes, source_profiles,
)
from scripts.diagnostics.offline_match import ANCHORS, verify_frames
from scripts.diagnostics.source_catalog import GUARDS
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_x431_capture import fix


class IdentityCaptureTests(unittest.TestCase):
    def fixtures(self):
        record = dict(field(), at=42)
        group = dict(requestHex='22F100', minimumKnownBytes=1, expectedTotalBytes=None,
                     fields=[dict(sourceOffset=42, decodingDefinition=record)])
        profile = dict(profileId='source-pdk', module='Getriebesteuerung_PDK', variant='PDK',
                       identityGroups=[group], identityMissing=[])
        unit = dict(generation='981', ecuId=2)
        routes = {2: dict(txId=0x71E, rxId=0x788, liveAddressQualified=False)}
        sample = dict(txId=0x71E, rxId=0x788, requestHex='22F100', pduHex='62F1002A',
                      observationId='q', status='positive-candidate')
        return unit, profile, routes, sample

    def test_positive_fields_stay_source_candidates_not_complete_identity(self):
        unit, profile, routes, sample = self.fixtures()
        result = profile_evidence(unit, profile, [sample], routes)
        self.assertEqual(result['sourceGroupsWithPositiveFields'], 1)
        group = result['identityGroups'][0]
        self.assertEqual(group['status'], 'historical-source-fields-decoded')
        self.assertEqual(group['values'][0]['value']['value'], 42)
        self.assertTrue(group['values'][0]['independentDecoderAgreement'])
        self.assertIsNone(group['expectedTotalBytes'])
        for key, value in FLAGS.items(): self.assertEqual(result[key], value)
        self.assertFalse(result['completeIdentityQualified'])

    def test_other_generation_and_unit_never_inherit_observations(self):
        unit, profile, routes, sample = self.fixtures()
        for changed in [dict(unit, generation='982'), dict(unit, ecuId=4)]:
            result = profile_evidence(changed, profile, [sample], routes)
            self.assertEqual(result['sourceGroupsWithPositiveFields'], 0)
            self.assertEqual(result['identityGroups'][0]['observations'], [])
        sample['txId'] = 0x7E0
        self.assertEqual(profile_evidence(unit, profile, [sample], routes)['sourceGroupsWithPositiveFields'], 0)

    def test_negative_pending_wrong_echo_and_address_stay_visible_without_values(self):
        unit, profile, routes, sample = self.fixtures()
        for status in ['negative', 'pending', 'echo-mismatch', 'address-unqualified', 'unqualified-return', 'unpaired']:
            with self.subTest(status=status):
                changed = dict(sample, status=status, pduHex='7F2231')
                group = profile_evidence(unit, profile, [changed], routes)['identityGroups'][0]
                self.assertEqual(group['status'], 'observed-without-positive-identity')
                self.assertEqual(group['observationStatuses'], {status: 1})
                self.assertEqual(group['observations'], ['q']); self.assertEqual(group['values'], [])
        sample['rxId'] = 0x77A
        group = profile_evidence(unit, profile, [sample], routes)['identityGroups'][0]
        self.assertEqual(group['values'], [])

    def test_changed_value_is_conflict_never_last_value_wins(self):
        unit, profile, routes, sample = self.fixtures()
        second = dict(sample, observationId='q2', pduHex='62F1002B')
        group = profile_evidence(unit, profile, [sample, second], routes)['identityGroups'][0]
        self.assertEqual(group['status'], 'historical-value-conflict')
        self.assertEqual(group['conflictingFieldOffsets'], [42])
        self.assertEqual(len(group['values']), 2)

    def test_truncation_and_independent_reference_failure_are_explicit(self):
        unit, profile, routes, sample = self.fixtures()
        sample['pduHex'] = '62F100'
        group = profile_evidence(unit, profile, [sample], routes)['identityGroups'][0]
        self.assertEqual(group['status'], 'historical-decode-gap')
        self.assertFalse(group['values'][0]['value']['ok'])
        sample['pduHex'] = '62F1002A'
        with patch('scripts.diagnostics.identity_capture.reference_value', side_effect=ValueError('unsupported')):
            group = profile_evidence(unit, profile, [sample], routes)['identityGroups'][0]
        self.assertFalse(group['values'][0]['independentDecoderAgreement'])
        self.assertIn('unsupported', group['values'][0]['issue'])

    def test_definition_request_mismatch_is_not_borrowed(self):
        _, profile, _, sample = self.fixtures()
        with self.assertRaisesRegex(ValueError, 'field-request-conflict'):
            field_result(profile['identityGroups'][0]['fields'][0], '22F101', sample)

    def packet_frames(self, pdu='7F2231'):
        request = bytearray.fromhex('55aaf0f80016012701640001ff020d610108e3c00322f100aaaaaa0000')
        reply = bytearray.fromhex('55aaf8f0001601670100000155aa0b08f10003')+bytes.fromhex(pdu)+bytes(7)
        out = []
        for i, (raw, direction) in enumerate([(request,'host_to_controller'),(reply,'controller_to_host')]):
            b = fix(raw)
            out.append(dict(frameId='f'+str(i), frameHex=b.hex(), frameSha256=hashlib.sha256(b).hexdigest(),
                stream=[2,direction,2], length=len(b), sequenceByte=b[6], commandByte=b[7], startRecord=10+i))
        return out

    def test_raw_negative_pdu_hash_stream_phase_and_frames_retained(self):
        frames = self.packet_frames(); verify_frames(frames)
        rows = observations(frames, {0x71E:0x788}, 10)
        self.assertEqual(len(rows), 1)
        row = rows[0]; self.assertEqual(row['status'], 'negative'); self.assertEqual(row['nrc'], 0x31)
        self.assertEqual(row['pduHex'], '7F2231')
        self.assertEqual(row['pduSha256'], hashlib.sha256(bytes.fromhex('7F2231')).hexdigest())
        self.assertEqual(row['capturePhase'], 'before-coding'); self.assertEqual(row['adapterStream'], [2,2])
        self.assertEqual(row['responseFrameSha256'], frames[1]['frameSha256'])
        frames[1]['frameHex'] = frames[1]['frameHex'][:-2]+'00'
        with self.assertRaisesRegex(ValueError, 'invalid-frame'): verify_frames(frames)

    def test_pending_and_cross_stream_do_not_become_negative_or_positive(self):
        rows = observations(self.packet_frames('7F2278'), {0x71E:0x788}, 0)
        self.assertEqual(rows[0]['status'], 'pending'); self.assertEqual(rows[0]['capturePhase'], 'coding-workflow')
        frames = self.packet_frames(); frames[1]['stream'][2] = 3
        self.assertEqual(observations(frames, {0x71E:0x788}, 0)[0]['status'], 'unpaired')

    def test_explicit_route_reference_required_and_conflicts_rejected(self):
        definitions = {'definitions':[dict(id=source_id, definitionCategory='author-read-command',
                       requestAddress=format(0x700+index,'03X'), responseAddress=format(0x770+index,'03X'))
                       for index, source_id in enumerate(ROUTE_REFERENCES.values())]}
        routes, _ = reference_routes(definitions); self.assertEqual(len(routes), 7)
        self.assertFalse(routes[2]['liveAddressQualified'])
        changed = deepcopy(definitions); changed['definitions'].append(dict(changed['definitions'][0]))
        with self.assertRaisesRegex(ValueError, 'not-unique'): reference_routes(changed)
        changed = deepcopy(definitions); changed['definitions'].append(dict(id='different',requestAddress='700',responseAddress='799'))
        with self.assertRaisesRegex(ValueError, 'conflicting-address-source'): reference_routes(changed)

    def portable(self, root, mutation=None):
        unit, profile, _, _ = self.fixtures()
        name = 'profiles/'+'a'*64+'.json'
        profile.update(ecuIds=[2], generations=['981'])
        unit.update(variants=[dict(profileId='source-pdk',file=name)])
        docs = {'all-units.json': dict(units=[unit]), name:profile}
        data = {key:json.dumps(value).encode() for key,value in docs.items()}
        manifest = dict(schemaVersion=1, kind='x431-offline-source-catalog', **GUARDS,
            sourceFingerprints=[dict(sha256=ANCHORS['variants'])],
            generatorCode=[dict(name='scripts/diagnostics/identity_capture.py',sha256=hashlib.sha256(Path('scripts/diagnostics/identity_capture.py').read_bytes()).hexdigest())],
            files=[dict(name=key,bytes=len(value),sha256=hashlib.sha256(value).hexdigest()) for key,value in data.items()])
        if mutation == 'binding':
            docs[name]['ecuIds'] = [4]; data[name] = json.dumps(docs[name]).encode()
            manifest['files'][1].update(bytes=len(data[name]),sha256=hashlib.sha256(data[name]).hexdigest())
        if mutation == 'drift': manifest['generatorCode'][0]['sha256'] = '0'*64
        raw = json.dumps(manifest).encode(); prefix='.local/diagnostics/x431-source-references/'+hashlib.sha256(raw).hexdigest()+'/'
        if mutation == 'payload': data[name] = b'!'+data[name][1:]
        path = root/'definitions.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr(prefix+'manifest.json',raw)
            for key,value in data.items():z.writestr(prefix+key,value)
        return path,hashlib.sha256(path.read_bytes()).hexdigest()

    def test_portable_hash_payload_generator_and_unit_binding_checked(self):
        for mutation, error in [(None,None),('payload','payload-hash-mismatch'),('binding','profile-binding-invalid'),('drift','generator-drift')]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                path,digest = self.portable(Path(tmp),mutation)
                if error:
                    with self.assertRaisesRegex(ValueError,error): source_profiles(path,digest)
                else:
                    rows, source = source_profiles(path,digest)
                    self.assertEqual(len(rows),1);self.assertEqual(source['sourceFiles'],2)
                    self.assertTrue(source['generatorCodeMatchesCurrent'])
                    with self.assertRaisesRegex(ValueError,'source-hash-mismatch'):source_profiles(path,'0'*64)


if __name__ == '__main__': unittest.main()
