"""Normalized-PDU peers, not diagnostic heads or vehicle evidence."""
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_coding_access as access
from scripts.diagnostics.x431_coding_access import CodingAccessSource
from scripts.diagnostics.elm import ElmError


PID = '9x1:Gateway:V'


class Clock:
    now = 0.0
    def __call__(self):return self.now
    def sleep(self, seconds):self.now += seconds


class Peer:
    def __init__(self, *, zero=False):
        self.allowed = {'22F100'}
        self.sent = []
        self.replies = {'1003': '500300100020', '104F': '504F',
            '2703': '670300000000' if zero else '670312345678',
            '2704B66CB38B': '6704'}
        self.override = {}
    def request(self, hx, deadline):
        assert self.allowed == {'22F100', hx}
        self.sent.append(hx)
        value = self.override.get(hx, dict(ok=True, payload_hex=self.replies.get(hx)))
        if isinstance(value, Exception):raise value
        return value


def context():
    source = CodingAccessSource.__new__(CodingAccessSource)
    source._rows = ((100, 0, 4, 0x03040100, 'Gateway', 'ASAM_seedkey_Gateway'),)
    profile = dict(profileId=PID, selectedEcuId=5, requestedGeneration='981')
    contract = dict(profileId=PID, ecuId=5, generation='981')
    qualification = dict(freshRead=True, observedProfileMatch=True,
        sourceCatalogId=access.SOURCE_CATALOG, generation='981', ecuId=5,
        variantLink=dict(profile_id=PID))
    return source, profile, contract, qualification


class CodingAccessClientTests(unittest.TestCase):
    def run_access(self, peer=None, *, guard=lambda:None, mutate=None, complete=True, deadline=20):
        peer = peer or Peer();clock = Clock();ctx = list(context())
        if mutate:mutate(ctx)
        with patch.object(access, 'source_coding_plan', return_value={
                'completeScopeSource': {}} if complete else {}):
            result = access.run_coding_access(peer, *ctx, deadline, guard,
                clock=clock, sleeper=clock.sleep)
        self.assertEqual(peer.allowed, {'22F100'})
        return result, peer, clock

    def test_source_sessions_each_get_a_fresh_seed_before_their_key(self):
        result, peer, clock = self.run_access()
        self.assertTrue(result['ok']);self.assertTrue(result['accessGranted'])
        self.assertEqual(peer.sent, ['1003', '2703', '2704B66CB38B',
            '104F', '2703', '2704B66CB38B'])
        self.assertFalse(result['codingWriteQualified'])
        self.assertFalse(result['codingRestoreQualified'])
        self.assertGreaterEqual(clock.now, 2)

    def test_zero_seed_skips_key_without_skipping_the_next_source_session(self):
        result, peer, _ = self.run_access(Peer(zero=True))
        self.assertTrue(result['ok'])
        self.assertEqual(peer.sent, ['1003', '2703', '104F', '2703'])
        self.assertTrue(all(v['alreadyGranted'] for v in result['completed'] if v['kind']=='access'))

    def test_mismatched_subfunction_or_seed_extent_never_sends_a_key(self):
        for hx in ('670112345678', '67031234', '67031234567890',
                '7F2735', '6703', '610312345678', 'not hex'):
            peer = Peer();peer.override['2703'] = dict(ok=True, payload_hex=hx)
            result, peer, _ = self.run_access(peer)
            self.assertFalse(result['ok'], hx);self.assertTrue(result['requiresFreshSession'])
            self.assertEqual(peer.sent, ['1003', '2703'])

    def test_failure_is_not_retried_and_does_not_advance_source_session(self):
        for value in (dict(ok=False, error='pending-timeout'), OSError('port-lost'),
                None, dict(ok=True, payload_hex='6705'), dict(ok=True, payload_hex='670400')):
            peer = Peer();peer.override['2704B66CB38B'] = value
            result, peer, _ = self.run_access(peer)
            self.assertFalse(result['ok']);self.assertTrue(result['requiresFreshSession'])
            self.assertEqual(peer.sent, ['1003', '2703', '2704B66CB38B'])

    def test_wrong_identity_or_incomplete_original_scope_is_rejected_before_io(self):
        for field, value in (('freshRead', False), ('observedProfileMatch', False),
                ('ecuId', 6), ('generation', '982'), ('sourceCatalogId', 'other'),
                ('variantLink', dict(profile_id='9x1:Gateway:Other'))):
            peer = Peer()
            with self.assertRaisesRegex(ElmError, 'identity-unqualified'):
                self.run_access(peer, mutate=lambda ctx:ctx[3].update({field:value}))
            self.assertEqual(peer.sent, [])
        peer = Peer()
        with self.assertRaisesRegex(ElmError, 'complete-scope-unqualified'):
            self.run_access(peer, complete=False)
        self.assertEqual(peer.sent, [])

    def test_unsupported_algorithm_or_flash_mode_cannot_send_anything(self):
        for mode, name in ((0x03040100, 'Unknown'), (0x11120a00, 'ASAM_seedkey_Gateway')):
            peer = Peer()
            def mutate(ctx):ctx[0]._rows=((100,0,4,mode,'Gateway',name),)
            with self.assertRaises(ElmError):self.run_access(peer, mutate=mutate)
            self.assertEqual(peer.sent, [])

    def test_guard_or_deadline_failure_never_retries_a_partially_started_exchange(self):
        peer = Peer();calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 3:raise ElmError('identity-changed')
        result, peer, _ = self.run_access(peer, guard=guard)
        self.assertFalse(result['ok']);self.assertTrue(result['requiresFreshSession'])
        self.assertEqual(peer.sent, ['1003'])
        result, peer, _ = self.run_access(deadline=0.2)
        self.assertFalse(result['ok']);self.assertEqual(peer.sent, ['1003'])

    def test_time_spent_in_guard_never_creates_a_negative_sleep(self):
        peer = Peer(zero=True);clock = Clock();ctx = context()
        def guard():clock.now += 0.04999
        def sleep(seconds):
            self.assertGreaterEqual(seconds, 0)
            clock.sleep(seconds)
        with patch.object(access, 'source_coding_plan', return_value={'completeScopeSource': {}}):
            result = access.run_coding_access(peer, *ctx, 20, guard, clock=clock, sleeper=sleep)
        self.assertTrue(result['ok'])
        self.assertEqual(peer.allowed, {'22F100'})


if __name__ == '__main__':unittest.main()
