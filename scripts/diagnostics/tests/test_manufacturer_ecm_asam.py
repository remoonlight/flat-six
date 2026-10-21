"""ASAM engine source selectors and repeated reads using modeled transports."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read, uds_source_read as uds
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_read import bundle, request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract, model_responses
from scripts.diagnostics.tests.test_vnci import FakeNative, ID

PROFILE=next(iter(uds.DME_ASAM_VERSIONS))
ASAM='EV_ECM40FSI011982906033AM'
NAME='IDENTICAL:MinMax,MinLength=3,BaseDataType=A_ASCIISTRING,MaxLength=25,BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'
VERSION='IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=24,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'


def contract():
    c=model_contract(5)
    c.update(ecuId=1, txId=0x7E0, rxId=0x7E8,
        sourceCommands=['3E00','22F19E','22F1A2','1003'],
        identityConstraints={'versionRules':[ASAM+'#001']},
        versionIdentityRequests=['22F19E','22F1A2'])
    c['identityRequests']=[r for r in c['identityRequests'] if r['requestHex']!='22F1A2']
    c['versionSelectors']=[dict(requestHex=hx,selector=dict(leftWordSigned=0x22,
        rightWordSigned=int(hx[2:],16)-65536,byteOffsetSigned=2,bitOffsetByte=0,formulaId=fid),formula={'text':formula})
        for hx,fid,formula in [('22F19E',0xF000000D,NAME),('22F1A2',0xF0000009,VERSION)]]
    return c


class AsamEngineTests(unittest.TestCase):
    def case(self, *, native=False, overrides=None, mutate=None, retry=None):
        p=dict(profileId=PROFILE,generation=None,ecuIds=[1],status='definition-only',
            parameters=[field('a','22F102'),field('b','22F102',1)],identity=[],checklist=[])
        responses=model_responses(5)
        responses.update({'22F19E':b'\x62\xf1\x9e'+ASAM.encode()+b'\0','22F1A2':b'\x62\xf1\xa2' + b'001',
                          '22F190':b'\x62\xf1\x90WP0ZZZ98ZJS223456'})
        responses.update(overrides or {})
        device=FakeNative();ports=[]
        device.make_events=lambda body,cop:FakeNative.reply(responses[body.hex().upper()],cop)
        def opened(*args,**kwargs):
            port=SessionSimPort('porsche-981-2014-dme')
            port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(0x7E8,body)) for hx,body in responses.items()}
            if mutate:mutate(port,len(ports))
            ports.append(port);return port,'modeled-port'
        req={**request(), 'generation':'982','ecuId':1,'profileId':PROFILE,'cycleCount':2,'sourceBound':True}
        if native:req['deviceId']=ID
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle(root,p)
            with patch.object(uds,'source_contract',return_value=contract()), \
                    patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened), \
                    patch('scripts.diagnostics.vnci.NativeDpu',return_value=device):
                out=read.read_manufacturer(req,root=root,wait_retry=retry)
        return out, device if native else ports

    def test_source_asam_selectors_allow_two_read_cycles_on_both_transports(self):
        for native in (False,True):
            out,clients=self.case(native=native)
            self.assertTrue(out['ok'],out)
            self.assertEqual([s['value'] for s in out['samples']],[42,7,42,7])
            self.assertEqual(out['completedCycles'],2)
            self.assertTrue(out['transport']['closed'])
            sent=clients.sent if native else clients[0].ecu_payloads
            self.assertEqual(sent[:4],['3E00','22F19E','22F1A2','1003'])
            self.assertEqual(sent.count('22F102'),2)
            self.assertFalse(out['liveVerified'])

    def test_wrong_asam_name_or_version_stops_before_session_and_values(self):
        for changes in [{'22F19E':b'\x62\xf1\x9eEV_OTHER\0'}, {'22F1A2':b'\x62\xf1\xa2' + b'999'}]:
            for native in (False,True):
                out,clients=self.case(native=native,overrides=changes)
                self.assertEqual(out['error'],'identity-mismatch')
                sent=clients.sent if native else clients[0].ecu_payloads
                self.assertNotIn('1003',sent);self.assertNotIn('22F102',sent)
                self.assertEqual(out['samples'],[]);self.assertTrue(out['transport']['closed'])

    def test_bad_echo_or_vin_never_publishes_values(self):
        for changes in [{'22F19E':b'\x62\xf1\x9f'+ASAM.encode()+b'\0'}, {'22F190':b'\x62\xf1\x90INVALID'}]:
            out,ports=self.case(overrides=changes)
            self.assertFalse(out['ok']);self.assertEqual(out['samples'],[])
            self.assertNotIn('22F102',ports[0].ecu_payloads);self.assertTrue(out['transport']['closed'])

    def test_reconnect_keeps_first_cycle_and_rechecks_full_identity(self):
        for changed in (False,True):
            def mutate(port,index):
                if index:
                    if changed:port.ecu_map['22F189']=ath1_prompt(isotp_ath1_lines(0x7E8,b'\x62\xf1\x89DIFF'))
                else:
                    write=port.write;calls=[]
                    def interrupted(data):
                        if data.strip().replace(b' ',b'')==b'0322F102':
                            calls.append(True)
                            if len(calls)==2:raise OSError('disconnected')
                        return write(data)
                    port.write=interrupted
            out,ports=self.case(mutate=mutate,retry=lambda *args:True)
            self.assertEqual(len(ports),2);self.assertTrue(out['transport']['closed'])
            if changed:
                self.assertEqual(out['error'],'identity-mismatch')
                self.assertEqual([s['value'] for s in out['samples']],[42,7])
                self.assertNotIn('22F102',ports[1].ecu_payloads)
            else:self.assertTrue(out['ok'],out);self.assertEqual([s['value'] for s in out['samples']],[42,7,42,7])

    def test_discovery_waits_for_both_source_fields_and_rejects_ambiguity(self):
        c=contract();legacy=deepcopy(c)
        legacy['versionSelectors']=[dict(requestHex='22F1A2',selector=dict(leftWordSigned=0x22,
            rightWordSigned=-3678,byteOffsetSigned=2,bitOffsetByte=0,formulaId=0x80000007),
            formula={'text':VERSION.replace('BitLength=24','BitLength=32')})]
        legacy['identityConstraints']={'versionRules':['P200']}
        candidates=[({'profileId':'legacy'},legacy),({'profileId':PROFILE},c)]
        responses={'22F1A2':(b'\x62\xf1\xa2' + b'001').hex()}
        self.assertIsNone(uds.resolve_version_responses(candidates,responses))
        responses['22F19E']=(b'\x62\xf1\x9e'+ASAM.encode()+b'\0').hex()
        self.assertEqual(uds.resolve_version_responses(candidates,responses)[0]['profileId'],PROFILE)
        with self.assertRaisesRegex(ElmError,'identity-mismatch'):
            uds.resolve_version_responses([*candidates,({'profileId':'duplicate'},c)],responses)

    def test_wrong_generation_is_rejected_before_any_source_or_device_access(self):
        with patch.object(read,'verified_profile') as source, patch('scripts.diagnostics.connection.open_selected_port') as device:
            out=read.read_manufacturer({**request(),'generation':'981','profileId':PROFILE})
        self.assertEqual(out['error'],'manufacturer-profile-unqualified');source.assert_not_called();device.assert_not_called()
