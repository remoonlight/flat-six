from copy import deepcopy
import json
import unittest

from scripts.diagnostics.dme_coding_source import complete_dme_scope, SCOPES


def model(row):
    profile = dict(profileId=row['profileId'])
    source = dict(module='DME_BDE_Continental', ecuIds=[1], coding={
        k:row[k] for k in ('definitionSha256','definitionFieldCount')})
    contract = {k:row[k] for k in ('sourceQualification','applicationProtocol','txId','rxId',
                                 'identityConstraints','sourceCommands','session')}
    contract.update(ecuId=1,generation='981',identityRequests=[dict(requestHex=r['requestHex'],
                    record=dict(at=r['sourceOffset'])) for r in row['identityFields']])
    groups = [dict(requestHex=g['requestHex'],identifierKind='LID',identifierHex=g['requestHex'][2:],
                   minimumKnownBytes=g['bytes'],expectedTotalBytes=None)
              for g in row['completeScopeSource']['requestLengths']]
    return profile, source, contract, groups


class DmeCodingScopes(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads(SCOPES.read_text())['profiles']

    def test_twelve_explicit_981_scopes_keep_all_twenty_eight_factory_blocks(self):
        self.assertEqual(len(self.rows),12)
        for row in self.rows:
            p,s,c,g = model(row)
            scope,groups = complete_dme_scope(p,s,c,g)
            self.assertEqual(scope,row['completeScopeSource'])
            self.assertEqual(len(groups),28)
            self.assertEqual([v['expectedTotalBytes'] for v in groups],
                             [v['bytes'] for v in scope['requestLengths']])

    def test_wrong_vehicle_route_session_version_or_identity_layout_is_rejected(self):
        p,s,c,g = model(self.rows[0])
        for change in [dict(ecuId=9),dict(generation='982'),dict(txId=0x710),dict(rxId=0x77A),
                       dict(applicationProtocol='uds'),dict(sourceQualification='uds-source-version-and-identity'),
                       dict(identityConstraints={}),dict(sourceCommands=[]),dict(session={}),dict(identityRequests=[])]:
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_dme_scope(p,s,{**c,**change},g)
        for key,value in [('module','other'),('ecuIds',[9]),('coding',{})]:
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_dme_scope(p,{**s,key:value},c,g)

    def test_missing_extra_or_wrong_length_blocks_cannot_be_complete(self):
        p,s,c,g = model(self.rows[0])
        for change in [lambda a:a.pop(),lambda a:a.append({**a[0],'requestHex':'21FF'}),
                       lambda a:a[0].update(minimumKnownBytes=4095),
                       lambda a:a[0].update(expectedTotalBytes=4095)]:
            bad=deepcopy(g);change(bad)
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                complete_dme_scope(p,s,c,bad)

    def test_other_models_and_factory_or_unprogrammed_states_are_not_approved(self):
        p,s,c,g = model(self.rows[0])
        for name in ('DME_BDE_Continental_BaseSys','SDI_9_1_unprogrammiert',
                     'SDI9_1_Werksdatensatz','SDI9_1_991_3_8L_EU5','DME_BDE_Continental'):
            self.assertIsNone(complete_dme_scope({'profileId':'9x1:DME_BDE_Continental:'+name},s,c,g))
