from copy import deepcopy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import time
import hashlib

from scripts.diagnostics.uds_coding_source import complete_uds_scope, coding_field_read_bindings, SCOPES
from scripts.diagnostics.tests.test_body_coding_scope import model


class UdsCodingScopes(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads(SCOPES.read_text())['profiles']
        self.reviewed = [r for r in self.rows if r['module'] not in ('BCM_vorne','BCM_hinten')]

    def test_all_reviewed_modules_and_generations_keep_complete_factory_sizes(self):
        self.assertEqual(len(self.rows),154)
        self.assertEqual(len(self.reviewed),142)
        self.assertEqual(len({r['profileId'] for r in self.rows}),154)
        for row in self.reviewed:
            p,s,c,g=model(row)
            for generation in row['generations']:
                scope,groups=complete_uds_scope(p,s,{**c,'generation':generation},g)
                self.assertEqual(scope,row['completeScopeSource'])
                self.assertEqual([g['expectedTotalBytes'] for g in groups],
                                 [g['bytes'] for g in scope['requestLengths']])

    def test_engine_grouped_layouts_are_exact_and_982_only(self):
        rows = [r for r in self.rows if r['module'] in ('BV_ECM_UDS','BV_EnginContrModul1UDS')]
        self.assertEqual(len(rows),20)
        for row in rows:
            p,s,c,g=model(row)
            self.assertEqual(row['generations'],['982'])
            self.assertGreater(sum(len(r.get('records') or [r.get('record')]) for r in c['identityRequests']),len(c['identityRequests']))
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_uds_scope(p,s,{**c,'generation':'981'},g)
            grouped=next(i for i,r in enumerate(c['identityRequests']) if r.get('records'))
            for change in (lambda a:a[grouped]['records'].pop(),
                           lambda a:a[grouped]['records'][1].update(byteOffset=999),
                           lambda a:a[grouped]['records'][1].update(at=0),
                           lambda a:a[grouped]['records'].reverse()):
                bad=deepcopy(c);change(bad['identityRequests'])
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,bad,g)

    def test_sound_scopes_bind_both_source_links_and_grouped_identity(self):
        rows = [r for r in self.rows if r['module']=='BV_ActuaForStrucBorneSoundUDS']
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual(row['softwareIdentityKind'],'asam-odx-version')
            self.assertEqual(row['hardwareIdentity']['requestHex'],'22F191')
            self.assertEqual(row['sourceLinks'],[dict(linkIndex=0,txId=0x71C,rxId=0x786),dict(linkIndex=1,txId=0x764,rxId=0x7CE)])
            for link in (0,1):
                p,s,c,g = model(row,source_link=link)
                for generation in row['generations']:
                    scope,_ = complete_uds_scope(p,s,{**c,'generation':generation},g)
                    self.assertEqual(scope,row['completeScopeSource'])
                for invalid in (1-link,True,'0',2):
                    with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                        complete_uds_scope({**p,'selectedSourceLink':invalid},s,c,g)

    def test_sound_grouped_fields_and_complete_sizes_cannot_be_shortened(self):
        for row in [r for r in self.rows if r['module']=='BV_ActuaForStrucBorneSoundUDS']:
            p,s,c,g = model(row)
            grouped = next(i for i,r in enumerate(c['identityRequests']) if r.get('records'))
            bad=deepcopy(c);bad['identityRequests'][grouped]['records'].pop()
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_uds_scope(p,s,bad,g)
            for change in (lambda a:a.pop(),lambda a:a[0].update(expectedTotalBytes=9999)):
                bad=deepcopy(g);change(bad)
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                    complete_uds_scope(p,s,c,bad)

    def test_engine_factory_only_groups_are_kept_in_complete_backups(self):
        rows=[r for r in self.rows if r['module']=='BV_ECM_UDS']
        self.assertEqual(len(rows),9)
        for row in rows:
            p,s,c,g=model(row);scope,complete=complete_uds_scope(p,s,c,g)
            self.assertNotIn('2204FC',[r['requestHex'] for r in g])
            self.assertEqual(next(r['expectedTotalBytes'] for r in complete if r['requestHex']=='2204FC'),3)
            self.assertEqual(row['hardwareIdentity']['requestHex'],'22F191')
            self.assertEqual(row['softwareIdentity']['requestHex'],'22F197')
            self.assertEqual(row['softwareIdentity']['maximumBytes'],15)

    def test_unprogrammed_engine_unknown_factory_lengths_do_not_grant_full_scope(self):
        row=next(r for r in self.rows if r['module']=='BV_EnginContrModul1UDS')
        p,s,c,g=model(row)
        self.assertIsNone(complete_uds_scope({**p,'profileId':'9x1:BV_EnginContrModul1UDS:EV_Unprogrammiertes_SG'},s,c,g))

    def test_shared_scope_qualifies_each_declared_target_and_generation(self):
        rows=[r for r in self.rows if r.get('contexts')]
        self.assertEqual(len(rows),7)
        for row in rows:
            self.assertEqual(len(row['contexts']),2)
            for target in row['contexts']:
                p,s,c,g=model(row,target['ecuId'])
                self.assertEqual(s['ecuIds'],row['ecuIds'])
                for generation in row['generations']:
                    scope,_=complete_uds_scope(p,s,{**c,'generation':generation},g)
                    self.assertEqual(scope,row['completeScopeSource'])
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,{**s,'ecuIds':[target['ecuId']]},c,g)

    def test_shared_target_route_offer_cannot_be_removed_or_swapped(self):
        for row in [r for r in self.rows if r.get('contexts')]:
            left,right=row['contexts'];p,s,c,g=model(row,left['ecuId'])
            changes = [dict(ecuId=right['ecuId'])]
            changes.extend({key:right[key]} for key in ('txId','rxId') if left[key]!=right[key])
            if 'transportAddressing' in left:
                changes.extend([dict(transportAddressing=right['transportAddressing']),dict(transportAddressing=None)])
            for change in changes:
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,{**c,**change},g)

    def test_lane_gen2_scopes_pin_the_two_distinct_targets(self):
        rows=[r for r in self.rows if r['module']=='BV_ContrUnitLaneChangAssis2UDS']
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertIn('RadarS2',row['profileId'])
            self.assertEqual(row['ecuIds'],[95,96])
            self.assertEqual(row['hardwareIdentity']['requestHex'],'22F191')
            self.assertEqual([(c['ecuId'],c['workspaceEcu'],c['txId'],c['rxId']) for c in row['contexts']],
                             [(95,'swa-right',0x74E,0x7B8),(96,'swa-left',0x4D2,0x232)])
            self.assertEqual([g['bytes'] for g in row['completeScopeSource']['requestLengths']],[3,20,1,11,4])
            for target in row['contexts']:
                p,s,c,g=model(row,target['ecuId'])
                self.assertEqual(complete_uds_scope(p,s,c,g)[0],row['completeScopeSource'])
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,{**c,'identityRequests':c['identityRequests'][:-1]},g)

    def test_lane_gen1_write_only_dataset_cannot_gain_a_complete_read_scope(self):
        row=next(r for r in self.rows if r['module']=='BV_ContrUnitLaneChangAssis2UDS')
        p,s,c,g=model(row)
        for variant in ('EV_RearRadarS1BoschPO416_001','EV_RearRadarS1BoschPO67x_001'):
            self.assertIsNone(complete_uds_scope({**p,'profileId':'9x1:BV_ContrUnitLaneChangAssis2UDS:'+variant},s,c,g))

    def test_shared_scope_cannot_select_an_unknown_target(self):
        for row in [r for r in self.rows if r.get('contexts')]:
            p,s,c,g=model(row)
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_uds_scope(p,s,{**c,'ecuId':999},g)

    def test_another_module_or_route_cannot_reuse_the_scope(self):
        for row in self.reviewed:
            p,s,c,g=model(row)
            for change in (dict(module='other'),dict(ecuIds=[999])):
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,{**s,**change},c,g)
            for change in (dict(ecuId=999),dict(txId=0),dict(rxId=0),dict(applicationProtocol='uds' if c.get('applicationProtocol')=='kwp' else 'kwp'),dict(generation='other')):
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,{**c,**change},g)

    def test_selectors_session_and_identity_layout_must_match_the_same_source(self):
        for row in self.reviewed:
            p,s,c,g=model(row)
            for change in (dict(sourceCommands=[]),dict(session={}),dict(identityConstraints={}),dict(identityRequests=[])):
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,{**c,**change},g)
            if row.get('versionSelectors'):
                bad = deepcopy(c)
                bad['versionSelectors'][0]['selector']['formulaId'] = 0
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,bad,g)

    def test_missing_or_extra_blocks_and_short_factory_lengths_are_not_complete(self):
        p,s,c,g=model(self.reviewed[0])
        for change in (lambda a:a.pop(),lambda a:a.append({**a[0],'requestHex':'22FFFF'}),
                       lambda a:a[0].update(minimumKnownBytes=65535),lambda a:a[0].update(expectedTotalBytes=65535)):
            bad=deepcopy(g);change(bad)
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                complete_uds_scope(p,s,c,bad)

    def test_generic_and_unprogrammed_states_are_not_approved_by_the_normal_scope(self):
        p,s,c,g=model(self.reviewed[0])
        for pid in ('9x1:PSM:PSM_BaseSys','9x1:EPB:unprogrammierte_Parkbremse',
                    '9x1:PASM_PADM:unprogrammiertes_PASM','toString'):
            self.assertIsNone(complete_uds_scope({'profileId':pid},s,c,g))

    def test_body_scopes_remain_bound_to_their_original_hashes(self):
        rows=[r for r in self.rows if r['module'] in ('BCM_vorne','BCM_hinten')]
        self.assertEqual(len(rows),12)
        for row in rows:
            p,s,c,g=model(row)
            self.assertEqual(complete_uds_scope(p,s,c,g)[0]['sha256'],row['completeScopeSource']['sha256'])

    def test_factory_only_reads_preserve_unnamed_groups_without_inventing_fields(self):
        rows = [r for r in self.rows if r.get('factoryOnlyReadRequests')]
        self.assertEqual(len(rows),20)
        for row in rows:
            p,s,c,g = model(row)
            _,complete = complete_uds_scope(p,s,c,g)
            extras = [v for v in complete if v['requestHex'] not in {a['requestHex'] for a in g}]
            self.assertEqual(len(extras),len(row['factoryOnlyReadRequests']))
            for extra in extras:
                self.assertEqual(extra['identifierKind'],'DID')
                self.assertEqual(extra['minimumKnownBytes'],extra['expectedTotalBytes'])
                self.assertNotIn('fields',extra)

    def test_caller_cannot_relabel_factory_only_bytes_as_named_source_groups(self):
        row = next(r for r in self.rows if r.get('factoryOnlyReadRequests'))
        p,s,c,g = model(row)
        extra = row['factoryOnlyReadRequests'][0]['requestHex']
        with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
            complete_uds_scope(p,s,c,g+[{**g[0],'requestHex':extra}])

    def test_factory_extra_metadata_never_authorizes_write_or_unknown_opcodes(self):
        row = deepcopy(next(r for r in self.rows if r.get('factoryOnlyReadRequests')))
        p,s,c,g = model(row)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/'scope.json'
            for sid in (0x2E,0x31,0x14):
                row['factoryOnlyReadRequests'][0]['readSID'] = sid
                file.write_text(json.dumps(dict(schemaVersion=1,kind='reviewed-source-coding-read-scopes',profiles=[row])))
                with patch('scripts.diagnostics.uds_coding_source.SCOPES',file):
                    with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                        complete_uds_scope(p,s,c,g)

    def test_short_or_surplus_unnamed_group_cannot_become_a_complete_original(self):
        from scripts.diagnostics.manufacturer_coding import read_source_coding, SOURCE_CATALOG
        row = next(r for r in self.rows if r.get('factoryOnlyReadRequests'))
        p,s,c,g = model(row)
        scope,groups = complete_uds_scope(p,s,c,g)
        extra = row['factoryOnlyReadRequests'][0]['requestHex']
        sizes = {v['requestHex']:v['bytes'] for v in scope['requestLengths']}
        plan = dict(profileId=row['profileId'],requestGroups=groups,completeScopeSource=scope)
        q = dict(freshRead=True,observedProfileMatch=True,sourceCatalogId=SOURCE_CATALOG,
                 generation='981',ecuId=row['ecuId'],variantLink=dict(profile_id=row['profileId']))
        class Client:
            def request(self,hx,deadline):
                size = sizes[hx]+(self.delta if hx==extra else 0)
                return dict(ok=True,payload_hex='62'+hx[2:]+'A5'*size)
        for delta in (-1,1):
            client = Client();client.delta=delta;client.allowed={'3E00'}
            with patch('scripts.diagnostics.manufacturer_coding.source_coding_plan',return_value=plan):
                result = read_source_coding(client,p,c,q,time.monotonic()+5,lambda:None,require_complete_scope=True)
            self.assertFalse(result['ok'])
            self.assertFalse(result['completeOriginalRead'])
            self.assertIn(extra,result['missing'])
            self.assertEqual(client.allowed,{'3E00'})

    def test_kwp_media_scopes_preserve_local_identifiers_and_diagnostic_identity(self):
        rows = [r for r in self.rows if r['module'] in ('TVTuner','Verstaerker')]
        self.assertEqual(len(rows),4)
        for row in rows:
            p,s,c,g = model(row)
            self.assertEqual(row['softwareIdentityKind'],'diagnostic-version')
            self.assertNotIn('1A95',[r['requestHex'] for r in row['identityFields']])
            self.assertEqual(row['hardwareIdentity']['bytes'],11)
            _,groups = complete_uds_scope(p,s,c,g)
            self.assertTrue(all(r['requestHex'].startswith('21') and r['identifierKind']=='LID' for r in groups))
            for delta in (dict(identityLayoutResolvedByDsn=False),dict(identityConstraints={})):
                with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                    complete_uds_scope(p,s,{**c,**delta},g)

    def test_local_and_common_identifier_kinds_cannot_be_interchanged(self):
        for row in (self.rows[0],next(r for r in self.rows if r['module']=='TVTuner')):
            p,s,c,g = model(row)
            bad = deepcopy(g)
            bad[0]['identifierKind'] = 'DID' if g[0]['identifierKind']=='LID' else 'LID'
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                complete_uds_scope(p,s,c,bad)

    def test_headunit_factory_reads_use_each_fields_original_service(self):
        rows = [r for r in self.rows if r['module']=='Head_Unit']
        self.assertEqual(len(rows),5)
        kinds = []
        for row in rows:
            p,s,c,g = model(row);before = deepcopy(s)
            _,groups = complete_uds_scope(p,s,c,g)
            self.assertEqual(s,before, 'the first-record table must stay immutable')
            self.assertEqual({a['requestHex'] for a in groups},
                             {a['factoryReadRequestHex'] for a in row['fieldReadBindings']})
            self.assertEqual(row['softwareIdentity']['bytes'],8)
            kinds.extend(a['identifierKind'] for a in groups)
        self.assertEqual(kinds.count('LID'),81)
        self.assertEqual(kinds.count('DID'),9)

    def test_headunit_bindings_reject_different_original_fields_or_missing_coverage(self):
        row = next(r for r in self.rows if r['module']=='Head_Unit')
        _,s,_,_ = model(row)
        for change in (lambda a:a['coding']['requestGroups'].pop(),
                       lambda a:a['coding']['requestGroups'][0]['fields'][0].update(sourceOffset=-1),
                       lambda a:a['coding']['requestGroups'][0]['fields'][0].update(originalRecordReadSID=0x22),
                       lambda a:a['coding'].update(definitionSha256='0'*64)):
            bad=deepcopy(s);change(bad)
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                coding_field_read_bindings(bad,row)

    def test_headunit_binding_digest_does_not_authorize_another_request(self):
        row = next(r for r in self.rows if r['module']=='Head_Unit')
        _,s,_,_ = model(row)
        for change in (lambda b:b.pop(),lambda b:b[0].update(factoryReadRequestHex='3B00'),
                       lambda b:b[0].update(factoryReadRequestHex='2101'),
                       lambda b:b[0]['fields'][0].update(originalPrefixHex='223B0000000000')):
            bad=deepcopy(row);change(bad['fieldReadBindings'])
            bad['fieldReadBindingsSha256']=hashlib.sha256(json.dumps(bad['fieldReadBindings'],sort_keys=True,ensure_ascii=True).encode()).hexdigest()
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                coding_field_read_bindings(s,bad)

    def test_mixed_kwp_common_identifier_rejects_local_echo_and_surplus_data(self):
        from scripts.diagnostics.manufacturer_coding import read_source_coding, SOURCE_CATALOG
        row=next(r for r in self.rows if r['module']=='Head_Unit')
        p,s,c,g=model(row);scope,groups=complete_uds_scope(p,s,c,g)
        sizes={a['requestHex']:a['bytes'] for a in scope['requestLengths']}
        common=next(h for h in sizes if h.startswith('22'))
        plan=dict(profileId=row['profileId'],requestGroups=groups,completeScopeSource=scope)
        q=dict(freshRead=True,observedProfileMatch=True,sourceCatalogId=SOURCE_CATALOG,
               generation='981',ecuId=row['ecuId'],variantLink=dict(profile_id=row['profileId']))
        class Client:
            def request(self,hx,deadline):
                prefix='61' if hx.startswith('21') else '62'
                if hx==common and self.bad=='echo':prefix='61'
                return dict(ok=True,payload_hex=prefix+hx[2:]+'A5'*(sizes[hx]+int(hx==common and self.bad=='surplus')))
        for bad in ('echo','surplus'):
            client=Client();client.bad=bad;client.allowed={'3E00'}
            with patch('scripts.diagnostics.manufacturer_coding.source_coding_plan',return_value=plan):
                result=read_source_coding(client,p,c,q,time.monotonic()+5,lambda:None,require_complete_scope=True)
            self.assertFalse(result['ok'])
            self.assertFalse(result['completeOriginalRead'])
            self.assertIn(common,result['missing'])
            self.assertEqual(client.allowed,{'3E00'})
