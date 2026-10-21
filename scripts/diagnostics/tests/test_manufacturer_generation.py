"""Typed 981/982 read scopes; explicit fixture ports, no physical device IO."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read,pdk_read as kwp,uds_source_read as uds
from scripts.diagnostics.manufacturer_acquisition import READ_PROFILES,read_profile_generations
from scripts.diagnostics.manufacturer_transport import _client_cycles
from scripts.diagnostics.tests.test_manufacturer_read import bundle,request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.realtime_preparation import handle_ready,dump,sha
from scripts.diagnostics.tests.test_manufacturer_pdk import model_profile,model_contract,model_responses
from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract as uds_contract,model_responses as uds_responses,PROFILES
from scripts.diagnostics.session_simulator import SessionSimPort,ath1_prompt,isotp_ath1_lines
from scripts.diagnostics.tests.test_vnci import FakeNative,ID


class GenerationTests(unittest.TestCase):
    def test_profile_scopes_do_not_infer_a_dme_generation_from_a_shared_container(self):
        self.assertEqual(len(uds.DME_UDS_VERSIONS),9)
        for pid in uds.DME_UDS_VERSIONS:self.assertEqual(read_profile_generations(pid),('982',))
        self.assertEqual(len(uds.DME_ASAM_VERSIONS),11)
        self.assertEqual(len(uds.DME_982_VERSIONS),20)
        self.assertEqual(len(uds.DME_SOURCE_UDS_VERSIONS),21)
        self.assertEqual(read_profile_generations(uds.DME_SHARED_ASAM_PROFILE),('981','982'))
        for pid in uds.DME_ASAM_VERSIONS:self.assertEqual(read_profile_generations(pid),('982',))
        for pid in kwp.DME_VERSIONS:
            expected=('981','982') if pid in (kwp.SHARED_DME_FACTORY_PROFILE,kwp.SHARED_DME_UNPROGRAMMED_PROFILE) else ('981',)
            self.assertEqual(read_profile_generations(pid),expected)
        self.assertEqual(kwp.SOURCE_GENERATIONS[kwp.SHARED_DME_FACTORY_PROFILE],None)
        self.assertEqual(kwp.DME_VERSIONS[kwp.SHARED_DME_FACTORY_PROFILE],'C000')
        self.assertIsNone(kwp.SOURCE_GENERATIONS[kwp.SHARED_DME_UNPROGRAMMED_PROFILE])
        self.assertEqual(kwp.DME_VERSIONS[kwp.SHARED_DME_UNPROGRAMMED_PROFILE],'0000')
        for key in (None,[],{},True,'unknown'):self.assertEqual(read_profile_generations(key),())
        self.assertEqual(read_profile_generations(PROFILES[5]),('981','982'))

    def test_wrong_dme_or_unknown_scope_never_loads_a_profile_or_opens_a_device(self):
        dme982=next(iter(uds.DME_UDS_VERSIONS))
        changes=[dict(generation='982'),dict(generation='983'),dict(generation=True),
            dict(generation='981',profileId=dme982),dict(generation='982',profileId=read.GATEWAY_PROFILE,ecuId=9)]
        for delta in changes:
            with patch.object(read,'verified_profile') as loader,patch('scripts.diagnostics.connection.open_selected_port') as opened,patch('scripts.diagnostics.vnci.DpuClient') as native:
                out=read.read_manufacturer({**request(),**delta})
                self.assertEqual(out['error'],'manufacturer-profile-unqualified');loader.assert_not_called();opened.assert_not_called();native.assert_not_called()

    def run_shared(self,kind,transport,vin='WP0ZZZ98ZJS223456'):
        if kind=='kwp':p=model_profile();c=model_contract();responses=model_responses();module='pdk_read'
        else:
            p=dict(profileId=PROFILES[5],generation=None,ecuIds=[5],status='definition-only',parameters=[field('a','22F102'),field('b','22F102',1)],identity=[],checklist=[])
            c=uds_contract(5);responses=uds_responses(5);module='uds_source_read'
        hx='1A90' if kind=='kwp' else '22F190'
        responses[hx]=bytes([int(hx[:2],16)+0x40])+bytes.fromhex(hx[2:])+vin.encode('ascii')
        ports=[];native=FakeNative();native.make_events=lambda payload,cop:FakeNative.reply(responses[payload.hex().upper()],cop)
        def opened(*a,**k):
            port=SessionSimPort('porsche-981-2014-dme');port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(c['rxId'],pdu)) for hx,pdu in responses.items()};ports.append(port);return port,'synthetic'
        req=dict(request(),generation='982',profileId=p['profileId'],ecuId=p['ecuIds'][0],deviceId=ID if transport=='vnci' else 'bt:001122334455')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle(root,p)
            with patch(f'scripts.diagnostics.{module}.source_contract',return_value=c),patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened),patch('scripts.diagnostics.vnci.NativeDpu',return_value=native):
                return read.read_manufacturer(req,root=root)

    def test_982_shared_uds_and_kwp_sessions_keep_scope_through_fresh_identity_and_sampling(self):
        for kind in ('uds','kwp'):
            for transport in ('elm','vnci'):
                out=self.run_shared(kind,transport)
                self.assertTrue(out['ok'],out.get('error'));self.assertEqual([s['value'] for s in out['samples']],[42,7])
                self.assertEqual(out['readContract']['generation'],'982');self.assertEqual(out['transport']['qualification']['generation'],'982')
                self.assertTrue(out['transport']['closed']);self.assertFalse(out['liveVerified'])
                for vin in ('WP0ZZZ98ZES123456','WP0ZZZ98ZJS123456','WP0ZZZ99ZJS223456'):
                    rejected=self.run_shared(kind,transport,vin)
                    self.assertFalse(rejected['ok']);self.assertEqual(rejected['error'],'vehicle-identity-mismatch')
                    self.assertEqual(rejected['samples'],[]);self.assertEqual(rejected['completedCycles'],0)
                    self.assertTrue(rejected['transport']['closed'])

    def test_identity_discovery_cannot_mix_generation_contexts_before_opening_a_client(self):
        p=dict(profileId=PROFILES[5],generation=None,ecuIds=[5],status='definition-only',parameters=[],identity=[],requestedGeneration='981')
        with patch('scripts.diagnostics.uds_source_read.source_contract',return_value=uds_contract(5)),patch('scripts.diagnostics.elm.ElmClient') as factory:
            out=_client_cycles(p,[],factory,physical=True,identify_profiles=[p,{**p,'requestedGeneration':'982'}])
        self.assertEqual(out['error'],'manufacturer-identify-route-conflict');factory.assert_not_called();self.assertEqual(out['samples'],[])
        for probe in (True,1,None):
            with patch('scripts.diagnostics.uds_source_read.source_contract',return_value=uds_contract(5)),patch('scripts.diagnostics.elm.ElmClient') as factory:
                out=_client_cycles(p,[],factory,physical=True,identify_profiles=[p],vehicle_probe=probe)
            self.assertEqual(out['error'],'manufacturer-vehicle-probe-unqualified');factory.assert_not_called()

    def test_source_lookup_uses_the_requested_generation_row(self):
        index=dict(units=[dict(generation=g,ecuId=2,variants=[dict(profileId='same',file=g+'.json')]) for g in ('981','982')])
        def document(root,manifest,name):return index if name=='all-units.json' else dict(selectedSource=name)
        with patch.object(kwp,'_catalog',return_value=('source',{})) as catalog,patch.object(kwp,'_document',side_effect=document):
            self.assertEqual(kwp._source_profile('same',2,generation='982'),dict(selectedSource='982.json'))
            catalog.reset_mock()
            with self.assertRaises(ValueError):kwp._source_profile('same',2,generation='983')
            catalog.assert_not_called()


class ReadyGenerationTests(unittest.TestCase):
    SHARED='9x1:BV_ContrUnitLaneChangAssis2UDS:EV_RearRadarS1BoschPO416_001'

    def directory(self,root,generation=None):
        p=dict(profileId=self.SHARED,name='静态版本',module='source-module',generation=generation,ecuIds=[95,96],status='identity-matched',
            parameters=[{**field('a','22F102'),'status':'capture-matched','decodedSampleCount':5,'replay':[{'pointCount':5}]},field('b','22F102',1)],
            identity=[],identityReplay=[{'source':'MODELED_981_HISTORY'}],checklist=[])
        manifest=bundle(root,p);index=__import__('json').loads((root/'index.json').read_bytes());index['profiles'][0].update(name=p['name'],status=p['status'],parameterCount=2,replayParameterCount=1)
        index.update(units=[dict(ecuId=e,systemId='swa-'+str(e),label='单元中文名称',variants=[]) for e in (95,96)],counts=dict(units=2,profiles=1,parameters=2))
        dump(root/'index.json',index);manifest['outputs'][0]['sha256']=sha(root/'index.json');dump(root/'manifest.json',manifest)
        return p

    def test_982_directory_has_full_source_rows_and_unique_counts_without_981_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root)
            variant=dict(profileId=self.SHARED,name='raw-name',status='catalog-unqualified',parameterCount=99,replayParameterCount=3,identityCount=0,identityDecoderCount=0)
            unknown=dict(variant,profileId='982-catalogue-only',parameterCount=3)
            catalogue=dict(ok=True,present=True,units=[dict(ecuId=95,systemId='swa-95',label='raw',variants=[variant,unknown]),dict(ecuId=96,systemId='swa-96',label='raw',variants=[variant])])
            with patch('scripts.diagnostics.realtime_preparation.handle_catalog',return_value=catalogue):out=handle_ready(dict(action='ready-units',generation='982'),root=root)
            self.assertTrue(out['ok'],out);self.assertEqual(out['counts'],dict(units=2,profiles=2,parameters=5))
            for unit in out['units']:
                self.assertEqual(unit['label'],'单元中文名称')
                for v in unit['variants']:self.assertEqual(v['status'],'definition-only');self.assertEqual(v['replayParameterCount'],0)
            self.assertEqual(out['units'][0]['variants'][0]['name'],'静态版本')

    def test_982_parameter_view_clears_historical_evidence_without_modifying_the_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root);file=next((root/'profiles').glob('*.json'));before=file.read_bytes()
            req=dict(action='ready-parameters',generation='982',ecuId=95,profileId=self.SHARED)
            out=handle_ready(req,root=root);self.assertTrue(out['ok'],out);self.assertEqual(out['identityReplay'],[])
            self.assertTrue(all(p['decodedSampleCount']==0 and p['status']=='definition-only' for p in out['items']))
            old=handle_ready({**req,'generation':'981'},root=root);self.assertEqual(old['items'][0]['decodedSampleCount'],5);self.assertTrue(old['identityReplay']);self.assertEqual(file.read_bytes(),before)

    def test_plan_keeps_generation_and_the_selected_shared_unit_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root);contract={**uds_contract(5),'ecuId':95,'txId':0x74E,'rxId':0x7B8}
            with patch('scripts.diagnostics.uds_source_read.source_contract',return_value=contract) as source:
                out=handle_ready(dict(action='ready-plan',generation='982',ecuId=95,profileId=self.SHARED,parameterIds=['a'*64]),root=root)
            self.assertTrue(out['ok'],out);self.assertEqual(out['plan']['preparedTransport']['generation'],'982');self.assertEqual(out['plan']['preparedTransport']['ecuId'],95)
            self.assertEqual(source.call_args.args[0]['selectedEcuId'],95)

    def test_982_never_relabels_981_replay_or_rehearsal_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root)
            for action in ('ready-replay','ready-acquire'):
                with patch('scripts.diagnostics.manufacturer_transport.transport_rehearsal') as runner:
                    out=handle_ready(dict(action=action,generation='982',ecuId=95,profileId=self.SHARED,parameterIds=['a'*64]),root=root)
                self.assertFalse(out['ok']);self.assertEqual(out['error'],'historical-scope-mismatch');runner.assert_not_called()

    def test_catalogue_only_982_parameters_remain_viewable_without_an_executable_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root);req=dict(action='ready-parameters',generation='982',ecuId=95,profileId='982-catalogue-only',search='name',limit=40)
            with patch('scripts.diagnostics.realtime_preparation.handle_catalog',return_value=dict(ok=True,items=[],total=0)) as catalogue:
                out=handle_ready(req,root=root)
            self.assertTrue(out['ok']);catalogue.assert_called_once_with({**req,'action':'catalog-parameters'})
            old=handle_ready({**req,'generation':'981'},root=root);self.assertFalse(old['ok']);self.assertEqual(old['error'],'unknown-profile')

    def test_explicit_981_definition_cannot_be_opened_as_982(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root,generation='981')
            out=handle_ready(dict(action='ready-parameters',generation='982',ecuId=95,profileId=self.SHARED),root=root)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],'wrong-generation')

    def test_missing_982_source_catalogue_does_not_affect_the_existing_981_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.directory(root)
            with patch('scripts.diagnostics.realtime_preparation.handle_catalog',return_value=dict(ok=True,present=False,units=[])):
                out=handle_ready(dict(action='ready-units',generation='982'),root=root)
                old=handle_ready(dict(action='ready-units',generation='981'),root=root)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],'preparation-missing');self.assertTrue(old['ok']);self.assertEqual(old['counts']['profiles'],1)


if __name__=='__main__':unittest.main()
