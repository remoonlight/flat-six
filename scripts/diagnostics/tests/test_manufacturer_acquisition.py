from copy import deepcopy
import hashlib
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.manufacturer_acquisition import acquire_cycle, rehearsal_cycle, DME_PROFILE
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.realtime_preparation import handle_ready, dump, sha


def profile():
    return {'profileId': DME_PROFILE, 'generation': '981', 'ecuIds': [1], 'status': 'identity-matched',
            'parameters': [field('a'), field('b', offset=1)], 'identity': [], 'checklist': []}


class AcquisitionTests(unittest.TestCase):
    def test_decoded_identifier_without_display_preserves_the_complete_value(self):
        for value in ('0000000000000000', ''):
            with self.subTest(value=value), patch(
                'scripts.diagnostics.manufacturer_acquisition.decode_application_response',
                return_value={'ok': True, 'value': value, 'display': None, 'numeric': False}):
                out = acquire_cycle(profile(), ['a'*64], lambda _: bytes.fromhex('62F10000'))
                self.assertTrue(out['ok'], out)
                self.assertEqual(out['samples'][0]['value'], value)
                self.assertEqual(out['samples'][0]['display'], value)

    def test_invalid_value_does_not_discard_healthy_fields_or_other_requests(self):
        p = profile(); p['parameters'].append(field('c', '22F101'))
        p['parameters'][0]['formula']['text'] += 'Ic:[0xFF]->0xF0005B27'
        out = acquire_cycle(p, ['a'*64, 'b'*64, 'c'*64],
            lambda hx: bytes.fromhex('62F100FF07' if hx == '22F100' else '62F1012A'))
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['value'] for s in out['samples']], [7, 42])
        self.assertEqual(out['unavailableParameters'], [])
        self.assertEqual(len(out['invalidParameters']), 1)
        entry = out['invalidParameters'][0]
        self.assertEqual((entry['parameterId'], entry['reason']), ('a'*64, 'invalid_reserved'))
        self.assertEqual(entry['pduSha256'], out['transactions'][0]['pduSha256'])
        self.assertEqual(entry['processedUtc'], out['transactions'][0]['processedUtc'])
        self.assertTrue(all(t['ok'] for t in out['transactions']))

    def test_invalid_value_then_truncated_field_keeps_group_atomic(self):
        p = profile(); p['parameters'][0]['formula']['text'] += 'Ic:[0xFF]->0xF0005B27'
        out = acquire_cycle(p, ['a'*64, 'b'*64], lambda _: bytes.fromhex('62F100FF'))
        self.assertFalse(out['ok'], out)
        self.assertEqual(out['invalidParameters'], [])
        self.assertEqual(out['samples'], [])

    def test_only_known_value_errors_are_isolated(self):
        from scripts.diagnostics.manufacturer_acquisition import INVALID_VALUE_REASONS
        for reason in [*INVALID_VALUE_REASONS, 'unexpected-decoder-error']:
            with self.subTest(reason=reason), patch(
                'scripts.diagnostics.manufacturer_acquisition.decode_application_response',
                return_value={'ok': False, 'reason': reason}):
                out = acquire_cycle(profile(), ['a'*64], lambda _: bytes.fromhex('62F100FF'))
                self.assertEqual(out['ok'], reason in INVALID_VALUE_REASONS)
                self.assertEqual(len(out['invalidParameters']), int(reason in INVALID_VALUE_REASONS))
                self.assertEqual(out['samples'], [])

    def test_conflicting_request_metadata_is_rejected_before_provider(self):
        def forbidden(_):raise AssertionError('provider called')
        for change in ({'pid':0xF101},{'wireSID':0x21},{'rawSID':0x2E},{'read_request_candidate_hex':'22F101'}):
            with self.subTest(change=change):
                p=profile();p['parameters'][0].update(change)
                result=acquire_cycle(p,['a'*64],forbidden)
                self.assertFalse(result['ok']);self.assertEqual(result['error'],'manufacturer-definition-incomplete');self.assertEqual(result['transactions'],[])

    def test_embedded_record_request_and_span_must_match_selected_parameter(self):
        def forbidden(_):raise AssertionError('provider called')
        for record in (field('a',request='22F101'),field('a',offset=3)):
            p=profile();p['parameters'][0]['record']=record
            result=acquire_cycle(p,['a'*64],forbidden)
            self.assertFalse(result['ok']);self.assertEqual(result['transactions'],[])

    def test_group_once_values_and_no_vehicle_claim(self):
        requests = []
        def receive(hx):
            requests.append(hx)
            return bytes.fromhex('62F1002A07')
        out = acquire_cycle(profile(), ['a' * 64, 'b' * 64], receive)
        self.assertTrue(out['ok'], out)
        self.assertEqual(requests, ['22F100'])
        self.assertEqual([s['value'] for s in out['samples']], [42, 7])
        self.assertEqual(out['completedCycles'], 1)
        self.assertFalse(out['vehicleDataCollected'])
        self.assertTrue(all(s['synthetic'] for s in out['samples']))

    def test_shared_response_observation_precedes_field_decoding(self):
        from scripts.diagnostics import manufacturer_acquisition as acquisition
        original = acquisition.decode_application_response
        clock = [100.0]
        def decode(*args, **kwargs):
            clock[0] += 10  # Decoding work must not change either field's observation.
            return original(*args, **kwargs)
        def receive(_):
            clock[0] = 102.5
            return bytes.fromhex('62F1002A07')
        with patch.object(acquisition.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(acquisition, 'decode_application_response', side_effect=decode):
            out = acquire_cycle(profile(), ['a' * 64, 'b' * 64], receive)
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['elapsedMs'] for s in out['samples']], [2500, 2500])
        self.assertEqual(out['samples'][0]['processedUtc'], out['samples'][1]['processedUtc'])

    def test_connection_change_between_groups_in_one_cycle_uses_each_response_epoch(self):
        from scripts.diagnostics.manufacturer_acquisition import _decode_cycle
        p = profile(); p['parameters'][1] = field('b', '22F101')
        epoch = [0]
        def receive(hx):
            epoch[0] += 1
            return bytes.fromhex('62F1002A' if hx == '22F100' else '62F10107')
        out = _decode_cycle(p, ['a' * 64, 'b' * 64], receive, cancelled=lambda: False,
                            source='synthetic-ecu', synthetic=True, connection_epoch=lambda: epoch[0])
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['connectionEpoch'] for s in out['samples']], [1, 2])
        self.assertEqual([t['connectionEpoch'] for t in out['transactions']], [1, 2])

    def test_live_and_wrong_profile_never_touch_provider(self):
        def forbidden(_): raise AssertionError('provider called')
        out = acquire_cycle(profile(), ['a' * 64], forbidden, mode='live')
        self.assertEqual(out['error'], 'manufacturer-transport-unqualified')
        for key, value in [('status', 'selector-candidate'), ('ecuIds', [9]), ('generation', '982')]:
            p = profile(); p[key] = value
            self.assertEqual(acquire_cycle(p, ['a' * 64], forbidden)['error'], 'manufacturer-profile-unqualified')

    def test_group_atomic_negative_short_timeout_and_missing(self):
        for value in (b'', None, bytes.fromhex('7F2278'), bytes.fromhex('7F2233'),
                      bytes.fromhex('62F1002A'), bytes.fromhex('62F1012A07')):
            out = acquire_cycle(profile(), ['a' * 64, 'b' * 64], lambda _: value)
            self.assertFalse(out['ok']); self.assertEqual(out['samples'], [])
            self.assertEqual(out['completedCycles'], 0)
        def timeout(_): raise TimeoutError('response-timeout')
        self.assertEqual(acquire_cycle(profile(), ['a' * 64], timeout)['error'], 'response-timeout')

    def test_cancel_and_selection_before_read(self):
        def forbidden(_): raise AssertionError('provider called')
        out = acquire_cycle(profile(), ['a' * 64], forbidden, cancelled=lambda: True)
        self.assertEqual(out['error'], 'cancelled')
        for ids in ([], ['a' * 64] * 2, ['f' * 64], ['a' * 64] * 13):
            self.assertFalse(acquire_cycle(profile(), ids, forbidden)['ok'])

    def test_prior_completed_group_preserved_on_failure(self):
        p = profile(); p['parameters'][1] = field('b', '22F101')
        out = acquire_cycle(p, ['a' * 64, 'b' * 64],
                            lambda hx: bytes.fromhex('62F1002A') if hx == '22F100' else b'')
        self.assertFalse(out['ok']); self.assertEqual(len(out['samples']), 1)
        self.assertEqual(out['samples'][0]['value'], 42)

    def test_explicit_unsupported_group_disables_only_its_fields_and_keeps_other_group(self):
        p = profile(); p['parameters'].append(field('c', '22F101'))
        for nrc in (0x11, 0x31):
            seen = []
            def receive(hx):
                seen.append(hx)
                return bytes((0x7F, 0x22, nrc)) if hx == '22F100' else bytes.fromhex('62F10107')
            out = acquire_cycle(p, ['a'*64, 'b'*64, 'c'*64], receive)
            self.assertTrue(out['ok'], out)
            self.assertEqual(seen, ['22F100', '22F101'])
            self.assertEqual([s['parameterId'] for s in out['samples']], ['c'*64])
            self.assertEqual([s['parameterId'] for s in out['unavailableParameters']], ['a'*64, 'b'*64])
            self.assertEqual(out['unavailableParameters'][0]['pduSha256'], out['transactions'][0]['pduSha256'])
            self.assertEqual(out['unavailableParameters'][0]['processedUtc'], out['transactions'][0]['processedUtc'])

    def test_other_negatives_never_become_unavailable_and_do_not_continue(self):
        p = profile(); p['parameters'][1] = field('b', '22F101')
        for pdu in ('7F2221', '7F2222', '7F2233', '7F2278', '7F2131', '7F223100', '7F22'):
            seen = []
            def receive(hx):
                seen.append(hx); return bytes.fromhex(pdu)
            out = acquire_cycle(p, ['a'*64, 'b'*64], receive)
            self.assertFalse(out['ok'], pdu)
            self.assertEqual(out['unavailableParameters'], [])
            self.assertEqual(seen, ['22F100'])

    def test_exact_historical_source_hash_address_phase(self):
        p = profile(); raw = bytes.fromhex('62F1002A07')
        for param in p['parameters']:
            param.update(decodedSampleCount=1, examples=[{'groupId': 'g', 'responseFrameId': 'f',
                'pduSha256': hashlib.sha256(raw).hexdigest()}])
        group = {'groupId': 'g', 'requestHex': '22F100', 'txId': 0x7E0, 'rxId': 0x7E8,
                 'adapterStream': [2, 2], 'capturePhase': 'before-coding',
                 'samples': [{'responseFrameId': 'f', 'pduHex': raw.hex()}]}
        self.assertTrue(rehearsal_cycle(p, ['a' * 64, 'b' * 64], [group])['ok'])
        for key, value in [('txId', 0x710), ('adapterStream', [1, 2]), ('capturePhase', 'coding-workflow')]:
            wrong = deepcopy(group); wrong[key] = value
            with self.assertRaisesRegex(ValueError, 'reference-mismatch'):
                rehearsal_cycle(p, ['a' * 64], [wrong])
        wrong = deepcopy(group); wrong['samples'][0]['pduHex'] = '62F1000000'
        with self.assertRaisesRegex(ValueError, 'reference-mismatch'):
            rehearsal_cycle(p, ['a' * 64], [wrong])
        p['parameters'][0]['decodedSampleCount'] = 0
        with self.assertRaisesRegex(ValueError, 'response-missing'):
            rehearsal_cycle(p, ['a' * 64], [group])

    def test_bridge_rejects_stale_code_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'profiles').mkdir()
            filename = 'profiles/' + '0' * 64 + '.json'
            dump(root / filename, profile())
            dump(root / 'index.json', {'profiles': [{'profileId': DME_PROFILE, 'ecuIds': [1],
                'file': filename, 'sha256': sha(root / filename)}]})
            code = root / 'compiler.py'; code.write_text('old')
            dump(root / 'manifest.json', {'outputs': [{'path': str(root / 'index.json'), 'sha256': sha(root / 'index.json')}],
                'code': [{'path': str(code), 'sha256': sha(code)}], 'inputs': []})
            req = {'action': 'ready-acquire', 'ecuId': 1, 'profileId': DME_PROFILE, 'parameterIds': ['a' * 64]}
            self.assertEqual(handle_ready({**req, 'mode': 'live'}, root)['error'], 'manufacturer-forbidden-field')
            code.write_text('changed')
            out = handle_ready(req, root)
            self.assertFalse(out['ok']); self.assertIn('source-hash-mismatch', out['error'])

    def test_portable_bundle_relocated_without_original_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / 'old'; (root / 'profiles').mkdir(parents=True)
            p = profile(); raw = bytes.fromhex('62F1002A07')
            for parameter in p['parameters']:
                parameter.update(decodedSampleCount=1, examples=[{'groupId': 'g', 'responseFrameId': 'f',
                    'pduSha256': hashlib.sha256(raw).hexdigest()}])
            filename = 'profiles/' + '0' * 64 + '.json'; dump(root / filename, p)
            dump(root / 'index.json', {'profiles': [{'profileId': DME_PROFILE, 'ecuIds': [1],
                 'file': filename, 'sha256': sha(root / filename)}]})
            dump(root / 'rehearsal-fixtures.json', [{'groupId': 'g', 'requestHex': '22F100', 'txId': 0x7E0,
                'rxId': 0x7E8, 'adapterStream': [2, 2], 'capturePhase': 'before-coding',
                'samples': [{'responseFrameId': 'f', 'pduHex': raw.hex()}]}])
            codeRoot = base / 'runtime'; (codeRoot / 'scripts').mkdir(parents=True)
            code = codeRoot / 'scripts/compiler.py'; code.write_text('known-code')
            dump(root / 'manifest.json', {'schemaVersion': 2,
                'inputs': [{'path': 'Z:/unavailable-original/capture.bin', 'sha256': '0' * 64}],
                'outputs': [{'path': name, 'sha256': sha(root / name)} for name in ('index.json', 'rehearsal-fixtures.json')],
                'code': [{'path': 'scripts/compiler.py', 'sha256': sha(code)}]})
            relocated = base / 'new machine with spaces'; shutil.move(root, relocated)
            req = {'action': 'ready-acquire', 'ecuId': 1, 'profileId': DME_PROFILE, 'parameterIds': ['a' * 64, 'b' * 64]}
            with patch('scripts.diagnostics.realtime_preparation.ROOT', codeRoot):
                out = handle_ready(req, relocated)
                self.assertTrue(out['ok'], out); self.assertEqual(len(out['samples']), 2)
                self.assertTrue(out['transport']['closed'])
                (relocated / 'rehearsal-fixtures.json').write_text('[]')
                self.assertIn('source-hash-mismatch', handle_ready(req, relocated)['error'])


if __name__ == '__main__': unittest.main()
