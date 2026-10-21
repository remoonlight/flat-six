from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics.gateway_coding_source import complete_gateway_scope, SCOPES
from scripts.diagnostics import manufacturer_coding as coding, manufacturer_read as reader
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.tests.test_manufacturer_coding import context, source, Client
from scripts.diagnostics.tests.test_manufacturer_read import request


def model(row, generation='981'):
    profile = dict(profileId=row['profileId'])
    source = dict(module='CAN_CAN_Gateway', ecuIds=[9], coding={
        k:row[k] for k in ('definitionSha256','definitionFieldCount')})
    contract = dict(ecuId=9, generation=generation, sourceQualification='uds-source-version-and-identity',
        applicationProtocol='uds', txId=0x710, rxId=0x77A, identityConstraints=row['identityConstraints'],
        session=dict(requestHex='1003',positivePrefixHex='5003'),
        sourceCommands=['3E00','22F1A2','22F19E','22F1DF','1003'])
    groups = [dict(requestHex=g['requestHex'], identifierKind='DID', identifierHex=g['requestHex'][2:],
                   minimumKnownBytes=g['bytes']-1, expectedTotalBytes=None)
              for g in row['completeScopeSource']['requestLengths']]
    return profile, source, contract, groups


class GatewayCodingScopes(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads(SCOPES.read_text())['profiles']

    def test_all_reviewed_scopes_keep_factory_bytes_beyond_named_fields(self):
        self.assertEqual(len(self.rows),15)
        for row in self.rows:
            for generation in ('981','982'):
                p,s,c,g = model(row,generation)
                scope, groups = complete_gateway_scope(p,s,c,g)
                self.assertEqual(scope,row['completeScopeSource'])
                self.assertEqual([v['expectedTotalBytes'] for v in groups],
                                 [v['bytes'] for v in scope['requestLengths']])

    def test_identity_session_protocol_and_definition_must_match(self):
        p,s,c,g = model(self.rows[0])
        for changes in [dict(ecuId=47),dict(generation='other'),dict(txId=0x725),dict(rxId=0x78F),
                        dict(applicationProtocol='kwp'),dict(identityConstraints={}),dict(sourceCommands=[]),dict(session={})]:
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
                complete_gateway_scope(p,s,{**c,**changes},g)
        with self.assertRaisesRegex(ValueError,'coding-complete-scope-unqualified'):
            complete_gateway_scope(p,{**s,'coding':{}},c,g)

    def test_missing_extra_or_conflicting_group_lengths_are_rejected(self):
        p,s,c,g = model(self.rows[0])
        for change in [lambda a:a.pop(),lambda a:a.append({**a[0],'requestHex':'220011'}),
                       lambda a:a[0].update(minimumKnownBytes=4095),
                       lambda a:a[0].update(expectedTotalBytes=4095)]:
            bad=deepcopy(g);change(bad)
            with self.assertRaisesRegex(ValueError,'coding-complete-scope-conflict'):
                complete_gateway_scope(p,s,c,bad)

    def test_a71_extra_filterstatus_and_generic_states_are_not_qualified(self):
        p,s,c,g = model(self.rows[0])
        for name in ('CAN_CAN_Gateway_A7_1','CAN_CAN_Gateway_BaseSys','Werksdatensatz_Gateway','unprogrammiertes_Gateway'):
            self.assertIsNone(complete_gateway_scope({'profileId':'9x1:CAN_CAN_Gateway:'+name},s,c,g))

    def test_complete_desktop_read_stops_before_unqualified_coding_groups(self):
        p,c,q=context();client=Client()
        with patch.object(coding,'_source_profile',return_value=source()),self.assertRaisesRegex(ElmError,'coding-complete-scope-unqualified'):
            coding.read_source_coding(client,p,c,q,time.monotonic()+5,lambda:None,require_complete_scope=True)
        self.assertEqual(client.sent,[])

    def test_complete_scope_flag_is_typed_and_restricted_to_coding_reads(self):
        for value in ('yes',1,None,[]):
            with self.assertRaisesRegex(ValueError,'manufacturer-complete-scope-flag-invalid'):
                reader._validate({**request(), 'action':'manufacturer-read-coding','parameterIds':[],
                                  'completeOriginalRequired':value,'sourceBound':True})
        with self.assertRaisesRegex(ValueError,'manufacturer-complete-scope-flag-invalid'):
            reader._validate({**request(),'completeOriginalRequired':True})
