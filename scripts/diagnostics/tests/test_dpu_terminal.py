"""Independent native queues and clocks; no DLL, head or vehicle access."""
import threading
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.vnci import DpuClient, ID_UNDEF, UNDEF
from scripts.diagnostics.tests.test_vnci import FakeNative, ID
from scripts.diagnostics.elm import parse_ath1_response
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.sessions import run_session


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def status(value, cop=1):
    return {"type": 0x1301, "cop": cop, "value": value}


class QueueNative(FakeNative):
    def __init__(self, *, cancel_events=(), cancel_error=None):
        super().__init__(response=None)
        self.cancel_events = cancel_events
        self.cancel_error = cancel_error
        self.after_start = None
        self.reads = 0
    def start(self, payload):
        cop = super().start(payload)
        if self.after_start:
            self.after_start()
        return cop
    def event(self):
        self.reads += 1
        return super().event()
    def cancel(self, cop):
        self.cancelled.append(cop)
        self.events.extend(self.cancel_events)
        if self.cancel_error:
            raise ElmError(self.cancel_error)


def prepared(native):
    clock = Clock()
    stop = threading.Event()
    client = DpuClient(ID, native=native, cancel_event=stop, clock=clock, sleeper=clock.sleep)
    client.validate_adapter()
    client.configure_standard_engine(100)
    return client, clock, stop


class DpuTerminalTests(unittest.TestCase):
    def abort(self, native):
        client, clock, stop = prepared(native)
        native.after_start = stop.set
        with self.assertRaisesRegex(ElmError, '^cancelled$'):
            client.request('0100', 10)
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(native.cancelled, [1])
        with self.assertRaisesRegex(ElmError, 'client-poisoned'):
            client.request('0100', 10)
        self.assertEqual(native.sent, ['0100'])
        return client, clock

    def test_stopped_task_still_observes_its_own_cancelled_terminal(self):
        native = QueueNative(cancel_events=[status(0x8011), status(0x8013)])
        client, clock = self.abort(native)
        self.assertEqual(client.close_restore()['errors'], [])
        self.assertEqual(clock.now, 0)
        self.assertEqual(native.closed, 1)
        self.assertEqual([r['value'] for r in client.raw_log if r.get('dir') == 'status'],
                         [0x8011, 0x8013])

    def test_late_result_is_preserved_but_never_becomes_a_success(self):
        late = {**FakeNative.reply(bytes.fromhex('4100181A8000'), 1)[0],
                'timestamp': 3456, 'unique_id': 77, 'flags': bytes.fromhex('80000000')}
        native = QueueNative(cancel_events=[late, status(0x8012)])
        client, _ = self.abort(native)
        rx = [r for r in client.raw_log if r.get('dir') == 'rx']
        self.assertEqual(rx, [{'dir': 'rx', 'hex': '4100181A8000', 'layer': 'diagnostic-pdu',
            'cop': 1, 'eventType': 0x1300, 'timestampUs': 3456, 'uniqueId': 77, 'flagsHex': '80000000',
            'duringCleanup': True}])
        self.assertEqual(client.close_restore()['errors'], [])

    def test_cancel_error_survives_even_when_a_terminal_arrives(self):
        native = QueueNative(cancel_events=[status(0x8013)], cancel_error='lower-cancel-failed')
        client, _ = self.abort(native)
        errors = client.close_restore()['errors']
        self.assertEqual(errors, ['d-pdu:primitive-cancel-failed:lower-cancel-failed'])
        self.assertEqual(client.close_restore()['errors'], errors)
        self.assertEqual(native.closed, 1)

    def test_cancel_success_without_terminal_times_out_and_stays_failed(self):
        native = QueueNative()
        client, clock = self.abort(native)
        self.assertAlmostEqual(clock.now, 1.0)
        report = client.close_restore()
        self.assertEqual(report['errors'], ['d-pdu:primitive-terminal-timeout'])
        report['errors'].clear()
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:primitive-terminal-timeout'])
        self.assertEqual(native.closed, 1)

    def test_busy_event_queue_is_bounded_without_a_sleep(self):
        native = QueueNative(cancel_events=[status(0x8011)] * 1000)
        client, clock = self.abort(native)
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:cleanup-event-cap'])
        self.assertEqual(clock.now, 0)
        self.assertEqual(native.reads, 258)  # Setup status, empty poll, 256 cleanup polls.

    def test_foreign_terminal_cannot_prove_this_request_completed(self):
        native = QueueNative(cancel_events=[status(0x8013, 999)])
        client, _ = self.abort(native)
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:cleanup-foreign-primitive'])

    def test_unexpected_cleanup_events_and_states_are_failures(self):
        for event, reason in ((status(0x8014), 'cleanup-unexpected-state'),
                ({'type': 0x1302, 'cop': 1, 'value': 0x42}, 'cleanup-unexpected-event'),
                ({'type': 0x1303, 'cop': 1, 'value': 1}, 'cleanup-unexpected-event')):
            with self.subTest(event=event):
                native = QueueNative(cancel_events=[event])
                client, _ = self.abort(native)
                self.assertEqual(client.close_restore()['errors'], ['d-pdu:' + reason])

    def test_event_read_failure_is_retained_without_replacing_task_error(self):
        native = QueueNative()
        original = native.event
        def event():
            if native.cancelled:
                raise ElmError('native-event-read-failed')
            return original()
        native.event = event
        client, _ = self.abort(native)
        self.assertEqual(client.close_restore()['errors'], ['native-event-read-failed'])

    def test_deadline_abort_gets_a_separate_cleanup_wait(self):
        native = QueueNative(cancel_events=[status(0x8013)])
        client, clock, _ = prepared(native)
        with self.assertRaisesRegex(ElmError, '^deadline-expired$'):
            client.request('0100', 0.05)
        self.assertGreaterEqual(clock.now, 0.05)
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(native.cancelled, [1])
        self.assertEqual(client.close_restore()['errors'], [])

    def test_already_terminal_failure_is_not_cancelled_again(self):
        for value, reason in ((0x8012, 'finished-without-response'), (0x8013, '^cancelled$')):
            with self.subTest(value=value):
                native = QueueNative()
                native.make_events = lambda payload, cop: [status(value, cop)]
                client, _, _ = prepared(native)
                with self.assertRaisesRegex(ElmError, reason):
                    client.request('0100', 10)
                self.assertEqual(native.cancelled, [])
                self.assertEqual(client.close_restore()['errors'], [])

    def test_unknown_request_state_or_info_does_not_silently_continue(self):
        for event, reason in ((status(0x8014), 'unexpected-primitive-state'),
                             ({'type': 0x1303, 'cop': 1, 'value': 1}, 'unexpected-primitive-event')):
            with self.subTest(event=event):
                native = QueueNative(cancel_events=[status(0x8013)])
                native.make_events = lambda payload, cop: [event]
                client, _, _ = prepared(native)
                with self.assertRaisesRegex(ElmError, reason):
                    client.request('0100', 10)
                self.assertEqual(native.cancelled, [1])
                self.assertEqual(client.close_restore()['errors'], [])

    def test_missing_start_handle_is_unknown_and_never_retried(self):
        for handle in (None, True, -1, 1.5, ID_UNDEF, UNDEF):
            with self.subTest(handle=handle):
                native = QueueNative()
                def start(payload):
                    native.sent.append(payload.hex().upper())
                    return handle
                native.start = start
                client, _, _ = prepared(native)
                with self.assertRaisesRegex(ElmError, 'primitive-handle-missing'):
                    client.request('0100', 10)
                self.assertEqual(native.sent, ['0100'])
                self.assertEqual(native.cancelled, [])
                self.assertEqual(client.close_restore()['errors'], ['d-pdu:primitive-start-outcome-unknown'])

    def test_start_exception_keeps_the_unknown_outcome(self):
        native = QueueNative()
        def start(payload):
            native.sent.append(payload.hex().upper())
            raise ElmError('native-start-uncertain')
        native.start = start
        client, _, _ = prepared(native)
        with self.assertRaisesRegex(ElmError, '^native-start-uncertain$'):
            client.request('0100', 10)
        self.assertEqual(native.cancelled, [])
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:primitive-start-outcome-unknown'])

    def test_native_close_failure_is_sticky_and_not_repeated(self):
        native = QueueNative()
        native.close_errors = ['disconnect-failed', 'disconnect-failed']
        client, _, _ = prepared(native)
        first = client.close_restore()
        self.assertEqual(first['errors'], ['disconnect-failed'])
        first['errors'].clear()
        native.close_errors.clear()
        self.assertEqual(client.close_restore()['errors'], ['disconnect-failed'])
        self.assertEqual(native.closed, 1)

    def test_thrown_or_invalid_close_report_never_becomes_success(self):
        for result in (ElmError('destruct-failed'), None, {'errors': 'bad'}, {'errors': [None]}):
            with self.subTest(result=result):
                native = QueueNative()
                def close():
                    native.closed += 1
                    if isinstance(result, Exception):
                        raise result
                    return result
                native.close = close
                client, _, _ = prepared(native)
                expected = 'destruct-failed' if isinstance(result, Exception) else 'd-pdu:close-report-invalid'
                self.assertEqual(client.close_restore()['errors'], [expected])
                self.assertEqual(client.close_restore()['errors'], [expected])
                self.assertEqual(native.closed, 1)

    def test_close_during_request_never_destructs_active_native_code(self):
        native = QueueNative()
        client, _, _ = prepared(native)
        reports = []
        def after_start():
            reports.append(client.close_restore())
            self.assertEqual(native.closed, 0)
        native.after_start = after_start
        native.make_events = lambda payload, cop: FakeNative.reply(bytes.fromhex('4100181A8000'), cop)
        with self.assertRaisesRegex(ElmError, 'client-poisoned'):
            client.request('0100', 10)
        self.assertEqual(reports[0]['errors'], ['d-pdu:close-during-request'])
        with self.assertRaisesRegex(ElmError, 'client-poisoned'):
            client.request('0100', 10)
        self.assertEqual(native.sent, ['0100'])
        self.assertEqual(native.cancelled, [1])
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:close-during-request'])
        self.assertEqual(native.closed, 1)

    def test_cleanup_also_blocks_close_and_another_request(self):
        native = QueueNative(cancel_events=[status(0x8013)])
        client, _, stop = prepared(native)
        native.after_start = stop.set
        original = native.cancel
        def cancel(cop):
            self.assertEqual(client.close_restore()['errors'], ['d-pdu:close-during-request'])
            self.assertEqual(native.closed, 0)
            with self.assertRaisesRegex(ElmError, 'client-poisoned'):
                client.request('0100', 10)
            original(cop)
        native.cancel = cancel
        with self.assertRaisesRegex(ElmError, '^cancelled$'):
            client.request('0100', 10)
        self.assertEqual(client.close_restore()['errors'], ['d-pdu:close-during-request'])
        self.assertEqual(native.sent, ['0100'])

    def test_close_between_guard_and_lock_stops_before_native_poll_or_start(self):
        native = QueueNative()
        client, _, _ = prepared(native)
        def close_before_lock(payload, allowed):
            client.close_restore()
            return None
        with patch('scripts.diagnostics.vnci.blocked_reason', side_effect=close_before_lock):
            with self.assertRaisesRegex(ElmError, 'client-poisoned'):
                client.request('0100', 10)
        self.assertEqual(native.reads, 0)
        self.assertEqual(native.sent, [])
        self.assertEqual(native.closed, 1)

    def test_zero_handle_is_valid_and_successful_result_preserves_flags(self):
        native = QueueNative()
        def start(payload):
            native.sent.append(payload.hex().upper())
            native.events.extend(FakeNative.reply(bytes.fromhex('4100181A8000'), 0))
            return 0
        native.start = start
        client, _, _ = prepared(native)
        self.assertTrue(client.request('0100', 10)['ok'])
        self.assertEqual([r['flagsHex'] for r in client.raw_log if r.get('dir') == 'rx'], ['00000000'])
        self.assertEqual(native.cancelled, [])

    def session_native(self):
        native = QueueNative()
        fixtures = catalog_success_map('porsche-981-2014-dme')
        def events(payload, cop):
            req = payload.hex().upper()
            parsed = parse_ath1_response(fixtures[req].decode('ascii'), req_hex=req,
                                        rx_id=0x7E8, tx_id=0x7E0, sent_sf='')
            return FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
        native.make_events = events
        return native

    def test_session_completion_and_saved_manifest_fail_when_disconnect_fails(self):
        native = self.session_native()
        native.close_errors = ['native-disconnect-failed']
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: DpuClient(did, native=native, **kw)):
            result = run_session(profile_id='porsche-981-2014-dme', mode='live', device_id=ID,
                confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch))
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error'], 'cleanup-failed')
            self.assertEqual(result['restoration']['errors'], ['native-disconnect-failed'])
            saved = json.loads((Path(result['artifactDir']) / 'manifest.json').read_text(encoding='utf8'))
            self.assertFalse(saved['ok'])
            self.assertEqual(saved['error'], 'cleanup-failed')
        self.assertEqual(native.closed, 1)

    def test_session_cancel_keeps_late_raw_data_and_cleanup_error_in_saved_evidence(self):
        native = self.session_native()
        stop = threading.Event()
        native.cancel_error = 'native-cancel-failed'
        native.cancel_events = [FakeNative.reply(bytes.fromhex('580000'), 1)[0]]
        client_holder = []
        def events(payload, cop):
            if payload.hex().upper() == '1800FF00':
                stop.set()
                native.cancel_events = [FakeNative.reply(bytes.fromhex('580000'), cop)[0], status(0x8013, cop)]
                return []
            return prior(payload, cop)
        prior = native.make_events
        native.make_events = events
        def factory(did, **kw):
            client = DpuClient(did, native=native, **kw)
            client_holder.append(client)
            return client
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient', side_effect=factory):
            result = run_session(profile_id='porsche-981-2014-dme', mode='live', device_id=ID,
                confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch), cancel_event=stop)
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'cancelled')
            self.assertEqual(result['restoration']['errors'], ['d-pdu:primitive-cancel-failed:native-cancel-failed'])
            raw = json.loads((Path(result['artifactDir']) / 'raw.json').read_text(encoding='utf8'))
            self.assertEqual([r['hex'] for r in raw if r.get('duringCleanup') and r.get('dir') == 'rx'], ['580000'])
            self.assertNotIn('receivedCanFrames', result)  # PDU bytes are not reconstructed CAN.
            self.assertEqual(client_holder[0].close_restore()['errors'], result['restoration']['errors'])
        self.assertEqual(native.sent.count('1800FF00'), 1)
        self.assertEqual(native.closed, 1)


if __name__ == '__main__':
    unittest.main()
