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

    def test_live_and_wrong_profile_never_touch_provider(self):
        def forbidden(_): raise AssertionError('provider called')
        out = acquire_cycle(profile(), ['a' * 64], forbidden, mode='live')
        self.assertEqual(out['error'], 'manufacturer-transport-unqualified')
        for key, value in [('status', 'selector-candidate'), ('ecuIds', [9]), ('generation', '982')]:
            p = profile(); p[key] = value
            self.assertEqual(acquire_cycle(p, ['a' * 64], forbidden)['error'], 'manufacturer-profile-unqualified')

    def test_group_atomic_negative_short_timeout_and_missing(self):
        for value in (b'', None, bytes.fromhex('7F2278'), bytes.fromhex('7F2231'),
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
