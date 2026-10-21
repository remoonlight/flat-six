"""Version discovery with explicitly synthetic ports; never hardware acceptance."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import patch
from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics import pdk_read,uds_source_read
from scripts.diagnostics.tests import test_manufacturer_pdk as kwp
from scripts.diagnostics.tests import test_manufacturer_uds_source as uds
from scripts.diagnostics.tests.test_manufacturer_read import request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_vnci import FakeNative,ID
from scripts.diagnostics.vnci import DpuClient
from scripts.diagnostics.elm import ElmError
import time
from scripts.diagnostics.session_simulator import SessionSimPort,ath1_prompt,isotp_ath1_lines


class IdentifyTests(unittest.TestCase):
    def mixed_media_case(self, hits=('uds',), *, identifying=True, failure=None, closed=True, wrong_profile=False, engine=False, bose=False):
        pids=({'kwp':pdk_read.SHARED_DME_FACTORY_PROFILE,'uds':next(iter(uds_source_read.DME_982_VERSIONS))} if engine else
            {'kwp':'9x1:Verstaerker:ASK','uds':'9x1:BV_SoundSysteUDS:EV_AMPMst16C1Gen2ASK_001'})
        if bose:pids['bose']=uds_source_read.BOSE_PROFILE
        ecu=1 if engine else 65
        profiles={pid:{'profileId':pid,'ecuIds':[ecu],'generation':None,'status':'definition-only'} for pid in pids.values()}
        calls=[]
        def cycles(profile,selected,factory,**options):
            calls.append((profile,selected,options));family=next(k for k,v in pids.items() if v==profile['profileId'])
            out=read._empty_result(read.SOURCE);out['simulation']=False;out['transport']['closed']=closed
            probe=identifying and len(calls)<=len(pids);ok=not probe or family in hits and failure is None
            out.update(ok=ok,error=None if ok else failure or 'NO DATA',identityOnly=identifying,
                identifiedProfileId=pids['kwp' if family=='uds' else 'uds'] if wrong_profile else profile['profileId'])
            return out
        req={**request(),'ecuId':ecu,'generation':'982' if engine else '981','profileId':pids['kwp'] if identifying else pids['uds'],
            'action':'manufacturer-identify' if identifying else 'manufacturer-read',
            'parameterIds':[] if identifying else ['a'*64]}
        if engine:req['sourceBound']=True
        with patch.object(read,'READ_PROFILES',{pid:read.READ_PROFILES[pid] for pid in profiles}),\
                patch.object(read,'verified_profile',side_effect=lambda req,root=None:profiles[req['profileId']]),\
                patch.object(read,'_client_cycles',side_effect=cycles),\
                patch('scripts.diagnostics.connection.open_selected_port',side_effect=AssertionError('unexpected hardware open')):
            out=read.read_manufacturer(req)
        return out,calls,pids

    def test_mixed_protocol_discovery_probes_only_identity_and_requalifies_unique_family(self):
        for engine,hit in ((engine,hit) for engine in (False,True) for hit in ('kwp','uds')):
            out,calls,pids=self.mixed_media_case((hit,),engine=engine)
            self.assertTrue(out['ok'],out);self.assertEqual(len(calls),3)
            self.assertEqual([c[1] for c in calls],[[],[],[]])
            self.assertEqual([c[0]['profileId'] for c in calls],[pids['kwp'],pids['uds'],pids[hit]])
            self.assertEqual(calls[-1][2]['identify_profiles'],[calls[-1][0]])
            self.assertEqual(out['sourceRouteDiscovery']['applicationProtocol'],hit)
            self.assertEqual(len({c[2]['batch_deadline'] for c in calls}),1)
            self.assertTrue(all(c[2]['budget_s']<=30 for c in calls))

    def test_mixed_protocol_zero_or_multiple_matches_never_publish_a_version(self):
        for engine,hits,reason in ((engine,hits,reason) for engine in (False,True) for hits,reason in
                [((),'manufacturer-source-route-unidentified'),(('kwp','uds'),'manufacturer-source-route-ambiguous')]):
            out,calls,_=self.mixed_media_case(hits,engine=engine)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],reason);self.assertEqual(len(calls),2)

    def test_mixed_protocol_head_cancel_or_close_failure_does_not_try_another_family(self):
        for engine,reason,closed in ((engine,reason,closed) for engine in (False,True) for reason,closed in
                [('adapter-identity-mismatch',True),('cancelled',True),('recovery-close-failed',False)]):
            out,calls,_=self.mixed_media_case((),failure=reason,closed=closed,engine=engine)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],reason);self.assertEqual(len(calls),1)

    def test_mixed_protocol_probe_cannot_claim_a_version_from_the_other_family(self):
        for engine in (False,True):
            out,calls,_=self.mixed_media_case(('kwp',),wrong_profile=True,engine=engine)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],'identity-mismatch');self.assertEqual(len(calls),1)

    def test_normal_media_read_keeps_the_selected_recipe_without_other_family_probes(self):
        for engine in (False,True):
            out,calls,pids=self.mixed_media_case(identifying=False,engine=engine)
            self.assertTrue(out['ok']);self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][0]['profileId'],pids['uds']);self.assertEqual(calls[0][1],['a'*64])
            self.assertIsNone(calls[0][2]['identify_profiles']);self.assertNotIn('sourceRouteDiscovery',out)

    def test_piwis_bose_command_plan_is_probed_separately_and_must_match_uniquely(self):
        out,calls,pids=self.mixed_media_case(('bose',),bose=True)
        self.assertTrue(out['ok'],out)
        self.assertEqual([c[0]['profileId'] for c in calls],[pids['kwp'],pids['uds'],pids['bose'],pids['bose']])
        self.assertTrue(all(c[1]==[] and len(c[2]['identify_profiles'])==1 for c in calls))
        self.assertEqual(out['sourceRouteDiscovery']['sourceBridge'],'piwis-bose002')
        self.assertEqual(len({c[2]['batch_deadline'] for c in calls}),1)
        out,calls,_=self.mixed_media_case(('uds','bose'),bose=True)
        self.assertFalse(out['ok']);self.assertEqual(out['error'],'manufacturer-source-route-ambiguous')
        self.assertEqual(len(calls),3)

    def identity_bind_case(self, change=None, *, measurement=False):
        p=kwp.model_profile();p['profileId']=next(iter(pdk_read.VERSIONS))
        actual=deepcopy(p);actual['profileId']=kwp.PROFILE
        base=kwp.model_contract();base['identityConstraints']={'dsnHex':'4000'}
        target=deepcopy(base);target['identityConstraints']={'dsnHex':'4210'}
        extra=deepcopy(target['identityRequests'][1]);extra.update(field='1A92',requestHex='1A92')
        extra['record'].update(pid=0x92,read_request_candidate_hex='1A92')
        target['identityRequests'].append(extra)
        if change:change(target)
        native=FakeNative();client=DpuClient(ID,native=native);deadline=time.monotonic()+10
        with patch('scripts.diagnostics.pdk_read.source_contract',side_effect=lambda q,ids:target if q['profileId']==actual['profileId'] else base):
            client.configure_manufacturer(p,['a'*64] if measurement else [],deadline,identity_only=not measurement)
            before=set(client.allowed);links=list(native.links)
            try:client.bind_manufacturer_identity(actual,deadline);error=None
            except (ValueError,ElmError) as e:error=str(e)
        return client,native,before,links,error

    def test_actual_source_identity_superset_is_bound_without_link_or_bus_io(self):
        client,native,before,links,error=self.identity_bind_case()
        self.assertIsNone(error);self.assertEqual(client.allowed,before|{'1A92'})
        self.assertEqual(native.links,links);self.assertEqual(native.sent,[])
        for hx in ('2122','22F100','2EF10001','2701','3101','3400'):
            with self.subTest(hx=hx),self.assertRaises(ElmError):client.request(hx,time.monotonic()+2)
        client.close_restore()

    def test_identity_binding_cannot_switch_route_unit_or_protocol(self):
        for key,value in [('txId',0x7E0),('rxId',0x7E8),('ecuId',9),('applicationProtocol','uds'),
                ('catalogProfileId','porsche-981-uds-source'),('sourceCommands',['1003'])]:
            client,native,before,links,error=self.identity_bind_case(lambda c:c.update({key:value}))
            self.assertIn('route-conflict',error);self.assertEqual(client.allowed,before)
            self.assertEqual(native.links,links);self.assertEqual(native.sent,[]);client.close_restore()

    def test_identity_binding_cannot_grant_measurements_or_writes(self):
        for hx in ('2122','22F10000','2EF10001','2701','3101','3400'):
            def changed(c):c['identityRequests'][-1]['requestHex']=hx
            client,native,before,links,error=self.identity_bind_case(changed)
            self.assertIn('request-invalid',error);self.assertEqual(client.allowed,before)
            self.assertEqual(native.sent,[]);client.close_restore()

    def test_identity_binding_is_only_for_an_identity_discovery_session(self):
        client,native,before,links,error=self.identity_bind_case(measurement=True)
        self.assertIn('bind-unqualified',error);self.assertEqual(client.allowed,before)
        self.assertEqual(native.sent,[]);client.close_restore()

    def test_closed_or_reconfigured_link_cannot_reuse_identity_binding(self):
        for close in (False,True):
            client,native,_,_,error=self.identity_bind_case();self.assertIsNone(error)
            if close:client.close_restore()
            else:client.configure_standard_engine(time.monotonic()+2)
            before=set(client.allowed);links=list(native.links)
            with self.assertRaises(ElmError):client.bind_manufacturer_identity(kwp.model_profile(),time.monotonic()+2)
            self.assertEqual(client.allowed,before);self.assertEqual(native.links,links);client.close_restore()

    def test_busy_or_expired_identity_binding_leaves_permissions_unchanged(self):
        for busy in (False,True):
            client,native,before,_,error=self.identity_bind_case();self.assertIsNone(error);before=set(client.allowed)
            if busy:client._lock.acquire()
            try:
                with self.assertRaises(ElmError):client.bind_manufacturer_identity(kwp.model_profile(),time.monotonic()+2 if busy else 0)
            finally:
                if busy:client._lock.release()
            self.assertEqual(client.allowed,before);self.assertEqual(native.sent,[]);client.close_restore()

    def test_source_mode_rejects_nonboolean_values_and_other_units(self):
        for value,ecu in [(False,1),(1,1),(True,5),(True,True)]:
            req={**request(),'sourceBound':value,'ecuId':ecu}
            with self.subTest(value=value,ecu=ecu),self.assertRaises(ValueError):read._validate(req)

    def source_links_case(self,hits=(0,),identifying=False,actual_second=False,probe_error=None):
        ids=['a'*64,'b'*64]
        pids=['9x1:BV_ActuaForStrucBorneSoundUDS:'+name for name in
              ('Actuator_For_Exterior_Noise_UDS','EV_SoundActuaGen2AU64X_003')]
        profiles={pid:{'profileId':pid,'ecuIds':[87],'generation':None,'status':'definition-only'} for pid in pids}
        calls=[]
        def cycles(profile,selected,factory,**options):
            calls.append((profile,selected,options))
            out=read._empty_result(read.SOURCE);out['simulation']=False;out['transport']['closed']=True
            if len(calls)<=2:
                link=profile['selectedSourceLink'];ok=link in hits and probe_error is None
                out.update(ok=ok,error=None if ok else probe_error or 'NO DATA',identityOnly=True,
                           identifiedProfileId=pids[int(actual_second)])
            else:out.update(ok=True,error=None,identityOnly=identifying,completedCycles=0 if identifying else 1)
            return out
        req={**request(),'ecuId':87,'profileId':pids[0],'cycleCount':1,
             'action':'manufacturer-identify' if identifying else 'manufacturer-read',
             'parameterIds':[] if identifying else ids}
        with patch.object(read,'verified_profile',side_effect=lambda req,root=None:profiles[req['profileId']]),\
                patch.object(read,'_client_cycles',side_effect=cycles),\
                patch('scripts.diagnostics.connection.open_selected_port',side_effect=AssertionError('mock must not create hardware')):
            out=read.read_manufacturer(req)
        return out,calls

    def test_unique_source_link_is_reopened_fresh_before_any_measurements(self):
        for hit in (0,1):
            out,calls=self.source_links_case(hits=(hit,))
            self.assertTrue(out['ok'],out);self.assertEqual(len(calls),3)
            self.assertEqual([call[1] for call in calls[:2]],[[],[]])
            self.assertTrue(all(c[2]['identify_profiles'] for c in calls[:2]))
            self.assertEqual(calls[-1][0]['selectedSourceLink'],hit)
            self.assertEqual(calls[-1][1],['a'*64,'b'*64])
            self.assertIsNone(calls[-1][2]['identify_profiles'])
            self.assertEqual(len({c[2]['batch_deadline'] for c in calls}),1,
                'source probes and the final fresh read share one absolute ten-minute deadline')

    def test_zero_or_multiple_matching_source_links_never_read_values(self):
        for hits,error in [((),'manufacturer-source-route-unidentified'),((0,1),'manufacturer-source-route-ambiguous')]:
            out,calls=self.source_links_case(hits=hits)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],error)
            self.assertEqual(len(calls),2);self.assertEqual([c[1] for c in calls],[[],[]])

    def test_source_link_probe_does_not_retry_adapter_fault_or_cancel(self):
        for error in ('adapter-identity-mismatch','cancelled','recovery-close-failed'):
            out,calls=self.source_links_case(probe_error=error)
            self.assertFalse(out['ok']);self.assertEqual(out['error'],error);self.assertEqual(len(calls),1)

    def test_identified_source_link_keeps_discovery_parameter_free(self):
        out,calls=self.source_links_case(hits=(1,),identifying=True,actual_second=True)
        self.assertTrue(out['ok'],out);self.assertEqual([c[1] for c in calls],[[],[],[]])
        self.assertEqual(calls[-1][0]['profileId'].rsplit(':',1)[1],'EV_SoundActuaGen2AU64X_003')
        self.assertEqual(calls[-1][0]['selectedSourceLink'],1)

    def test_source_link_actual_version_cannot_replace_a_normal_read_selection(self):
        out,calls=self.source_links_case(hits=(1,),actual_second=True)
        self.assertFalse(out['ok']);self.assertEqual(out['error'],'identity-mismatch')
        self.assertEqual(len(calls),2);self.assertEqual([c[1] for c in calls],[[],[]])

    def test_shared_definition_requires_an_explicit_validated_target(self):
        from scripts.diagnostics.manufacturer_acquisition import matched_read_profile, read_profile_targets
        pid='9x1:BV_ContrUnitLaneChangAssis2UDS:EV_RearRadarS1BoschPO416_001'
        profile={'profileId':pid,'generation':None,'ecuIds':[95,96],'status':'definition-only'}
        self.assertEqual(read_profile_targets(pid),(95,96))
        for target in (95,96):
            chosen={**profile,'selectedEcuId':target}
            self.assertEqual(matched_read_profile(chosen)[0],target)
            self.assertEqual(chosen['ecuIds'],[95,96])
        for target in (None,94,True,'95'):
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,'manufacturer-profile-unqualified'):
                matched_read_profile({**profile,'selectedEcuId':target})
        with self.assertRaisesRegex(ValueError,'manufacturer-profile-unqualified'):
            matched_read_profile(profile)

    def test_shared_profile_cannot_override_a_single_target_recipe(self):
        from scripts.diagnostics.manufacturer_acquisition import matched_read_profile
        profile={'profileId':uds.PROFILES[5],'generation':None,'ecuIds':[5],'status':'definition-only','selectedEcuId':96}
        with self.assertRaisesRegex(ValueError,'manufacturer-profile-unqualified'):
            matched_read_profile(profile)

    def test_shared_target_validation_happens_before_adapter_creation(self):
        pid='9x1:BV_ContrUnitLaneChangAssis2UDS:EV_RearRadarS1BoschPO416_001'
        with patch('scripts.diagnostics.connection.open_selected_port',side_effect=AssertionError('port must stay closed')):
            for target in (94,True,'95'):
                result=read.read_manufacturer({**request(),'profileId':pid,'ecuId':target})
                self.assertFalse(result['ok'])
                self.assertEqual(result['error'],'manufacturer-profile-unqualified')
                self.assertFalse(result['transport']['openAttempted'])

    def run_case(self,kind='kwp',device='elm',overrides=None,conflict=None,identifying=True,check_only=False,cancelled=False):
        if kind=='kwp':
            ecu=2;pid1=kwp.MODULE+'PDK';pid2=kwp.PROFILE
            p1=kwp.model_profile();p1['profileId']=pid1
            c1=kwp.model_contract();c1['identityConstraints']={'dsnHex':'4000'}
            c2=kwp.model_contract();replies=kwp.model_responses();module='pdk_read';measurement='2122'
        elif kind=='rdk-selectors':
            ecu=55;pid1='9x1:RDK:Reifendruckkontrolle_A2_4';pid2='9x1:RDK3:RDK3_005'
            p1={'profileId':pid1,'generation':None,'ecuIds':[55],'status':'definition-only','parameters':[field('a','22F102'),field('b','22F102',1)],'identity':[],'checklist':[]}
            c1=uds.model_contract(55);c1['sourceCommands']=['3E00','22F1DF','22F1A2','22F19E','1003']
            dsn=deepcopy(next(x['record'] for x in c1['identityRequests'] if x['requestHex']=='22F1A2')['formula'])
            selector=dict(leftWordSigned=0x22,rightWordSigned=-3678,byteOffsetSigned=2,bitOffsetByte=0,formulaId=0x80000002)
            c1['versionSelectors']=[dict(requestHex='22F1A2',selector=selector,formula=dsn)]
            c1['identityConstraints']={'versionRules':['200001']};c2=deepcopy(c1)
            c2['versionSelectors']=[dict(requestHex='22F1A2',selector={**selector,'formulaId':0x8000000A},formula={'text':dsn['text'].replace('BitLength=48','BitLength=24')}),
                dict(requestHex='22F19E',selector={**selector,'rightWordSigned':-3682,'formulaId':0x8000002A},formula={'text':'IDENTICAL:MinMax,MinLength=6,BaseDataType=A_ASCIISTRING,MaxLength=26,BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'})]
            c2['identityConstraints']={'versionRules':['005#EV_RDKBERU30']}
            c2['identityRequests']=[r for r in c2['identityRequests'] if r['requestHex'] not in ('22F1A2','22F1A3')]
            c2['versionIdentityRequests']=['22F1A2','22F19E']
            replies=uds.model_responses(55);replies.update({'22F1A2':b'\x62\xF1\xA2'+b'005','22F19E':b'\x62\xF1\x9E'+b'EV_RDKBERU30\0','22F1DF':bytes.fromhex('62F1DF00')})
            module='uds_source_read';measurement='22F102'
        else:
            ecu=5;pid1=uds.PROFILES[5];pid2='9x1:PSM:PSM_a2'
            p1={'profileId':pid1,'generation':None,'ecuIds':[5],'status':'definition-only','parameters':[field('a','22F102'),field('b','22F102',1)],'identity':[],'checklist':[]}
            c1=uds.model_contract(5);c2=deepcopy(c1);c2['identityConstraints']={'dsnText':'002000'}
            replies=uds.model_responses(5);replies['22F1A2']=bytes.fromhex('62F1A2')+b'002000';module='uds_source_read';measurement='22F102'
        p2=deepcopy(p1);p2['profileId']=pid2
        if conflict:conflict(c2)
        profiles={pid1:p1,pid2:p2};contracts={pid1:c1,pid2:c2};replies.update(overrides or {})
        ports=[];native=FakeNative();native.make_events=lambda payload,cop:FakeNative.reply(replies[payload.hex().upper()],cop)
        def opened(*args,**kwargs):
            port=SessionSimPort('porsche-981-2014-dme');port.ecu_map={hx:ath1_prompt(isotp_ath1_lines(c1['rxId'],pdu)) for hx,pdu in replies.items()};ports.append(port);return port,'synthetic'
        req={**request(),'action':'manufacturer-identify' if identifying else 'manufacturer-read','ecuId':ecu,'profileId':pid1,
             'parameterIds':[] if identifying else ['a'*64,'b'*64],'cycleCount':1}
        if device=='vnci':req['deviceId']=ID
        cancel=threading.Event()
        if cancelled:cancel.set()
        with patch.object(read,'READ_PROFILES',{pid:read.READ_PROFILES[pid] for pid in profiles}),\
                patch.object(read,'verified_profile',side_effect=lambda req,root=None:profiles[req['profileId']]),\
                patch('scripts.diagnostics.'+module+'.source_contract',side_effect=lambda p,ids:contracts[p['profileId']]),\
                patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened) as factory,\
                patch('scripts.diagnostics.vnci.NativeDpu',return_value=native) as native_factory:
            out=read.read_manufacturer(req,check_only=check_only,cancel_event=cancel)
            opened_count=factory.call_count+native_factory.call_count
        sent=native.sent if device=='vnci' else ports[0].ecu_payloads if ports else []
        return out,sent,pid2,measurement,opened_count

    def test_kwp_discovers_nondefault_version_on_both_transports_without_measurements(self):
        for device in ('elm','vnci'):
            out,sent,pid,measurement,_=self.run_case(device=device)
            self.assertTrue(out['ok'],out);self.assertEqual(out['identifiedProfileId'],pid)
            self.assertEqual(sent,['1089','1A9F','1089','1A95','1A9A','1A87','1A91','1A90']);self.assertNotIn(measurement,sent)
            self.assertEqual(out['samples'],[]);self.assertEqual(out['transactions'],[]);self.assertEqual(out['readContract']['groups'],[])
            self.assertEqual(out['completedCycles'],0);self.assertEqual(out['requestCount'],0);self.assertTrue(out['transport']['closed'])
            self.assertTrue(out['transport']['qualification']['freshRead']);self.assertFalse(out['liveVerified'])

    def test_selector_discovery_uses_actual_version_fields_on_both_transports(self):
        for device in ('elm','vnci'):
            out,sent,pid,measurement,_=self.run_case(kind='rdk-selectors',device=device)
            self.assertTrue(out['ok'],out);self.assertEqual(out['identifiedProfileId'],pid)
            self.assertNotIn('22F1A3',sent,'an identity request absent from the actual version must not be borrowed')
            self.assertNotIn(measurement,sent);self.assertEqual(out['samples'],[])

    def test_normal_read_does_not_borrow_another_versions_selector_format(self):
        for device in ('elm','vnci'):
            out,sent,_pid,measurement,_=self.run_case(kind='rdk-selectors',device=device,identifying=False)
            self.assertFalse(out['ok']);self.assertNotIn(measurement,sent)
            self.assertEqual(out['samples'],[]);self.assertEqual(out['transactions'],[])
            self.assertEqual(out['completedCycles'],0);self.assertEqual(out['requestCount'],0);self.assertTrue(out['transport']['closed'])

    def test_uds_discovers_nondefault_version_on_both_transports_without_measurements(self):
        for device in ('elm','vnci'):
            out,sent,pid,measurement,_=self.run_case('uds',device)
            self.assertTrue(out['ok'],out);self.assertEqual(out['identifiedProfileId'],pid);self.assertNotIn(measurement,sent)
            self.assertEqual(sent,['3E00','22F1A2','22F1DF','1003','22F189','22F1A3','22F191','22F187','22F190'])
            self.assertEqual(out['samples'],[]);self.assertEqual(out['readContract']['groups'],[]);self.assertTrue(out['transport']['closed'])

    def test_unknown_version_or_invalid_identity_does_not_publish_a_profile(self):
        for kind,overrides in [('kwp',{'1A9F':bytes.fromhex('5A9FFFFF')}),('uds',{'22F1A2':bytes.fromhex('62F1A2')+b'BAD000'}),
                               ('kwp',{'1A90':bytes.fromhex('5A90')+b'I'*17}),('uds',{'22F190':bytes.fromhex('62F190')+b'I'*17})]:
            for device in ('elm','vnci'):
                out,sent,_,measurement,_=self.run_case(kind,device,overrides)
                self.assertFalse(out['ok']);self.assertNotIn('identifiedProfileId',out);self.assertEqual(out['samples'],[])
                self.assertNotIn(measurement,sent);self.assertTrue(out['transport']['closed'])

    def test_incompatible_candidate_recipe_is_rejected_before_factory(self):
        for conflict in (lambda c:c.update(txId=0x700),lambda c:c.update(session={'requestHex':'1001'}),lambda c:c['identityRequests'].pop()):
            out,_,_,_,opened=self.run_case(conflict=conflict)
            self.assertEqual(out['error'],'manufacturer-identify-route-conflict');self.assertEqual(opened,0)
            self.assertIs(out['transport']['openAttempted'],False)

    def test_normal_acquisition_cannot_switch_version_or_borrow_other_parameters(self):
        for kind in ('kwp','uds'):
            for device in ('elm','vnci'):
                out,sent,_,measurement,_=self.run_case(kind,device,identifying=False)
                self.assertEqual(out['error'],'identity-mismatch');self.assertNotIn('identifiedProfileId',out);self.assertNotIn(measurement,sent)

    def test_malformed_discovery_requests_never_load_source_or_open_device(self):
        base={**request(),'action':'manufacturer-identify','ecuId':2,'profileId':kwp.MODULE+'PDK','parameterIds':[],'cycleCount':1}
        for delta in ({'generation':'983'},{'ecuId':True},{'ecuId':999},{'profileId':kwp.PROFILE},{'parameterIds':['a'*64]},
                      {'cycleCount':None},{'requestHex':'22F1A2'},{'confirmedReadOnly':False}):
            with patch.object(read,'verified_profile',side_effect=AssertionError('must not load')):
                out=read.read_manufacturer({**base,**delta})
            self.assertFalse(out['ok']);self.assertIs(out['transport']['openAttempted'],False)

    def test_check_only_and_cancellation_do_not_claim_fresh_vehicle_identification(self):
        for options in ({'check_only':True},{'cancelled':True}):
            out,_,_,_,opened=self.run_case(**options);self.assertEqual(opened,0);self.assertNotIn('identifiedProfileId',out)
            self.assertFalse(out['executionEnabled']);self.assertIs(out['transport']['openAttempted'],False)
            if options.get('check_only'):self.assertTrue(out['checkOnly']);self.assertEqual(out['readContract']['groups'],[])
            else:self.assertEqual(out['error'],'cancelled')


if __name__=='__main__':unittest.main()
