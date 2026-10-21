from copy import deepcopy
import json
import unittest

from scripts.diagnostics.uds_coding_source import complete_uds_scope, SCOPES


def model(row, ecu_id=None, source_link=0):
    if row.get('contexts'):
        target = next(r for r in row['contexts'] if r['ecuId'] == (ecu_id if ecu_id is not None else row['contexts'][0]['ecuId']))
        row = {**row,**target}
    profile = dict(profileId=row['profileId'])
    if row.get('sourceLinks'):
        row = {**row, **row['sourceLinks'][source_link]}
        profile['selectedSourceLink'] = source_link
    source = dict(module=row['module'], ecuIds=row.get('ecuIds',[row['ecuId']]), coding={
        k:row[k] for k in ('definitionSha256','definitionFieldCount')})
    contract = {k:row[k] for k in ('sourceQualification','applicationProtocol','txId','rxId',
                                 'identityConstraints','sourceCommands','session') if k in row}
    contract.update(ecuId=row['ecuId'],generation=row['generations'][0],identityRequests=deepcopy(row['identityLayout']) if 'identityLayout' in row else
                    [dict(requestHex=r['requestHex'],record=dict(at=r['sourceOffset'])) for r in row['identityFields']])
    if 'versionSelectors' in row:
        contract['versionSelectors'] = deepcopy(row['versionSelectors'])
    if 'identityLayoutResolvedByDsn' in row:
        contract['identityLayoutResolvedByDsn'] = row['identityLayoutResolvedByDsn']
    if 'transportAddressing' in row:
        contract['transportAddressing'] = deepcopy(row['transportAddressing'])
    groups = [dict(requestHex=g['requestHex'],identifierKind='LID' if g['requestHex'].startswith('21') else 'DID',identifierHex=g['requestHex'][2:],
                   minimumKnownBytes=g['bytes']-1,expectedTotalBytes=None)
              for g in row['completeScopeSource']['requestLengths']
              if g['requestHex'] not in {v['requestHex'] for v in row.get('factoryOnlyReadRequests', [])}]
    if row.get('fieldReadBindings'):
        original = {r['factoryReadRequestHex']:r['sourcePlanRequestHex'] for r in row['fieldReadBindings']}
        groups = [{**g, 'requestHex':original[g['requestHex']], 'identifierHex':original[g['requestHex']][2:],
                   'identifierKind':'LID' if original[g['requestHex']].startswith('21') else 'DID'} for g in groups]
        source['coding']['requestGroups'] = [dict(requestHex=b['sourcePlanRequestHex'],
            fields=[{**f, 'originalRecordReadSID':int(f['originalPrefixHex'][:2],16)} for f in b['fields']])
            for b in row['fieldReadBindings']]
    return profile, source, contract, groups


class BodyCodingScopes(unittest.TestCase):
    def setUp(self):
        self.rows = [r for r in json.loads(SCOPES.read_text())['profiles'] if r['module'] in ('BCM_vorne','BCM_hinten')]

    def test_twelve_body_scopes_keep_bytes_beyond_named_fields(self):
        self.assertEqual(len(self.rows),12)
        for row in self.rows:
            p,s,c,g = model(row)
            scope,groups = complete_uds_scope(p,s,c,g)
            self.assertEqual(scope,row['completeScopeSource'])
            self.assertEqual(len(groups),len(row['completeScopeSource']['requestLengths']))
            self.assertEqual([v['expectedTotalBytes'] for v in groups],
                             [v['bytes'] for v in scope['requestLengths']])

    def test_wrong_vehicle_route_session_version_or_identity_layout_is_rejected(self):
        p,s,c,g = model(self.rows[0])
        for change in [dict(ecuId=9),dict(generation='other'),dict(txId=0x710),dict(rxId=0x77A),
                       dict(applicationProtocol='kwp'),dict(sourceQualification='pdk-dsn-and-full-identity'),
                       dict(identityConstraints={}),dict(sourceCommands=[]),dict(session={}),dict(identityRequests=[])]:
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_uds_scope(p,s,{**c,**change},g)
        for key,value in [('module','other'),('ecuIds',[9]),('coding',{})]:
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_uds_scope(p,{**s,key:value},c,g)

    def test_missing_extra_or_wrong_length_blocks_cannot_be_complete(self):
        p,s,c,g = model(self.rows[0])
        for change in [lambda a:a.pop(),lambda a:a.append({**a[0],'requestHex':'21FF'}),
                       lambda a:a[0].update(minimumKnownBytes=4095),
                       lambda a:a[0].update(expectedTotalBytes=4095)]:
            bad=deepcopy(g);change(bad)
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                complete_uds_scope(p,s,c,bad)

    def test_other_models_and_factory_or_unprogrammed_states_are_not_approved(self):
        p,s,c,g = model(self.rows[0])
        for module in ('BCM_vorne','BCM_hinten'):
            for suffix in ('BaseSys','uncodiertes_Basis_SG','uncodiertes_Max_SG','uncodiertes_SG','unprogrammiertes_SG'):
                self.assertIsNone(complete_uds_scope({'profileId':'9x1:'+module+':'+module+'_'+suffix},s,c,g))
