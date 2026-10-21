"""Fresh gateway VIN recognition with modeled ports; no vehicle/device I/O."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.manufacturer_transport import vehicle_generation_from_vin
from scripts.diagnostics.manufacturer_acquisition import GATEWAY_PROFILE
from scripts.diagnostics.tests.test_manufacturer_gateway import profile
from scripts.diagnostics.tests.test_manufacturer_read import bundle
from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract, model_responses
from scripts.diagnostics.tests.test_vnci import FakeNative, ID
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines


class VehicleIdentificationTests(unittest.TestCase):
    def case(self, vin, native=False, overrides=None):
        contract = model_contract(5)
        contract.update(ecuId=9, txId=0x710, rxId=0x77A)
        responses = model_responses(5)
        responses['22F190'] = b'\x62\xf1\x90' + vin.encode('ascii')
        responses.update(overrides or {})
        ports = []
        device = FakeNative()
        def opened(*args, **kwargs):
            port = SessionSimPort('porsche-981-2014-gateway')
            port.ecu_map.update({hx: ath1_prompt(isotp_ath1_lines(0x77A, body)) for hx, body in responses.items()})
            ports.append(port)
            return port, 'modeled-port'
        device.make_events = lambda body, cop: FakeNative.reply(responses[body.hex().upper()], cop)
        request = dict(action='manufacturer-identify-vehicle', deviceId=ID if native else 'bt:001122334455',
                       confirmedReadOnly=True, x431Inactive=True)
        frames = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle(root, profile())
            with patch.object(read, 'READ_PROFILES', {GATEWAY_PROFILE: read.READ_PROFILES[GATEWAY_PROFILE]}), \
                    patch.object(read, 'FAULT_READ_PROFILES', {GATEWAY_PROFILE: read.FAULT_READ_PROFILES[GATEWAY_PROFILE]}), \
                    patch('scripts.diagnostics.uds_source_read.source_contract', return_value=contract), \
                    patch('scripts.diagnostics.connection.open_selected_port', side_effect=opened), \
                    patch('scripts.diagnostics.vnci.NativeDpu', return_value=device):
                out = read.read_manufacturer(request, root=root,
                    on_pdu=lambda pdu: frames.append(dict(event='diagnostic-pdu', result=dict(executionEnabled=True,
                        liveVerified=False, writePayload=None, simulation=False, sourceKind=read.SOURCE, pdu=pdu))))
        final = read._redact(out)
        final.pop('samples', None)
        final.pop('transactions', None)
        final.pop('connectionEvents', None)
        frames.append(dict(event='finished', result=final))
        if os.getenv('OBD_VEHICLE_MODEL_OUTPUT') and out.get('ok') and not native:
            dest = Path(os.environ['OBD_VEHICLE_MODEL_OUTPUT'])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ('vehicle-' + out['vehicleGeneration'] + '.json')).write_text(json.dumps(dict(
                syntheticPort=True, deviceIO=False, vehicleIO=False, frames=frames), indent=2))
        return out, ports, device

    def test_documented_type_positions_and_years_are_required(self):
        for vin, expected in [('WP0ZZZ98ZES123456', '981'), ('WP0CA2A88ES123456', '981'),
                              ('WP0ZZZ98ZJS223456', '982'), ('WP0CA2A88JS223456', '982')]:
            self.assertEqual(vehicle_generation_from_vin(vin), expected)
        for vin in [None, '', 'WP0ZZZ98ZES12345I', 'WP0ZZZ98ZES223456', 'WP0ZZZ98ZJS123456',
                    'WP0ZZZ98ZCS723456', 'WP0ZZZ99ZJS223456', 'WA0ZZZ98ZJS223456', 'WP0ZZZ98Z9S223456']:
            self.assertIsNone(vehicle_generation_from_vin(vin), vin)

    def test_both_generations_are_detected_from_fresh_gateway_without_measurements(self):
        for vin, generation in [('WP0ZZZ98ZES123456', '981'), ('WP0ZZZ98ZJS223456', '982')]:
            for native in (False, True):
                out, ports, device = self.case(vin, native)
                self.assertTrue(out['ok'], out)
                self.assertEqual(out['vehicleGeneration'], generation)
                self.assertEqual(out['vehicleIdentitySha256'], hashlib.sha256(vin.encode()).hexdigest())
                self.assertTrue(out['identityOnly'])
                self.assertEqual(out['readContract']['ecuId'], 9)
                self.assertEqual(out['readContract']['groups'], [])
                self.assertEqual(out['completedCycles'], 0)
                self.assertEqual(out['samples'], [])
                self.assertFalse(out['vehicleDataCollected'])
                self.assertTrue(out['transport']['closed'])
                sent = device.sent if native else [x.decode().strip().upper() for x in ports[0].writes if not x.startswith(b'AT')]
                self.assertNotIn('22F102', sent)
                self.assertNotIn(vin, json.dumps(read._redact(out)))

    def test_unknown_or_conflicting_model_never_opens_a_generation(self):
        for vin in ['WP0ZZZ98ZCS723456', 'WP0ZZZ98ZES223456', 'WP0ZZZ99ZJS223456']:
            out, _, _ = self.case(vin)
            self.assertFalse(out['ok'])
            self.assertEqual(out['error'], 'vehicle-model-unidentified')
            self.assertIsNone(out['vehicleGeneration'])
            self.assertTrue(out['transport']['closed'])

    def test_failed_identity_never_publishes_a_vehicle(self):
        for overrides in [{'22F190': b'\x62\xf1\x91' + b'WP0ZZZ98ZES123456'}, {'22F1A2': b'\x7f\x22\x31'}]:
            out, _, _ = self.case('WP0ZZZ98ZES123456', overrides=overrides)
            self.assertFalse(out['ok'])
            self.assertNotIn('vehicleGeneration', out)
            self.assertTrue(out['transport']['closed'])

    def test_renderer_cannot_select_model_profile_or_probe_requests(self):
        request = dict(action='manufacturer-identify-vehicle', deviceId='bt:001122334455',
                       confirmedReadOnly=True, x431Inactive=True)
        for key, value in [('generation', '982'), ('profileId', GATEWAY_PROFILE), ('ecuId', 1),
                           ('parameterIds', []), ('cycleCount', None), ('sourceBound', True), ('vehicle_probe', True)]:
            with self.assertRaisesRegex(ValueError, 'manufacturer-forbidden-field'):
                read._validate({**request, key: value})
        for key in ['confirmedReadOnly', 'x431Inactive']:
            with self.assertRaisesRegex(ValueError, 'preconditions'):
                read._validate({**request, key: False})
