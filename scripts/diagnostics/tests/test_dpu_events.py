"""Owned ctypes event storage and synthetic queues; no DLL or device access."""
import ctypes as c
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.diagnostics import vnci
from scripts.diagnostics.elm import ElmError, parse_ath1_response
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.sessions import run_session
from scripts.diagnostics.tests.test_dpu_terminal import Clock, QueueNative, status
from scripts.diagnostics.tests.test_vnci import FakeNative, ID


class NativeEventStorage:
    def __init__(self):
        self.owned = []
        self.destroyed = 0
        self.result = vnci.Result()
        self.item = vnci.Event(0x1300, 1, None, 4000, c.cast(c.pointer(self.result), vnci.PTR))
        self.native = vnci.NativeDpu.__new__(vnci.NativeDpu)
        self.native.module, self.native.link = 1, 9
        self.native.dll = type('Dll', (), {'PDUGetEventItem': self.get})()
        self.native.call = self.call
        self.code = 0
        self.set_result()

    def buffer(self, value):
        data = (c.c_uint8 * len(value))(*value)
        self.owned.append(data)
        return c.cast(data, vnci.PTR)

    def flag(self, value):
        return vnci.Flag(len(value), c.cast(self.buffer(value), c.POINTER(c.c_uint8)))

    def set_result(self, payload=bytes.fromhex('4100181A8000'), flags=b'\0\0\0\0'):
        self.result.flags = self.flag(flags)
        self.result.unique_id, self.result.acceptance = 1, 11
        self.result.timestamp_flags = self.flag(bytes.fromhex('C0000000'))
        self.result.tx_done, self.result.rx_start = 100, 200
        self.result.size = len(payload)
        self.result.data = self.buffer(payload) if payload else None

    def set_info(self, kind, value, extra):
        info = vnci.EventInfo(value, extra); self.owned.append(info)
        self.item.type = kind; self.item.data = c.cast(c.pointer(info), vnci.PTR)

    def set_extra(self, header, footer):
        extra = vnci.ExtraInfo(len(header), len(footer), self.buffer(header), self.buffer(footer))
        self.owned.append(extra); self.result.extra = c.cast(c.pointer(extra), vnci.PTR)
        return extra

    def get(self, module, link, out):
        if not self.code:
            c.cast(out, c.POINTER(c.POINTER(vnci.Event)))[0] = c.pointer(self.item)
        return self.code

    def call(self, name, ptr):
        assert name == 'PDUDestroyItem'
        self.destroyed += 1
        # Poison all borrowed buffers; decoded output must already own its bytes.
        for owner in self.owned:
            c.memset(c.addressof(owner), 0xAA, c.sizeof(owner))


def indicator(flags='00000002', unique=1, cop=1):
    return dict(type=0x1300, cop=cop, payload=b'', flags=bytes.fromhex(flags),
                unique_id=unique, timestamp=123, timestamp_flags=b'\0\0\0\0',
                tx_done=111, rx_start=222, acceptance_id=0)


def client(native=None):
    native = native or FakeNative()
    clock = Clock()
    result = vnci.DpuClient(ID, native=native, clock=clock, sleeper=clock.sleep)
    result.validate_adapter(); result.configure_standard_engine(10)
    return result, native, clock


class DpuEventTests(unittest.TestCase):
    def test_x64_event_metadata_layout_matches_retained_C_structs(self):
        expected = {vnci.Event: (32, {'type': 0, 'cop': 4, 'tag': 8, 'timestamp': 16, 'data': 24}),
            vnci.Result: (72, {'flags': 0, 'unique_id': 16, 'acceptance': 20, 'timestamp_flags': 24,
                              'tx_done': 40, 'rx_start': 44, 'extra': 48, 'size': 56, 'data': 64}),
            vnci.ExtraInfo: (24, {'header_size': 0, 'footer_size': 4, 'header': 8, 'footer': 16}),
            vnci.EventInfo: (8, {'value': 0, 'extra_info': 4})}
        for cls, (size, offsets) in expected.items():
            self.assertEqual(c.sizeof(cls), size)
            for name, offset in offsets.items():
                self.assertEqual(getattr(cls, name).offset, offset)

    def test_result_metadata_and_optional_bytes_are_copied_before_destroy(self):
        store = NativeEventStorage(); store.set_extra(bytes.fromhex('000007E8'), bytes.fromhex('AA'))
        ev = store.native.event()
        self.assertEqual(ev['payload'].hex().upper(), '4100181A8000')
        self.assertEqual(ev['timestamp_flags'].hex().upper(), 'C0000000')
        self.assertEqual((ev['acceptance_id'], ev['tx_done'], ev['rx_start']), (11, 100, 200))
        self.assertEqual(ev['extra_header'].hex().upper(), '000007E8')
        self.assertEqual(ev['extra_footer'].hex().upper(), 'AA')
        self.assertEqual(store.destroyed, 1)

    def test_absent_extra_is_distinct_from_present_empty_extra(self):
        store = NativeEventStorage(); ev = store.native.event()
        self.assertIsNone(ev['extra_header']); self.assertIsNone(ev['extra_footer'])
        store = NativeEventStorage(); store.set_extra(b'', b''); ev = store.native.event()
        self.assertEqual(ev['extra_header'], b''); self.assertEqual(ev['extra_footer'], b'')

    def test_zero_timestamp_flags_keep_raw_fields_without_validity_inference(self):
        store = NativeEventStorage(); store.result.timestamp_flags = store.flag(b'')
        ev = store.native.event()
        self.assertEqual(ev['timestamp_flags'], b'')
        self.assertEqual((ev['tx_done'], ev['rx_start']), (100, 200))

    def test_error_and_info_extra_words_are_not_lost(self):
        for kind in (0x1302, 0x1303):
            store = NativeEventStorage(); store.set_info(kind, 0x7005, 0xFEDCBA98)
            ev = store.native.event()
            self.assertEqual((ev['value'], ev['extra_info']), (0x7005, 0xFEDCBA98))
            self.assertEqual(store.destroyed, 1)

    def test_only_known_empty_progress_markers_are_parsed(self):
        for flags in ('00000002', '00000008', '00000009'):
            store = NativeEventStorage(); store.set_result(b'', bytes.fromhex(flags))
            self.assertEqual(store.native.event()['payload'], b'')
            self.assertEqual(store.destroyed, 1)
        for flags in ('', '00000000', '00000001', '00000010', '00000042', '02'):
            store = NativeEventStorage(); store.set_result(b'', bytes.fromhex(flags))
            with self.assertRaisesRegex(ElmError, 'invalid-result-size'):
                store.native.event()
            self.assertEqual(store.destroyed, 1)

    def test_bad_flag_counts_and_pointers_destroy_the_item(self):
        for field in ('flags', 'timestamp_flags'):
            for count in (1, 5):
                store = NativeEventStorage(); setattr(store.result, field, vnci.Flag(count, None))
                with self.assertRaisesRegex(ElmError, 'invalid-.*flags'):
                    store.native.event()
                self.assertEqual(store.destroyed, 1)

    def test_bad_extra_counts_and_pointers_are_bounded_and_destroyed(self):
        for field in ('header', 'footer'):
            for count in (1, 4096):
                store = NativeEventStorage(); extra = store.set_extra(b'', b'')
                setattr(extra, field + '_size', count); setattr(extra, field, None)
                with self.assertRaisesRegex(ElmError, 'invalid-result-extra-' + field):
                    store.native.event()
                self.assertEqual(store.destroyed, 1)

    def test_missing_or_oversized_payload_is_not_dereferenced(self):
        for size in (1, 4096):
            store = NativeEventStorage(); store.result.size = size; store.result.data = None
            with self.assertRaisesRegex(ElmError, 'invalid-result-size'):
                store.native.event()
            self.assertEqual(store.destroyed, 1)

    def test_empty_queue_does_not_destroy_an_unreturned_item(self):
        store = NativeEventStorage(); store.code = 0x71
        self.assertIsNone(store.native.event()); self.assertEqual(store.destroyed, 0)

    def test_progress_before_payload_neither_poison_nor_complete_the_request(self):
        cli, native, _ = client()
        native.make_events = lambda payload, cop: [indicator('00000008', vnci.ID_UNDEF), indicator()] + FakeNative.reply(bytes.fromhex('4100181A8000'), cop)
        result = cli.request('0100', 10)
        self.assertTrue(result['ok']); self.assertEqual(native.sent, ['0100']); self.assertEqual(native.cancelled, [])
        self.assertEqual([r['dir'] for r in cli.raw_log if 'dir' in r], ['tx', 'event', 'event', 'rx', 'status'])
        self.assertEqual([r['indication'] for r in cli.raw_log if 'indication' in r], ['tx-done', 'start-of-message'])
        self.assertEqual(cli.close_restore()['errors'], [])

    def test_marker_plus_finished_without_payload_is_a_failure_not_an_empty_value(self):
        cli, native, _ = client()
        native.make_events = lambda payload, cop: [indicator(), status(0x8012, cop)]
        with self.assertRaisesRegex(ElmError, 'finished-without-response'):
            cli.request('0100', 10)
        self.assertEqual(native.cancelled, [])

    def test_flagged_nonempty_and_unknown_identity_do_not_become_progress(self):
        for ev in ({**indicator(), 'payload': bytes.fromhex('4100181A8000')}, indicator(unique=99)):
            cli, native, _ = client()
            native.make_events = lambda payload, cop: [ev]
            with self.assertRaisesRegex(ElmError, 'malformed-or-unmatched-response'):
                cli.request('0100', 10)
            self.assertEqual(native.cancelled, [1]); self.assertEqual(native.sent, ['0100'])

    def test_foreign_progress_and_error_are_recorded_before_refusal(self):
        for ev, reason in ((indicator(cop=999), 'foreign-com-primitive'),
                           ({'type': 0x1302, 'cop': 1, 'timestamp': 456, 'value': 0x7005, 'extra_info': 1234}, 'event-error')):
            cli, native, _ = client()
            native.make_events = lambda payload, cop: [ev]
            with self.assertRaisesRegex(ElmError, reason):
                cli.request('0100', 10)
            rows = [r for r in cli.raw_log if r.get('eventType') == ev['type']]
            self.assertEqual(rows[0]['cop'], ev['cop'])
            if 'extra_info' in ev:
                self.assertEqual(rows[0]['extraInfo'], 1234)

    def test_progress_flood_is_bounded_even_with_a_nonadvancing_clock(self):
        cli, native, clock = client()
        native.make_events = lambda payload, cop: [indicator()] * 1000
        with self.assertRaisesRegex(ElmError, 'request-event-cap'):
            cli.request('0100', 10)
        self.assertEqual(native.sent, ['0100']); self.assertEqual(native.cancelled, [1])
        self.assertEqual(cli.close_restore()['errors'], ['d-pdu:cleanup-event-cap'])
        self.assertEqual(clock.now, 0)
        self.assertLessEqual(len(cli.raw_log), 515)

    def test_cap_does_not_recancel_a_terminal_already_observed(self):
        cli, native, _ = client()
        native.make_events = lambda payload, cop: [status(0x8011, cop)] * 256 + [status(0x8012, cop)]
        with self.assertRaisesRegex(ElmError, 'request-event-cap'):
            cli.request('0100', 10)
        self.assertEqual(native.cancelled, [])

    def test_cleanup_preserves_indications_and_extra_error_without_success(self):
        native = QueueNative(cancel_events=[indicator(), {'type': 0x1302, 'cop': 1, 'value': 0x7005, 'extra_info': 555}])
        cli, _, _ = client(native)
        stop = threading.Event(); cli.cancel_event = stop; native.after_start = stop.set
        with self.assertRaisesRegex(ElmError, '^cancelled$'):
            cli.request('0100', 10)
        rows = [r for r in cli.raw_log if r.get('duringCleanup')]
        self.assertEqual(rows[0]['dir'], 'event'); self.assertEqual(rows[1]['extraInfo'], 555)
        self.assertEqual(cli.close_restore()['errors'], ['d-pdu:cleanup-unexpected-event'])

    def test_native_metadata_survives_json_and_does_not_invent_bus_timing(self):
        cli, native, _ = client(); store = NativeEventStorage(); store.set_extra(b'HEAD', b'END')
        event = store.native.event()
        native.make_events = lambda payload, cop: [event, status(0x8012, cop)]
        self.assertTrue(cli.request('0100', 10)['ok'])
        saved = json.loads(json.dumps(cli.raw_log)); rx = next(r for r in saved if r.get('dir') == 'rx')
        self.assertEqual(rx['timestampFlagsHex'], 'C0000000')
        self.assertEqual((rx['txDoneTimestampUsRaw'], rx['rxStartTimestampUsRaw']), (100, 200))
        self.assertEqual((rx['extraHeaderHex'], rx['extraFooterHex']), ('48454144', '454E44'))
        self.assertNotIn('busTimestampUs', rx)

    def test_full_session_records_progress_metadata_without_pseudo_CAN_frames(self):
        native = FakeNative(); fixtures = catalog_success_map('porsche-981-2014-dme')
        def events(payload, cop):
            req = payload.hex().upper()
            parsed = parse_ath1_response(fixtures[req].decode('ascii'), req_hex=req,
                                        rx_id=0x7E8, tx_id=0x7E0, sent_sf='')
            return [indicator(cop=cop)] + FakeNative.reply(bytes.fromhex(parsed['payload_hex']), cop)
        native.make_events = events
        real_client = vnci.DpuClient
        with tempfile.TemporaryDirectory() as scratch, patch('scripts.diagnostics.vnci.DpuClient',
                side_effect=lambda did, **kw: real_client(did, native=native, **kw)):
            result = run_session(profile_id='porsche-981-2014-dme', mode='live', device_id=ID,
                confirmed_read_only=True, x431_inactive=True, artifact_root=Path(scratch))
            self.assertTrue(result['ok'], result)
            raw = json.loads((Path(result['artifactDir']) / 'raw.json').read_text(encoding='utf8'))
            self.assertEqual(sum(r.get('indication') == 'start-of-message' for r in raw), len(native.sent))
            self.assertNotIn('receivedCanFrames', result)
            self.assertEqual(result['restoration']['errors'], [])


if __name__ == '__main__':
    unittest.main()
