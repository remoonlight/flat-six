import copy
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.catalog import load_catalog, profile_by_id
from scripts.diagnostics.decode import decode_payload
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.sessions import run_session


class ReferenceTextTests(unittest.TestCase):
    def operation(self, profile_id):
        profile = profile_by_id(load_catalog(), profile_id)
        op = next(op for op in profile['operations']
                  if op['decode']['type'] in ('kwp-dtc-18', 'uds-dtc-19-02'))
        return profile, op

    def test_reference_does_not_override_wire_code_status_or_observed_text(self):
        for pid in ('porsche-981-2014-dme', 'porsche-981-2014-gateway'):
            profile, op = self.operation(pid)
            spec = op['decode']
            key = next(key for key in spec['referenceCatalog']['records']
                       if key not in spec.get('observedCatalog', {}))
            prefix = '5802' if pid.endswith('dme') else '5902FF'
            result = decode_payload(prefix + key + '09' + key + '28', spec)
            self.assertTrue(result['ok'], result)
            self.assertEqual([row['dtcHex'] for row in result['records']], [key, key])
            self.assertEqual([row['statusHex'] for row in result['records']], ['09', '28'])
            for row in result['records']:
                self.assertEqual(row['referenceText'], spec['referenceCatalog']['records'][key]['text'])
                self.assertNotIn('observedText', row)
                self.assertNotIn('observedStatusText', row)
                self.assertFalse(row['referenceTextIsObservedVehicleText'])
                self.assertFalse(row['referenceTextQualifiesEcuVersion'])

    def test_missing_or_invalid_reference_never_removes_received_fault(self):
        _, op = self.operation('porsche-981-2014-dme')
        for change in ({'records': {}}, {'referenceOnly': False}, {'ecuVersionQualified': True},
                       {'dataSha256': 'bad'}, {'records': {'FFFF': {'text': ''}}}):
            spec = copy.deepcopy(op['decode'])
            spec['referenceCatalog'].update(change)
            result = decode_payload('5801FFFF28', spec)
            self.assertTrue(result['ok'])
            self.assertEqual(len(result['records']), 1)
            self.assertEqual(result['records'][0]['dtcHex'], 'FFFF')
            self.assertNotIn('referenceText', result['records'][0])

    def test_full_fresh_identity_precedes_reference_text_on_product_session(self):
        for pid in ('porsche-981-2014-dme', 'porsche-981-2014-gateway'):
            profile, op = self.operation(pid)
            key = next(key for key in op['decode']['referenceCatalog']['records']
                       if key not in op['decode'].get('observedCatalog', {}))
            pdu = ('5801' if pid.endswith('dme') else '5902FF') + key + '09'
            for mismatch in (False, True):
                port = SessionSimPort(pid, 'identity-mismatch' if mismatch else 'success')
                port.ecu_map[op['requestHex']] = ath1_prompt(isotp_ath1_lines(
                    int(profile['rxId'], 16), bytes.fromhex(pdu)))
                with tempfile.TemporaryDirectory() as temp:
                    out = run_session(profile_id=pid, mode='simulation', port=port, artifact_root=Path(temp))
                self.assertTrue(port.closed)
                if mismatch:
                    self.assertFalse(out['ok'])
                    self.assertNotIn(op['requestHex'], port.ecu_payloads)
                else:
                    self.assertTrue(out['ok'], out)
                    row = next(row for row in out['results'] if row.get('operationId') == op['id'])
                    self.assertTrue(row['freshlyRead'])
                    self.assertIn('referenceText', row['decoded']['records'][0])
                    self.assertFalse(out['liveVerified'])


if __name__ == '__main__':
    unittest.main()
