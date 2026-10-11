"""Exercise the production engine runner with synthetic D-PDU events only."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.elm import parse_ath1_response
from scripts.diagnostics.session_simulator import scenario_map
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.tests.test_vnci import DpuClient, FakeNative, ID


class VnciEngineSessionTests(unittest.TestCase):
    def run_engine(self, *, scenario='success', mask=None, handoff=None, mutate=None, close_errors=None):
        native, stop, captured = FakeNative(), threading.Event(), []
        fixtures = scenario_map('porsche-981-2014-dme', scenario, engine_mask=mask)
        native.close_errors = close_errors or []
        def events(payload, cop):
            req = payload.hex().upper()
            self.assertEqual(native.links[-1][1:], (0x7DF, 0x7E8) if req.startswith('01') else (0x7E0, 0x7E8))
            parsed = parse_ath1_response(fixtures[req].decode('ascii'), req_hex=req,
                                        rx_id=0x7E8, tx_id=0x7E0, sent_sf='')
            rows = FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
            if mutate and req == '0100':
                rows[0].update(mutate)
            return rows
        native.make_events = events
        release = native.release_link
        def switch():
            if handoff == 'cancel': stop.set()
            if handoff == 'failure':
                return {'errors': ['synthetic-link-release-failed'], 'moduleRetained': True}
            return release()
        native.release_link = switch
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: DpuClient(did, native=native, **kw)):
            result = run_session(profile_id='porsche-981-2014-dme', mode='live', device_id=ID,
                session_task='engine', selected_pids=['0C', '11'], sample_cycles=2, interval_ms=500,
                confirmed_read_only=True, x431_inactive=True, cancel_event=stop,
                artifact_root=Path(scratch), progress_sink=captured.append)
            self.assertIsNotNone(result['artifactDir'], result)
            saved = json.loads((Path(result['artifactDir']) / 'engine.json').read_text())
        return result, native, saved, captured

    def test_identity_then_functional_route_selection_decode_save_and_release(self):
        result, native, saved, captured = self.run_engine()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['identityQualification']['observedProfileMatch'])
        self.assertFalse(result['liveVerified'], 'synthetic native events never confer vehicle qualification')
        self.assertEqual(native.links, [('ISO_14230_3_on_ISO_15765_2', 0x7E0, 0x7E8),
                                        ('ISO_OBD_on_ISO_15765_4', 0x7DF, 0x7E8)])
        self.assertEqual(native.connected, [123])
        self.assertEqual([r for r in native.sent if r.startswith('01')],
                         ['0100', '010C', '0111', '010C', '0111'])
        self.assertNotIn('1800FF00', native.sent)
        self.assertEqual(saved['completedCycles'], 2)
        self.assertEqual(saved['acquisitionRoute']['txId'], '7DF')
        self.assertEqual([s['value'] for s in saved['samples'] if s['pid'] == '0C'], [2000.0, 2000.0])
        self.assertEqual(len(saved['samples']), 4)
        self.assertEqual([e['pdu']['requestHex'] for e in captured if e['type'] == 'diagnostic-pdu'], native.sent)
        self.assertEqual(result['restoration']['errors'], [])
        self.assertEqual(native.closed, 1)

    def test_identity_mismatch_never_switches_to_functional_route(self):
        result, native, _, _ = self.run_engine(scenario='identity-mismatch')
        self.assertEqual(result['error'], 'identity-mismatch')
        self.assertEqual(len(native.links), 1)
        self.assertFalse(any(r.startswith('01') for r in native.sent))
        self.assertEqual(native.closed, 1)

    def test_support_mask_controls_actual_native_pid_requests(self):
        result, native, saved, _ = self.run_engine(mask=bytes.fromhex('00008000'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(saved['unsupportedPids'], ['0C'])
        self.assertEqual([r for r in native.sent if r.startswith('01')], ['0100', '0111', '0111'])

    def test_release_failure_or_cancellation_never_queries_standard_route(self):
        for handoff in ('failure', 'cancel'):
            with self.subTest(handoff=handoff):
                result, native, _, _ = self.run_engine(handoff=handoff)
                self.assertFalse(result['ok'], result)
                self.assertEqual(len(native.links), 1)
                self.assertFalse(any(r.startswith('01') for r in native.sent))
                self.assertEqual(native.closed, 1)
                if handoff == 'failure':
                    self.assertIn('synthetic-link-release-failed', result['restoration']['errors'])
                else:
                    self.assertEqual(result['error'], 'cancelled')

    def test_unmatched_native_response_stops_without_retry_or_sampling(self):
        for change in ({'unique_id': 99}, {'flags': b'\x80'}, {'payload': bytes.fromhex('410C1F40')}):
            with self.subTest(change=change):
                result, native, saved, _ = self.run_engine(mutate=change)
                self.assertFalse(result['ok'], result)
                self.assertEqual([r for r in native.sent if r.startswith('01')], ['0100'])
                self.assertEqual(saved['samples'], [])
                self.assertEqual(native.closed, 1)

    def test_final_cleanup_failure_does_not_report_successful_batch(self):
        result, native, saved, _ = self.run_engine(close_errors=['synthetic-close-failed'])
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'cleanup-failed')
        self.assertEqual(result['restoration']['errors'], ['synthetic-close-failed'])
        self.assertEqual(len(saved['samples']), 4, 'received results survive failed release')
        self.assertEqual(native.closed, 1)


if __name__ == '__main__':
    unittest.main()
