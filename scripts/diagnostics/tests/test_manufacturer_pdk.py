"""PDK client tests with synthetic byte ports/native events; no hardware I/O."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.elm import parse_ath1_response
from scripts.diagnostics.pdk_read import MODULE, VERSIONS, SELECTOR_PROFILE
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_read import bundle, request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_vnci import FakeNative, ID

PROFILE = MODULE + 'PDK_A3_ohne_Quersperre'


def model_profile():
    parameters = [field('a', '2122'), field('b', '2122', 1)]
    for parameter in parameters:
        parameter['readSID'] = 0x21
    return {'profileId': PROFILE, 'generation': None, 'ecuIds': [2], 'status': 'selector-candidate',
            'parameters': parameters, 'identity': [], 'checklist': []}


def model_contract():
    rows = []
    for hx, size in [('1A9F', 2), ('1A95', 4), ('1A9A', 8), ('1A87', 11), ('1A91', 11), ('1A90', 17)]:
        record = {'readSID': 0x1A, 'pid': int(hx[2:], 16), 'read_request_candidate_hex': hx,
                  'byteOffset': 0, 'bitOffset': 0, 'formula': {'text':
                    f'IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength={size*8},Encoding=ISO-8859-1,HighLow=1,Radix=10,DataType=A_ASCIISTRING,Precision=null;'}}
        if hx == '1A9F':
            record['formula']['text'] = 'IDENTICAL:Standar,BitMask=0,BaseDataType=A_BYTEFIELD,BitLength=16,Encoding=Undefined,HighLow=1,Radix=10,DataType=A_BYTEFIELD,Precision=null;'
        rows.append({'field': hx, 'requestHex': hx, 'record': record})
    return {'catalogProfileId': 'porsche-981-pdk-source', 'ecuId': 2, 'txId': 0x71E, 'rxId': 0x788,
            'sourceQualification': 'pdk-dsn-and-full-identity',
            'session': {'requestHex': '1089', 'positivePrefixHex': '5089'},
            'identityRequests': rows, 'identityConstraints': {'dsnHex': '4210'}}


def model_responses(**overrides):
    rows = {'1089': bytes.fromhex('5089'), '1A9F': bytes.fromhex('5A9F4210'),
            '1A95': bytes.fromhex('5A95') + b'1234', '1A9A': bytes.fromhex('5A9A') + b'12345678',
            '1A87': bytes.fromhex('5A87') + b'12345678901', '1A91': bytes.fromhex('5A91') + b'12345678902',
            '1A90': bytes.fromhex('5A90') + b'WP0ZZZ98ZES123456', '2122': bytes.fromhex('61222A07')}
    rows.update(overrides)
    return rows


class PdkTests(unittest.TestCase):
    def test_declared_unprogrammed_dsn_does_not_allow_blank_identity(self):
        from scripts.diagnostics.pdk_read import qualify_client,FAULT_ONLY_VERSIONS
        from scripts.diagnostics.elm import ElmError
        class Client:
            def __init__(self,replies):self.replies=replies
            def request(self,hx,deadline):return {'ok':True,'payload_hex':self.replies[hx].hex()}
        for pid,dsn in FAULT_ONLY_VERSIONS.items():
            c=model_contract();c['identityConstraints']={'dsnHex':dsn}
            replies=model_responses(**{'1A9F':bytes.fromhex('5A9F'+dsn)})
            identity,qualified=qualify_client(Client(replies),c,pid,time.monotonic()+30,lambda:None)
            self.assertEqual(identity['1A9F'],'5A9F'+dsn);self.assertTrue(qualified['freshRead'])
            for hx,data in [('1A91',b'\xFF'*11),('1A91',b'\0'*11),('1A90',b'I'*17),
                            ('1A9F',b'\xFF'*3),('1A9F',b'\0\0')]:
                bad={**replies,hx:bytes.fromhex('5A'+hx[2:])+data}
                with self.assertRaises(ElmError):
                    qualify_client(Client(bad),c,pid,time.monotonic()+30,lambda:None)
        c=model_contract();c['identityConstraints']={'dsnHex':'FFFF'}
        with self.assertRaises(ElmError):
            qualify_client(Client(model_responses(**{'1A9F':bytes.fromhex('5A9FFFFF')})),c,PROFILE,time.monotonic()+30,lambda:None)

    def run_case(self, *, device='elm', replies=None, cycles=2, wait_retry=None, on_cycle=None, port_mutator=None, selector=False):
        contract=model_contract();p=model_profile();pid=PROFILE;ecu=2
        if selector:
            pid=SELECTOR_PROFILE;ecu=68;p.update(profileId=pid,ecuIds=[68])
            contract.update(ecuId=68,catalogProfileId='porsche-981-selector-source',txId=0x753,rxId=0x7BD,identityConstraints={'dsnHex':'0100'})
            contract['identityRequests']=[r for r in contract['identityRequests'] if r['requestHex'] in ('1A9F','1A91','1A90')]
        responses = model_responses(**{**({'1A9F':bytes.fromhex('5A9F0100')} if selector else {}),**(replies or {})}); native = FakeNative(); ports = []
        native.make_events = lambda payload, cop: FakeNative.reply(responses[payload.hex().upper()], cop)

        def opened(*args, **kwargs):
            port = SessionSimPort('porsche-981-2014-dme')
            port.ecu_map = {hx: ath1_prompt(isotp_ath1_lines(contract['rxId'], pdu)) for hx, pdu in responses.items()}
            if port_mutator: port_mutator(port, len(ports))
            ports.append(port)
            return port, 'synthetic-pdk-port'

        req = {**request(), 'profileId': pid, 'ecuId': ecu, 'cycleCount': cycles}
        if device == 'vnci': req['deviceId'] = ID
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root, p)
            with patch('scripts.diagnostics.pdk_read.source_contract', return_value=contract), \
                    patch('scripts.diagnostics.connection.open_selected_port', side_effect=opened), \
                    patch('scripts.diagnostics.vnci.NativeDpu', return_value=native):
                result = read.read_manufacturer(req, root=root, wait_retry=wait_retry, on_cycle=on_cycle)
        return result, native if device == 'vnci' else ports

    def test_order_identity_and_one_request_for_shared_parameters_on_both_transports(self):
        for device in ['elm', 'vnci']:
            result, clients = self.run_case(device=device)
            self.assertTrue(result['ok'], result)
            sent = clients.sent if device == 'vnci' else clients[0].ecu_payloads
            self.assertEqual(sent, ['1089', '1A9F', '1089', '1A95', '1A9A', '1A87', '1A91', '1A90', '2122', '2122'])
            self.assertEqual([s['value'] for s in result['samples']], [42, 7, 42, 7])
            self.assertEqual(result['transport']['qualification']['variantLink']['profile_id'], PROFILE)
            self.assertTrue(result['transport']['closed']); self.assertFalse(result['liveVerified'])
            if device == 'vnci':
                self.assertEqual(clients.links, [('ISO_14230_3_on_ISO_15765_2', 0x71E, 0x788)])

    def test_other_or_unknown_version_never_reads_parameters(self):
        for dsn in ['4200', 'FFFF', '0000']:
            for device in ['elm', 'vnci']:
                result, clients = self.run_case(device=device, replies={'1A9F': bytes.fromhex('5A9F' + dsn)})
                self.assertFalse(result['ok']); self.assertEqual(result['error'], 'identity-mismatch')
                sent = clients.sent if device == 'vnci' else clients[0].ecu_payloads
                self.assertEqual(sent, ['1089', '1A9F']); self.assertEqual(result['samples'], [])
                self.assertTrue(result['transport']['closed'])

    def test_incomplete_misaddressed_and_blank_identity_never_samples(self):
        for hx, pdu in [('1A95', b'\x5a\x9a1234'), ('1A95', b'\x5a\x95\0\0\0\0'),
                        ('1A9A', b'\x5a\x9a123'), ('1A90', b'\x5a\x90' + b'I'*17),
                        ('1A90', b'\x7f\x1a\x31')]:
            result, ports = self.run_case(replies={hx: pdu})
            self.assertFalse(result['ok']); self.assertEqual(result['samples'], [])
            self.assertNotIn('2122', ports[0].ecu_payloads); self.assertTrue(result['transport']['closed'])

    def test_reconnect_rechecks_identity_before_any_further_measurement(self):
        def mutate(port, index):
            if index:
                port.ecu_map['1A95'] = ath1_prompt(isotp_ath1_lines(0x788, b'\x5a\x95DIFF'))
            else:
                original = port.write; calls = []
                def write(data):
                    if data.strip().replace(b' ', b'') == b'022122':
                        calls.append(True)
                        if len(calls) == 2: raise OSError('disconnected')
                    return original(data)
                port.write = write
        result, ports = self.run_case(port_mutator=mutate, wait_retry=lambda *args: True)
        self.assertFalse(result['ok']); self.assertEqual(result['error'], 'identity-mismatch')
        self.assertEqual(len(ports), 2); self.assertNotIn('2122', ports[1].ecu_payloads)
        self.assertEqual([s['value'] for s in result['samples']], [42, 7])
        self.assertTrue(result['transport']['closed'])

    def test_selector_uses_its_source_pair_and_only_three_identity_fields(self):
        for device in ('elm','vnci'):
            out,clients=self.run_case(device=device,selector=True)
            self.assertTrue(out['ok'],out);sent=clients.sent if device=='vnci' else clients[0].ecu_payloads
            self.assertEqual(sent,['1089','1A9F','1089','1A91','1A90','2122','2122'])
            self.assertEqual(out['transport']['qualification']['ecuId'],68)
            self.assertTrue(out['transport']['closed']);self.assertFalse(out['liveVerified'])
            if device=='vnci':self.assertEqual(clients.links,[('ISO_14230_3_on_ISO_15765_2',0x753,0x7BD)])

    def test_selector_unknown_version_and_invalid_vin_do_not_sample(self):
        for replies in ({'1A9F':bytes.fromhex('5A9F0200')},{'1A90':b'\x5a\x90'+b'I'*17}):
            for device in ('elm','vnci'):
                out,clients=self.run_case(device=device,selector=True,replies=replies)
                self.assertFalse(out['ok']);self.assertEqual(out['samples'],[]);self.assertTrue(out['transport']['closed'])
                sent=clients.sent if device=='vnci' else clients[0].ecu_payloads
                self.assertNotIn('2122',sent)
                if '1A9F' in replies:self.assertEqual(sent,['1089','1A9F'])

    def test_only_named_supported_variants_enter_the_source_loader(self):
        self.assertEqual(len(VERSIONS), 8)
        for changes in [{'generation': '983'}, {'ecuId': 1}, {'profileId': MODULE+'PDK_unprogrammiert'},
                        {'profileId': MODULE+'Getriebesteuerung_PDK_BaseSys'}, {'ecuId': True}]:
            with patch.object(read, 'verified_profile', side_effect=AssertionError('must not load')):
                result = read.read_manufacturer({**request(), 'profileId': PROFILE, 'ecuId': 2, **changes})
            self.assertEqual(result['error'], 'manufacturer-profile-unqualified')


if __name__ == '__main__':
    unittest.main()
