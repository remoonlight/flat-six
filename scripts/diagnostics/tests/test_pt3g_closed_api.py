"""Production close and API dispatch with owned synthetic DLL results; no driver."""
import ctypes as c
from copy import deepcopy
import unittest

from scripts.diagnostics import pt3g, pt3g_read_source, pt3g_read_transport, pt3g_version, vnci
from scripts.diagnostics.elm import ElmError


class Directory:
    def __init__(self): self.closed = 0
    def close(self): self.closed += 1


class Dll:
    def __init__(self, destruct_error=0):
        self.calls = []
        self.destruct_error = destruct_error
    def PDUDestruct(self):
        self.calls.append('PDUDestruct')
        return self.destruct_error
    def PDUGetObjectId(self, _kind, _name, pointer):
        self.calls.append('PDUGetObjectId')
        c.cast(pointer, c.POINTER(vnci.U32)).contents.value = 0
        return 0
    def PDUGetVersion(self, *_args):
        self.calls.append('PDUGetVersion')
        return 0


def native(cls, error=0):
    value = cls.__new__(cls)
    value._close_report = None
    value._directory = Directory()
    value.module, value.link, value.online, value.constructed = None, None, False, True
    value._allowed = {'PDUDestruct'}
    value.dll = Dll(error)
    value._source_objects = {(0x8022, b'ISO_11898_2_DWCAN'): 0}
    value._read_objects = set(value._source_objects)
    return value


def query(value):
    if isinstance(value, pt3g_version._MetadataProbe):
        value.call('PDUGetVersion', vnci.UNDEF, c.byref(pt3g_version.VersionData()))
    else:
        output = vnci.U32(vnci.UNDEF)
        value.call('PDUGetObjectId', 0x8022, b'ISO_11898_2_DWCAN', c.byref(output))
        if output.value != 0: raise AssertionError('valid zero source object changed')


class ClosedApiTests(unittest.TestCase):
    classes = (pt3g_read_transport.NativeReadTransport, pt3g_read_source._ReadObjectProbe, pt3g_version._MetadataProbe)

    def test_metadata_calls_work_before_close_and_never_reach_DLL_after_close(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = native(cls)
                query(value)
                report = value.close()
                self.assertEqual(report['errors'], [])
                calls = list(value.dll.calls)
                with self.assertRaisesRegex(ElmError, '^pt3g-adapter-closed$'): query(value)
                self.assertEqual(value.dll.calls, calls)
                self.assertEqual(value.close(), report)
                self.assertEqual(value.dll.calls, calls)
                self.assertEqual(value._directory.closed, 1)

    def test_failed_destruct_stays_closed_and_preserves_first_cleanup_report(self):
        for cls in self.classes:
            with self.subTest(cls=cls.__name__):
                value = native(cls, 0x60)
                report = value.close()
                self.assertTrue(report['errors'])
                calls = list(value.dll.calls)
                with self.assertRaisesRegex(ElmError, '^pt3g-adapter-closed$'): query(value)
                copy = value.close()
                self.assertEqual(copy, report)
                copy['errors'].clear()
                self.assertEqual(value.close(), report)
                self.assertEqual(value.dll.calls, calls)
                self.assertEqual(value._directory.closed, 1)

    def test_closed_state_precedes_all_dispatch_and_argument_validation(self):
        for cls in self.classes:
            value = native(cls)
            value.close()
            calls = list(value.dll.calls)
            for name, args in [('PDUConstruct', (None, None)), ('PDUDestruct', ()),
                    ('PDUGetObjectId', ()), ('PDUGetVersion', ()), ('PDUGetModuleIds', ()),
                    ('PDUModuleConnect', (1,)), ('PDUStartComPrimitive', ()), ('UNKNOWN', ())]:
                with self.subTest(cls=cls.__name__, api=name), self.assertRaisesRegex(ElmError, '^pt3g-adapter-closed$'):
                    value.call(name, *args)
            self.assertEqual(value.dll.calls, calls)

    def test_base_dispatch_guard_supports_shared_read_transport_without_inheritance(self):
        self.assertFalse(issubclass(pt3g_read_transport.NativeReadTransport, pt3g.NativeAdapter))
        value = native(pt3g_read_transport.NativeReadTransport)
        pt3g.NativeAdapter.call(value, 'PDUDestruct')
        self.assertEqual(value.dll.calls, ['PDUDestruct'])
        value.constructed = False
        self.assertEqual(value.close()['errors'], [])


if __name__ == '__main__': unittest.main()
