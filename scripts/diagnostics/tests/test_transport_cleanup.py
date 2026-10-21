"""Adapter-local cleanup must remain bounded after acquisition cancellation."""
import threading
import unittest
from unittest.mock import patch

from scripts.diagnostics.bench import FakeClock, VirtualElmPort
from scripts.diagnostics.elm import ElmClient
from scripts.diagnostics import pt3g, vnci


class NativeCloseReportTests(unittest.TestCase):
    """Execute production close methods with injected API results, never DLLs."""
    def native(self, cls, errors=(), directory_error=False):
        native = cls.__new__(cls)
        native.module, native.constructed, native.link, native.online = 1, True, 2, True
        calls = []
        class Directory:
            count = 0
            def close(self):
                self.count += 1
                if directory_error:
                    raise OSError('SYNTHETIC directory close failed')
        native._directory = Directory()
        def call(name, *args):
            calls.append((name, args))
            if name in errors:
                raise RuntimeError('SYNTHETIC ' + name + ' failed')
        native.call = call
        return native, calls

    def test_native_cleanup_failures_survive_every_repeated_close_without_replay(self):
        for cls, actions in ((pt3g.NativeAdapter, ['PDUModuleDisconnect', 'PDUDestruct']),
                (vnci.NativeDpu, ['PDUDisconnect', 'PDUDestroyComLogicalLink', 'PDUModuleDisconnect', 'PDUDestruct'])):
            for failed in actions:
                with self.subTest(cls=cls.__name__, failed=failed):
                    native, calls = self.native(cls, [failed]); first = native.close()
                    expected = ['SYNTHETIC ' + failed + ' failed']
                    self.assertEqual(first['errors'], expected)
                    self.assertEqual([name for name, _ in calls], actions)
                    self.assertFalse(first['ecuRestorationProven'])
                    # A consumer cannot clear or replace the adapter's evidence.
                    first['errors'].clear(); first['adapterClose'] = 'SYNTHETIC changed'
                    second = native.close(); second['errors'].append('SYNTHETIC consumer note')
                    third = native.close()
                    self.assertEqual(third['errors'], expected)
                    self.assertNotEqual(third['adapterClose'], first['adapterClose'])
                    self.assertEqual(len(calls), len(actions))
                    self.assertEqual(native._directory.count, 1)

    def test_successful_native_close_is_also_once_only_and_returns_independent_reports(self):
        for cls in (pt3g.NativeAdapter, vnci.NativeDpu):
            with self.subTest(cls=cls.__name__):
                native, calls = self.native(cls)
                first = native.close(); count = len(calls)
                self.assertEqual(first['errors'], [])
                first['errors'].append('SYNTHETIC consumer note')
                self.assertEqual(native.close()['errors'], [])
                self.assertEqual(len(calls), count); self.assertEqual(native._directory.count, 1)

    def test_directory_release_failure_is_retained_with_all_native_errors(self):
        for cls in (pt3g.NativeAdapter, vnci.NativeDpu):
            with self.subTest(cls=cls.__name__):
                native, calls = self.native(cls, ['PDUModuleDisconnect', 'PDUDestruct'], directory_error=True)
                report = native.close()
                self.assertEqual(report['errors'], ['SYNTHETIC PDUModuleDisconnect failed', 'SYNTHETIC PDUDestruct failed',
                    'dll-directory-close:SYNTHETIC directory close failed'])
                self.assertEqual(native.close(), report); self.assertEqual(native._directory.count, 1)

    def test_close_during_native_release_never_claims_completed_or_replays_actions(self):
        for cls in (pt3g.NativeAdapter, vnci.NativeDpu):
            with self.subTest(cls=cls.__name__):
                native, calls = self.native(cls)
                entered, release = threading.Event(), threading.Event()
                original = native.call
                def wait(name, *args):
                    original(name, *args)
                    if len(calls) == 1:
                        entered.set()
                        if not release.wait(2): raise RuntimeError('SYNTHETIC test release timeout')
                native.call = wait; reports = []
                worker = threading.Thread(target=lambda: reports.append(native.close()))
                worker.start()
                second_reports, second_done = [], threading.Event()
                def second_close():
                    second_reports.append(native.close()); second_done.set()
                second = threading.Thread(target=second_close)
                try:
                    self.assertTrue(entered.wait(1))
                    second.start()
                    self.assertFalse(second_done.wait(.05), 'second close cannot return a report before actual release')
                    self.assertEqual(len(calls), 1); self.assertEqual(native._directory.count, 0)
                finally:
                    release.set(); worker.join(2)
                    if second.ident is not None: second.join(2)
                self.assertFalse(worker.is_alive()); self.assertEqual(reports[0]['errors'], [])
                self.assertFalse(second.is_alive()); self.assertEqual(second_reports, reports)
                self.assertEqual(native.close(), reports[0]); self.assertEqual(native._directory.count, 1)

    def test_base_native_dispatch_rejects_calls_after_close_before_loading_or_invoking(self):
        for cls in (pt3g.NativeAdapter, vnci.NativeDpu):
            with self.subTest(cls=cls.__name__):
                native, _ = self.native(cls); native.close()
                # No DLL attribute exists: the guard must run before dereferencing it.
                with self.assertRaisesRegex(RuntimeError, 'adapter-closed'):
                    cls.call(native, 'PDUModuleConnect', 1)

    def initialization_failure(self, stage, directory_error=False):
        for cls, module in ((pt3g.NativeAdapter, pt3g), (vnci.NativeDpu, vnci)):
            with self.subTest(cls=cls.__name__, stage=stage, directory_error=directory_error):
                calls = []
                class Directory:
                    count = 0
                    def close(self):
                        self.count += 1
                        if directory_error: raise OSError('SYNTHETIC directory release failed')
                class Function:
                    def __call__(self, *args):
                        calls.append('PDUConstruct')
                        return 0x60 if stage == 'construct' else 0
                class Dll:
                    def __getattr__(self, name):
                        if stage == 'binding' and name == 'PDUModuleConnect':
                            raise AttributeError('SYNTHETIC missing export')
                        return Function()
                directory = Directory()
                with patch.object(module.sys, 'platform', 'win32'), patch.object(module, 'verify_vendor_files'), \
                        patch.object(pt3g, 'verify_windows_config', return_value={}), \
                        patch.object(module.os, 'add_dll_directory', return_value=directory, create=True), \
                        patch.object(module.c, 'WinDLL', side_effect=OSError('SYNTHETIC DLL load failed') if stage == 'load' else None,
                                     return_value=Dll(), create=True):
                    with self.assertRaises(Exception) as caught:
                        cls('SYNTHETIC-driver-root')
                self.assertEqual(directory.count, 1)
                self.assertEqual(calls, ['PDUConstruct'] if stage == 'construct' else [])
                if directory_error:
                    self.assertIn('initialization-cleanup-failed', str(caught.exception))
                    self.assertIn('dll-directory-close:SYNTHETIC directory release failed', str(caught.exception))
                    self.assertIsNotNone(caught.exception.__cause__)
                    self.assertIn(str(caught.exception.__cause__), str(caught.exception))
                else:
                    self.assertNotIn('initialization-cleanup-failed', str(caught.exception))

    def test_initialization_load_failure_releases_owned_directory_once(self):
        self.initialization_failure('load')

    def test_initialization_binding_and_construct_failures_release_owned_directory_once(self):
        self.initialization_failure('binding'); self.initialization_failure('construct')

    def test_initialization_retains_primary_error_when_directory_cleanup_also_fails(self):
        for stage in ('load', 'binding', 'construct'):
            self.initialization_failure(stage, directory_error=True)


class CancelledCleanupTests(unittest.TestCase):
    def client(self, **port_options):
        clock, cancel = FakeClock(), threading.Event()
        port = VirtualElmPort(ecu_map={}, **port_options)
        client = ElmClient(port, clock=clock.time, sleeper=clock.sleep, cancel_event=cancel)
        cancel.set()
        return client, port, cancel, clock

    def test_cancelled_healthy_link_closes_protocol_once_without_ecu_request(self):
        client, port, cancel, _ = self.client()
        self.assertEqual(client.close_restore(), {'errors': []})
        self.assertEqual(port.writes, [b'ATPC\r'])
        self.assertTrue(port.closed)
        self.assertIs(client._cancel, cancel)
        self.assertTrue(cancel.is_set())
        self.assertEqual(client.close_restore(), {'errors': []})
        self.assertEqual(port.writes, [b'ATPC\r'])

    def test_poisoned_cancelled_link_never_writes_again(self):
        client, port, cancel, _ = self.client()
        client._poison('disconnect')
        self.assertEqual(client.close_restore()['errors'], ['poisoned:disconnect'])
        self.assertEqual(port.writes, [])
        self.assertTrue(port.closed)
        self.assertIs(client._cancel, cancel)

    def test_failed_protocol_close_still_closes_port_and_restores_cancellation(self):
        client, port, cancel, clock = self.client(atpc_body=b'ERROR\r\r>')
        result = client.close_restore()
        self.assertEqual(len(result['errors']), 1)
        self.assertTrue(result['errors'][0].startswith('ATPC:at-failed:'))
        self.assertEqual(port.writes, [b'ATPC\r'])
        self.assertTrue(port.closed)
        self.assertIs(client._cancel, cancel)
        self.assertLessEqual(clock.t, 3)

    def test_unresponsive_protocol_close_has_three_second_bound_and_closes_port(self):
        client, port, cancel, clock = self.client(atpc_body=b'')
        result = client.close_restore()
        self.assertTrue(result['errors'][0].startswith('ATPC:prompt-timeout:'))
        self.assertEqual(port.writes, [b'ATPC\r'])
        self.assertTrue(port.closed)
        self.assertIs(client._cancel, cancel)
        self.assertGreaterEqual(clock.t, 3)
        self.assertLess(clock.t, 3.02)

    def test_protocol_close_failure_is_retained_without_replaying_or_aliasing_report(self):
        client, port, _, _ = self.client(atpc_body=b'ERROR\r\r>')
        original = list(client.close_restore()['errors'])
        self.assertTrue(original)
        report = client.close_restore(); report['errors'].clear()
        self.assertEqual(client.close_restore()['errors'], original)
        self.assertEqual(port.writes, [b'ATPC\r'])

    def test_poison_close_failure_is_preserved_and_physical_close_is_not_retried(self):
        client, port, _, _ = self.client()
        calls = []
        def close():
            calls.append('close')
            raise OSError('SYNTHETIC serial release failed')
        port.close = close
        client._poison('disconnect')
        client._poison('later error')
        expected = ['poisoned:disconnect', 'close:SYNTHETIC serial release failed']
        self.assertEqual(client.close_restore()['errors'], expected)
        self.assertEqual(client.close_restore()['errors'], expected)
        self.assertEqual(calls, ['close'])
        self.assertEqual(port.writes, [])

    def test_close_in_progress_never_claims_release_or_replays_atpc(self):
        client, port, _, _ = self.client()
        entered, release = threading.Event(), threading.Event()
        original = port.close
        def close():
            entered.set()
            if not release.wait(2): raise RuntimeError('SYNTHETIC test timeout')
            original()
        port.close = close
        reports = []
        worker = threading.Thread(target=lambda: reports.append(client.close_restore()))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(client.close_restore()['errors'], ['close-in-progress'])
            self.assertEqual(port.writes, [b'ATPC\r'])
        finally:
            release.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reports, [{'errors': []}])
        self.assertEqual(client.close_restore(), reports[0])


if __name__ == '__main__': unittest.main()
