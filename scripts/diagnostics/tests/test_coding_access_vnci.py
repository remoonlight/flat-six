"""Qualified access through D-PDU event fixtures; public reads remain blocked."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.pair import response_matches
from scripts.diagnostics.vnci import DpuClient
from scripts.diagnostics.tests import test_vnci as fixtures
from scripts.diagnostics.tests.test_manufacturer_coding_access import Clock, context


class CodingAccessNativeTests(unittest.TestCase):
    def opened(self):
        source,profile,contract,q=context();clock=Clock();native=fixtures.FakeNative()
        contract.update(sourceCatalogId=q['sourceCatalogId'],sourceQualification='uds-source-version-and-identity',
            txId=0x710,rxId=0x77a,applicationProtocol='uds',groups=[],identityRequests=[],
            sourceCommands=['1003'],session=dict(requestHex='1003'))
        client=DpuClient(fixtures.ID,native=native,clock=clock,sleeper=clock.sleep)
        client.validate_adapter()
        with patch('scripts.diagnostics.manufacturer_transport.prepared_read_contract',return_value=deepcopy(contract)):
            client.configure_manufacturer(profile,[],20,identity_only=True)
        def events(payload,cop):
            if payload[0]==0x10:reply=bytes([0x50,payload[1]])
            elif payload==bytes.fromhex('2703'):reply=bytes.fromhex('670312345678')
            else:reply=bytes.fromhex('6704')
            return native.reply(reply,cop)
        native.make_events=events
        return client,native,source,profile,contract,q

    def execute(self,ctx,guard=lambda:None):
        client,native,source,profile,contract,q=ctx
        with patch('scripts.diagnostics.manufacturer_coding_access.source_coding_plan',
                return_value={'completeScopeSource':{}}):
            return client.run_coding_access(source,profile,contract,q,20,guard)

    def test_whole_source_operation_does_not_broaden_public_read_permissions(self):
        ctx=self.opened();client,native,*_=ctx;allowed=set(client.allowed)
        result=self.execute(ctx)
        self.assertTrue(result['ok']);self.assertEqual(client.allowed,allowed)
        self.assertEqual(native.sent,['1003','2703','2704B66CB38B','104F','2703','2704B66CB38B'])
        self.assertFalse(result['codingWriteQualified'])
        for hx in ('2703','2EF10000'):
            client.allowed.add(hx)
            with self.assertRaisesRegex(ElmError,'blocked-sid'):client.request(hx,20)
        self.assertEqual(len(native.sent),6)
        client.close_restore()

    def test_wrong_native_route_is_rejected_before_a_primitive_starts(self):
        for field,value in (('profileId','different'),('ecuId',9),('generation','982'),
                ('txId',0x711),('rxId',0x77b),('applicationProtocol','kwp'),('sourceCatalogId','other')):
            ctx=list(self.opened());ctx[4][field]=value
            with self.assertRaisesRegex(ElmError,'native-link-unqualified'):self.execute(ctx)
            self.assertEqual(ctx[1].sent,[]);ctx[0].close_restore()

    def test_route_change_after_a_completed_primitive_stops_before_the_next(self):
        ctx=self.opened();client,native,*_=ctx;original=native.make_events
        def events(payload,cop):
            client._manufacturer_contract['rxId']=0x77b
            return original(payload,cop)
        native.make_events=events
        result=self.execute(ctx)
        self.assertFalse(result['ok']);self.assertTrue(result['requiresFreshSession'])
        self.assertEqual(native.sent,['1003']);self.assertTrue(client._poisoned)
        client.close_restore()

    def test_negative_or_wrong_subfunction_never_sends_a_key_or_retries(self):
        for reply in (bytes.fromhex('7F2735'),bytes.fromhex('670112345678'),bytes.fromhex('67031234')):
            ctx=self.opened();client,native,*_=ctx;original=native.make_events
            native.make_events=lambda payload,cop: native.reply(reply,cop) if payload[0]==0x27 else original(payload,cop)
            result=self.execute(ctx)
            self.assertFalse(result['ok']);self.assertTrue(result['requiresFreshSession'])
            self.assertEqual(native.sent,['1003','2703']);self.assertTrue(client._poisoned)
            client.close_restore()

    def test_stale_identity_is_rejected_without_native_io(self):
        ctx=list(self.opened());ctx[5]['freshRead']=False
        with self.assertRaisesRegex(ElmError,'identity-unqualified'):self.execute(ctx)
        self.assertEqual(ctx[1].sent,[]);ctx[0].close_restore()

    def test_security_pairing_requires_the_exact_subfunction_echo(self):
        self.assertEqual(response_matches(bytes.fromhex('2703'),bytes.fromhex('67031234')),'positive-echo')
        self.assertIsNone(response_matches(bytes.fromhex('2703'),bytes.fromhex('67011234')))
        self.assertIsNone(response_matches(bytes.fromhex('2703'),bytes.fromhex('67')))
        self.assertEqual(response_matches(bytes.fromhex('2703'),bytes.fromhex('7F2735')),'negative')


if __name__=='__main__':unittest.main()
