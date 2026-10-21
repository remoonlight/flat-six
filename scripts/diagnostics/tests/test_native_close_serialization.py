"""Thread ordering at native API/borrowed-item/close boundaries; no real DLL."""
import ctypes as c
import threading
import unittest

from scripts.diagnostics import pt3g, pt3g_read_source, pt3g_read_transport, pt3g_version, vnci
from scripts.diagnostics.tests.test_pt3g_closed_api import native, query


class NativeCloseSerializationTests(unittest.TestCase):
    classes = (vnci.NativeDpu, pt3g.NativeAdapter, pt3g_read_transport.NativeReadTransport,
               pt3g_read_source._ReadObjectProbe, pt3g_version._MetadataProbe)

    def start(self, action):
        result = {}
        begun, done = threading.Event(), threading.Event()
        def run():
            begun.set()
            try: result['value'] = action()
            except Exception as error: result['error'] = error
            finally: done.set()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(begun.wait(2))
        return thread, done, result

    def finish(self, jobs):
        for thread, done, _ in jobs:
            thread.join(2)
            self.assertTrue(done.is_set(), 'native operation did not finish')

    def query(self, value):
        if isinstance(value, (pt3g_read_transport.NativeReadTransport,
                              pt3g_read_source._ReadObjectProbe, pt3g_version._MetadataProbe)):
            return query(value)
        out = vnci.U32(vnci.UNDEF)
        if isinstance(value, pt3g.NativeAdapter):
            return value.call('PDUGetObjectId', 0x8023, b'PDU_IOCTL_READ_VBATT', c.byref(out))
        return value.call('PDUGetObjectId', 0x8022, b'ISO_11898_2_DWCAN', c.byref(out))

    def test_no_query_enters_DLL_during_destruct_and_first_close_report_is_preserved(self):
        for cls in self.classes:
            for failure in (0, 0x60):
                with self.subTest(cls=cls.__name__, failure=failure):
                    value = native(cls, failure)
                    value._allowed.add('PDUGetObjectId')
                    entered, release = threading.Event(), threading.Event()
                    original = value.dll.PDUDestruct
                    def destruct():
                        entered.set()
                        if not release.wait(2): raise AssertionError('test destruct release missing')
                        return original()
                    value.dll.PDUDestruct = destruct
                    closing = self.start(value.close)
                    self.assertTrue(entered.wait(2))
                    querying = self.start(lambda: self.query(value))
                    try:
                        self.assertFalse(querying[1].wait(.05), 'query completed while destruct was in progress')
                        self.assertNotIn('PDUGetObjectId', value.dll.calls)
                    finally:
                        release.set()
                        self.finish([closing, querying])
                    self.assertIn('adapter-closed', str(querying[2].get('error')))
                    report = closing[2]['value']
                    self.assertEqual(bool(report['errors']), bool(failure))
                    self.assertEqual(value.close(), report)
                    self.assertEqual(value.dll.calls, ['PDUDestruct'])
                    self.assertEqual(value._directory.closed, 1)

    def test_concurrent_second_close_waits_for_first_report_without_repeating_destruct(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = native(cls, 0x60)
                entered, release = threading.Event(), threading.Event()
                original = value.dll.PDUDestruct
                def destruct():
                    entered.set()
                    if not release.wait(2): raise AssertionError('test destruct release missing')
                    return original()
                value.dll.PDUDestruct = destruct
                first = self.start(value.close)
                self.assertTrue(entered.wait(2))
                second = self.start(value.close)
                try: self.assertFalse(second[1].wait(.05), 'second close returned an unfinished report')
                finally:
                    release.set()
                    self.finish([first, second])
                self.assertEqual(first[2]['value'], second[2]['value'])
                self.assertEqual(value.dll.calls, ['PDUDestruct'])
                self.assertEqual(value._directory.closed, 1)

    def test_borrowed_event_is_copied_and_destroyed_before_close_enters_DLL(self):
        for cls in (vnci.NativeDpu,):
            value = native(cls)
            value.module, value.link, value.online = 1, 2, True
            entered, release = threading.Event(), threading.Event()
            status = vnci.U32(0x8012)
            item = vnci.Event()
            item.type, item.cop, item.timestamp = 0x1301, 7, 99
            item.data = c.cast(c.pointer(status), vnci.PTR)
            def get_event(_module, _link, pointer):
                value.dll.calls.append('PDUGetEventItem')
                entered.set()
                if not release.wait(2): raise AssertionError('test event release missing')
                c.cast(pointer, c.POINTER(c.POINTER(vnci.Event)))[0] = c.pointer(item)
                return 0
            def action(name):
                def call(*_args): value.dll.calls.append(name); return 0
                return call
            value.dll.PDUGetEventItem = get_event
            for name in ('PDUDestroyItem', 'PDUDisconnect', 'PDUDestroyComLogicalLink', 'PDUModuleDisconnect'):
                setattr(value.dll, name, action(name))
            reading = self.start(value.event)
            self.assertTrue(entered.wait(2))
            closing = self.start(value.close)
            try: self.assertFalse(closing[1].wait(.05), 'close passed a borrowed event still being read')
            finally:
                release.set()
                self.finish([reading, closing])
            self.assertNotIn('error', reading[2])
            self.assertEqual(reading[2]['value']['value'], status.value)
            self.assertEqual(value.dll.calls, ['PDUGetEventItem', 'PDUDestroyItem', 'PDUDisconnect',
                'PDUDestroyComLogicalLink', 'PDUModuleDisconnect', 'PDUDestruct'])

    def test_module_enumeration_item_is_read_and_freed_before_destruct(self):
        for cls in (vnci.NativeDpu, pt3g.NativeAdapter, pt3g_read_transport.NativeReadTransport):
            with self.subTest(cls=cls.__name__):
                value = native(cls)
                value._allowed.update(('PDUGetModuleIds', 'PDUDestroyItem'))
                entered, release = threading.Event(), threading.Event()
                item = vnci.ModuleItem()
                item.type, item.count = 0x1600, 0
                def modules(pointer):
                    value.dll.calls.append('PDUGetModuleIds')
                    entered.set()
                    if not release.wait(2): raise AssertionError('test module release missing')
                    c.cast(pointer, c.POINTER(c.POINTER(vnci.ModuleItem)))[0] = c.pointer(item)
                    return 0
                def destroy(_pointer): value.dll.calls.append('PDUDestroyItem'); return 0
                value.dll.PDUGetModuleIds, value.dll.PDUDestroyItem = modules, destroy
                reading = self.start(value.modules)
                self.assertTrue(entered.wait(2))
                closing = self.start(value.close)
                try: self.assertFalse(closing[1].wait(.05), 'close passed a borrowed module item')
                finally:
                    release.set()
                    self.finish([reading, closing])
                self.assertEqual(reading[2].get('value'), [])
                self.assertEqual(value.dll.calls, ['PDUGetModuleIds', 'PDUDestroyItem', 'PDUDestruct'])


if __name__ == '__main__': unittest.main()
